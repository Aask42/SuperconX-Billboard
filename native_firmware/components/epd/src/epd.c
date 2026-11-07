/*
 * epd.c — E-paper driver implementation.
 *
 * Native ESP-IDF C port of badge_fs/lib/epd.py (task T21). Drives the
 * Pervasive Displays 9.69" 960x672 three-colour panel as two halves (left CoG
 * via CS1, right CoG via CS2). Global refresh only.
 *
 * The two framebuffers are kept as full 960x672 1-bpp MONO_HLSB planes (MSB =
 * leftmost pixel), 120 bytes/row, 80640 bytes/plane, in PSRAM. On refresh each
 * plane row is split into a left 60-byte half and a right 60-byte half (exactly
 * as the Python run() method does) before being shipped to the matching CoG.
 *
 * SPI: hardware SPI master on SPI2_HOST, TX-only, 8 MHz, NO hardware CS. CS1,
 * CS2 and DC are driven manually as GPIOs to mirror the Python
 * _send_index_data / _send_index_data_left / _send_index_data_right routines
 * byte-for-byte (DC low for the 1-byte index with the relevant CS low, then DC
 * high + CS low for the data bytes). That CS/DC choreography is the subtle part
 * and is reproduced precisely below.
 */
#include "epd.h"

#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"
#include "esp_log.h"
#include "esp_heap_caps.h"

#include "board.h"

static const char *TAG = "epd";

/* half_frame_one_color_size in epd.py = height * width / (8*2) = 40320. */
#define EPD_HALF_PLANE_BYTES (EPD_PLANE_BYTES / 2)   /* 40320 */

/* Max bytes per physical SPI transfer; large image sends are chunked through a
 * DMA-capable bounce buffer of this size while CS is held low. */
#define EPD_SPI_CHUNK 4096

/* Full MONO_HLSB planes (as in the stub / epd.h contract). */
static uint8_t *s_black;   /* EPD_PLANE_BYTES */
static uint8_t *s_red;     /* EPD_PLANE_BYTES */

static bool s_inited;

/* Refresh counter; logged as "EMU_EPD_REFRESH <n>" for the QEMU harness. */
static unsigned s_refresh_count;

#if CONFIG_BADGE_EMULATOR
/* Exposed to the emulator: emulator/emu.py dumps these via gdb (read_planes).
 * They point at the same MONO_HLSB planes the firmware draws into. */
uint8_t *badge_emu_fb_black = NULL;
uint8_t *badge_emu_fb_red = NULL;
#endif

#if !CONFIG_BADGE_EMULATOR
/* ===== hardware-only state + helpers (no GPIO/SPI peripheral under QEMU) ===== */

/* Per-refresh left/right split half-planes (PSRAM). */
static uint8_t *s_black_left;
static uint8_t *s_black_right;
static uint8_t *s_red_left;
static uint8_t *s_red_right;

/* Internal, DMA-capable bounce buffer for SPI (PSRAM is not reliably DMA-safe
 * as a TX source, so copy each chunk here first). */
static uint8_t *s_dma_buf;

static spi_device_handle_t s_spi;

/*
 * Bit-reversal. The stock driver defaults _reverse_fb_bits = True; only
 * display_pn == b"84" disables it (epd.py lines 96-101). We have no hwconf
 * store here, so default reversal ON to match the common case.
 */
static const bool s_reverse_fb_bits = true;

/* reverse_byte() port (epd.py lines 70-74). */
static inline uint8_t reverse_byte(uint8_t b)
{
    b = (uint8_t)(((b & 0xF0) >> 4) | ((b & 0x0F) << 4));
    b = (uint8_t)(((b & 0xCC) >> 2) | ((b & 0x33) << 2));
    b = (uint8_t)(((b & 0xAA) >> 1) | ((b & 0x55) << 1));
    return b;
}

static inline void delay_ms(uint32_t ms)
{
    vTaskDelay(pdMS_TO_TICKS(ms));
}

/* Manual GPIO control of the select / data-command lines (idle high). */
static inline void dc(bool level)  { gpio_set_level(EPD_PIN_DC,  level); }
static inline void cs1(bool level) { gpio_set_level(EPD_PIN_CS1, level); }
static inline void cs2(bool level) { gpio_set_level(EPD_PIN_CS2, level); }

/* One blocking SPI write. CS is managed by the caller; this only shifts bytes.
 * Large buffers are chunked through the internal DMA bounce buffer so the DMA
 * engine never sources directly from PSRAM. Mirrors spi.write(). */
static void spi_write(const uint8_t *data, size_t len)
{
    size_t off = 0;
    while (off < len) {
        size_t n = len - off;
        if (n > EPD_SPI_CHUNK) {
            n = EPD_SPI_CHUNK;
        }
        memcpy(s_dma_buf, data + off, n);
        spi_transaction_t t = {
            .length = n * 8,     /* in bits */
            .tx_buffer = s_dma_buf,
        };
        ESP_ERROR_CHECK(spi_device_polling_transmit(s_spi, &t));
        off += n;
    }
}

/*
 * _send_index_data (epd.py 336-349): both CoGs selected.
 * DC low + CS1/CS2 low for the index byte, then DC high + CS1/CS2 low for data.
 */
static void send_index_data(uint8_t index, const uint8_t *data, size_t len)
{
    dc(false); cs1(false); cs2(false);
    spi_write(&index, 1);
    cs1(true); cs2(true);
    dc(true); cs1(false); cs2(false);
    spi_write(data, len);
    cs1(true); cs2(true);
}

/* _send_index_data_left (epd.py 350-359): CS1 only, CS2 stays high. */
static void send_index_data_left(uint8_t index, const uint8_t *data, size_t len)
{
    dc(false); cs1(false); cs2(true);
    spi_write(&index, 1);
    cs1(true);
    dc(true); cs1(false);
    spi_write(data, len);
    cs1(true);
}

/* _send_index_data_right (epd.py 361-370): CS2 only, CS1 stays high. */
static void send_index_data_right(uint8_t index, const uint8_t *data, size_t len)
{
    dc(false); cs1(true); cs2(false);
    spi_write(&index, 1);
    cs2(true);
    dc(true); cs2(false);
    spi_write(data, len);
    cs2(true);
}

/* Wait until both halves report not-busy. Python idiom:
 *   while not BUSY1 or not BUSY2: sleep  (i.e. loop while either is low). */
static void wait_busy(uint32_t poll_ms)
{
    while (gpio_get_level(EPD_PIN_BUSY1) == 0 ||
           gpio_get_level(EPD_PIN_BUSY2) == 0) {
        delay_ms(poll_ms);
    }
}

/* _reset (epd.py 378-389): CoG driver power-on hard reset. */
static void reset(void)
{
    delay_ms(200);
    gpio_set_level(EPD_PIN_RST, 1);
    delay_ms(20);
    gpio_set_level(EPD_PIN_RST, 0);
    delay_ms(200);
    gpio_set_level(EPD_PIN_RST, 1);
    delay_ms(200);
    cs1(true); cs2(true);
    delay_ms(5);
}

/* _send_images (epd.py 391-421). Half-planes are reversed in place first (when
 * enabled) to match the Python per-byte reverse_byte() pass. */
static void send_images(void)
{
    if (s_reverse_fb_bits) {
        for (size_t i = 0; i < EPD_HALF_PLANE_BYTES; i++) {
            s_black_left[i]  = reverse_byte(s_black_left[i]);
            s_red_left[i]    = reverse_byte(s_red_left[i]);
            s_black_right[i] = reverse_byte(s_black_right[i]);
            s_red_right[i]   = reverse_byte(s_red_right[i]);
        }
    }

    /* DUW for both halves. */
    static const uint8_t duw[]  = {0x00, 0x3B, 0x00, 0x00, 0x9F, 0x02};
    send_index_data(0x13, duw, sizeof duw);
    /* DRFW for both halves. */
    static const uint8_t drfw[] = {0x00, 0x3B, 0x00, 0xA9};
    send_index_data(0x90, drfw, sizeof drfw);

    static const uint8_t ram_rw[] = {0x3B, 0x00, 0x14};

    send_index_data_left(0x12, ram_rw, sizeof ram_rw);              /* RAM_RW left  */
    send_index_data_left(0x10, s_black_left,  EPD_HALF_PLANE_BYTES); /* black left  */
    send_index_data_left(0x12, ram_rw, sizeof ram_rw);
    send_index_data_left(0x11, s_red_left,    EPD_HALF_PLANE_BYTES); /* red left    */

    send_index_data_right(0x12, ram_rw, sizeof ram_rw);             /* RAM_RW right */
    send_index_data_right(0x10, s_black_right, EPD_HALF_PLANE_BYTES);/* black right */
    send_index_data_right(0x12, ram_rw, sizeof ram_rw);
    send_index_data_right(0x11, s_red_right,   EPD_HALF_PLANE_BYTES);/* red right   */
}

/* cog_initial (epd.py 278-321). */
static void cog_initial(void)
{
    uint8_t d;

    d = 0x7D; send_index_data(0x05, &d, 1);
    delay_ms(200);
    d = 0x00; send_index_data(0x05, &d, 1);
    delay_ms(10);
    d = 0x80; send_index_data(0xD8, &d, 1);   /* MS_SYNC */
    d = 0x00; send_index_data(0xD6, &d, 1);   /* BVSS */
    d = 0x10; send_index_data(0xA7, &d, 1);
    delay_ms(100);
    d = 0x00; send_index_data(0xA7, &d, 1);
    delay_ms(100);
    static const uint8_t osc[] = {0x00, 0x11};
    send_index_data(0x03, osc, sizeof osc);   /* OSC */

    /* Display 1 (left) */
    d = 0x00; send_index_data_left(0x44, &d, 1);
    d = 0x80; send_index_data_left(0x45, &d, 1);
    d = 0x10; send_index_data_left(0xA7, &d, 1);
    delay_ms(100);
    d = 0x00; send_index_data_left(0xA7, &d, 1);
    delay_ms(100);
    d = 0x06; send_index_data_left(0x44, &d, 1);
    d = 0x82; send_index_data_left(0x45, &d, 1);  /* Temperature 0x82@25C */
    d = 0x10; send_index_data_left(0xA7, &d, 1);
    delay_ms(100);
    d = 0x00; send_index_data_left(0xA7, &d, 1);
    delay_ms(100);

    /* Display 2 (right) */
    d = 0x00; send_index_data_right(0x44, &d, 1);
    d = 0x80; send_index_data_right(0x45, &d, 1);
    d = 0x10; send_index_data_right(0xA7, &d, 1);
    delay_ms(100);
    d = 0x00; send_index_data_right(0xA7, &d, 1);
    delay_ms(100);
    d = 0x06; send_index_data_right(0x44, &d, 1);
    d = 0x82; send_index_data_right(0x45, &d, 1);  /* Temperature 0x82@25C */
    d = 0x10; send_index_data_right(0xA7, &d, 1);
    delay_ms(100);
    d = 0x00; send_index_data_right(0xA7, &d, 1);
    delay_ms(100);

    d = 0x25; send_index_data(0x60, &d, 1);        /* TCON */
    d = 0x01; send_index_data_left(0x61, &d, 1);   /* STV_DIR for Display 1 */
    d = 0x00; send_index_data(0x02, &d, 1);        /* VCOM */
}

/* _dcdc_soft_start (epd.py 424-456). */
static void dcdc_soft_start(void)
{
    uint8_t d2[2];

    d2[0] = 0x50; d2[1] = 0x01;
    send_index_data(0x51, d2, 2);

    for (int val = 1; val < 5; val++) {
        uint8_t d = 0x1F; send_index_data(0x09, &d, 1);
        d2[0] = 0x50; d2[1] = (uint8_t)val; send_index_data(0x51, d2, 2);
        d = 0x9F; send_index_data(0x09, &d, 1);
        delay_ms(2);
    }
    for (int val = 1; val < 11; val++) {
        uint8_t d = 0x1F; send_index_data(0x09, &d, 1);
        d2[0] = 0x0A; d2[1] = (uint8_t)val; send_index_data(0x51, d2, 2);
        d = 0x9F; send_index_data(0x09, &d, 1);
        delay_ms(2);
    }
    for (int val = 3; val < 11; val++) {
        uint8_t d = 0x7F; send_index_data(0x09, &d, 1);
        d2[0] = 0x0A; d2[1] = (uint8_t)val; send_index_data(0x51, d2, 2);
        d = 0xFF; send_index_data(0x09, &d, 1);
        delay_ms(2);
    }
    for (int val = 9; val > 1; val--) {
        uint8_t d = 0x7F; send_index_data(0x09, &d, 1);
        d2[0] = (uint8_t)val; d2[1] = 0x0A; send_index_data(0x51, d2, 2);
        d = 0xFF; send_index_data(0x09, &d, 1);
        delay_ms(2);
    }

    uint8_t d = 0xFF; send_index_data(0x09, &d, 1);
    delay_ms(10);
}

/* _display_refresh (epd.py 372-376). */
static void display_refresh(void)
{
    wait_busy(100);
    uint8_t d = 0x3C; send_index_data(0x15, &d, 1);
    delay_ms(5);
}

/* _dcdc_soft_shutdown (epd.py 458-474). */
static void dcdc_soft_shutdown(void)
{
    wait_busy(100);
    uint8_t d;
    d = 0x7F; send_index_data(0x09, &d, 1);
    d = 0x7D; send_index_data(0x05, &d, 1);
    d = 0x00; send_index_data(0x09, &d, 1);
    delay_ms(200);
    wait_busy(100);
    dc(false);
    cs1(false);
    gpio_set_level(EPD_PIN_RST, 0);
    cs1(true);
}

/* Split both full planes into left/right half-planes, exactly as the Python
 * run() method slices each row (epd.py 218-246). */
static void split_planes(void)
{
    for (int row = 0; row < EPD_HEIGHT; row++) {
        const uint8_t *bk = s_black + (size_t)row * EPD_STRIDE_BYTES;
        const uint8_t *rd = s_red   + (size_t)row * EPD_STRIDE_BYTES;
        uint8_t *bl = s_black_left  + (size_t)row * EPD_HALF_STRIDE;
        uint8_t *br = s_black_right + (size_t)row * EPD_HALF_STRIDE;
        uint8_t *rl = s_red_left    + (size_t)row * EPD_HALF_STRIDE;
        uint8_t *rr = s_red_right   + (size_t)row * EPD_HALF_STRIDE;
        memcpy(bl, bk,                   EPD_HALF_STRIDE);
        memcpy(br, bk + EPD_HALF_STRIDE, EPD_HALF_STRIDE);
        memcpy(rl, rd,                   EPD_HALF_STRIDE);
        memcpy(rr, rd + EPD_HALF_STRIDE, EPD_HALF_STRIDE);
    }
}
#endif /* !CONFIG_BADGE_EMULATOR */

esp_err_t epd_init(void)
{
    if (s_inited) {
        return ESP_OK;
    }

    /* Colour planes live in both builds; the firmware draws into them. */
    if (!s_black) s_black = heap_caps_calloc(1, EPD_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_red)   s_red   = heap_caps_calloc(1, EPD_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_black || !s_red) {
        ESP_LOGE(TAG, "plane alloc failed");
        return ESP_ERR_NO_MEM;
    }

#if CONFIG_BADGE_EMULATOR
    badge_emu_fb_black = s_black;
    badge_emu_fb_red = s_red;
    s_inited = true;
    ESP_LOGI(TAG, "epd_init: emulator backend (planes exposed as badge_emu_fb_black/red)");
    return ESP_OK;
#else
    /* --- split/bounce buffers --- */
    if (!s_black_left)  s_black_left  = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_black_right) s_black_right = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_red_left)    s_red_left    = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_red_right)   s_red_right   = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    /* DMA bounce buffer must live in internal, DMA-capable RAM. */
    if (!s_dma_buf)     s_dma_buf     = heap_caps_malloc(EPD_SPI_CHUNK, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);

    if (!s_black || !s_red || !s_black_left || !s_black_right ||
        !s_red_left || !s_red_right || !s_dma_buf) {
        ESP_LOGE(TAG, "buffer alloc failed");
        return ESP_ERR_NO_MEM;
    }

    /* --- GPIO ---
     * CS1, CS2, DC, RST: outputs idle high. BUSY1, BUSY2: inputs. PWR_EN:
     * output. See epd.py __init__ (DC/RST/CS1/CS2 set high at 83-86). */
    gpio_config_t out = {
        .pin_bit_mask = (1ULL << EPD_PIN_CS1) | (1ULL << EPD_PIN_CS2) |
                        (1ULL << EPD_PIN_DC)  | (1ULL << EPD_PIN_RST) |
                        (1ULL << EPD_PIN_PWR_EN),
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&out));

    gpio_config_t in = {
        .pin_bit_mask = (1ULL << EPD_PIN_BUSY1) | (1ULL << EPD_PIN_BUSY2),
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&in));

    gpio_set_level(EPD_PIN_DC, 1);
    gpio_set_level(EPD_PIN_RST, 1);
    gpio_set_level(EPD_PIN_CS1, 1);
    gpio_set_level(EPD_PIN_CS2, 1);
    /* ASSUMPTION: the stock Python references EPD_PWR_EN (GPIO48) but never
     * toggles it. We drive it HIGH to enable panel power. If the rail is
     * active-low or already hardwired on, this may need revisiting. */
    gpio_set_level(EPD_PIN_PWR_EN, 1);

    /* --- Hardware SPI (SPI2_HOST), TX-only, 8 MHz, no hardware CS --- */
    spi_bus_config_t buscfg = {
        .mosi_io_num = EPD_PIN_MOSI,
        .miso_io_num = -1,               /* MISO unused (not connected) */
        .sclk_io_num = EPD_PIN_SCK,
        .quadwp_io_num = -1,
        .quadhd_io_num = -1,
        .max_transfer_sz = EPD_SPI_CHUNK,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));

    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = EPD_SPI_HZ,    /* 8 MHz */
        .mode = 0,                       /* CPOL=0, CPHA=0 */
        .spics_io_num = -1,              /* CS driven manually as GPIO */
        .queue_size = 1,
    };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &devcfg, &s_spi));

    s_inited = true;
    ESP_LOGI(TAG, "epd_init: GPIO + SPI2 ready (%d Hz)", EPD_SPI_HZ);
    return ESP_OK;
#endif /* CONFIG_BADGE_EMULATOR */
}

uint8_t *epd_black_plane(void) { return s_black; }
uint8_t *epd_red_plane(void)   { return s_red; }

void epd_clear(void)
{
    if (s_black) memset(s_black, 0, EPD_PLANE_BYTES);
    if (s_red)   memset(s_red,   0, EPD_PLANE_BYTES);
}

/*
 * epd_refresh = the Python global_update pipeline (epd.py 253-275), preceded by
 * the row-split that run() performs (218-246). Synchronous / blocking.
 */
esp_err_t epd_refresh(void)
{
    if (!s_inited) {
        ESP_LOGE(TAG, "epd_refresh before epd_init");
        return ESP_ERR_INVALID_STATE;
    }

#if !CONFIG_BADGE_EMULATOR
    split_planes();

    reset();
    send_images();
    cog_initial();
    dcdc_soft_start();
    display_refresh();
    dcdc_soft_shutdown();
#endif

    /* The emulator harness watches this line; the planes are already current. */
    s_refresh_count++;
    ESP_LOGI(TAG, "EMU_EPD_REFRESH %u", s_refresh_count);
    return ESP_OK;
}
