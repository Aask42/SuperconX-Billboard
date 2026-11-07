/*
 * epd.c — E-paper driver: framebuffers, init, and (on the target) the GPIO/SPI
 * HAL that backs the update sequence in epd_seq.c.
 *
 * The command *sequence* (CoG init, DC-DC ramp, image upload, refresh, shutdown)
 * lives in epd_seq.c so it can be golden-tested on the host (issue #4). Here we
 * keep the hardware: the two MONO_HLSB planes, the left/right split, and the
 * HAL that does the exact CS/DC choreography (ported from epd.py
 * _send_index_data / _left / _right) over hardware SPI2.
 */
#include "epd.h"
#include "epd_seq.h"

#include <string.h>

#include "esp_log.h"
#include "esp_heap_caps.h"

#include "board.h"

static const char *TAG = "epd";

/* Full MONO_HLSB planes; the firmware draws into these. */
static uint8_t *s_black;
static uint8_t *s_red;
static bool s_inited;

/* Refresh counter; logged as "EMU_EPD_REFRESH <n>" for the QEMU harness. */
static unsigned s_refresh_count;

#if CONFIG_BADGE_EMULATOR
/* Exposed to the emulator: emulator/emu.py dumps these via gdb (read_planes). */
uint8_t *badge_emu_fb_black = NULL;
uint8_t *badge_emu_fb_red = NULL;
#endif

#if !CONFIG_BADGE_EMULATOR
/* ===== hardware-only: GPIO/SPI HAL + split buffers (absent under QEMU) ===== */
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "driver/gpio.h"
#include "driver/spi_master.h"

#define EPD_HALF_PLANE_BYTES (EPD_PLANE_BYTES / 2)   /* 40320 */
#define EPD_SPI_CHUNK 4096

static uint8_t *s_black_left, *s_black_right, *s_red_left, *s_red_right;
static uint8_t *s_dma_buf;      /* DMA-capable bounce buffer (internal RAM) */
static spi_device_handle_t s_spi;
/* Stock defaults _reverse_fb_bits = True (epd.py 96-101). */
static const bool s_reverse_fb_bits = true;

static inline void pin_dc(bool level)  { gpio_set_level(EPD_PIN_DC,  level); }
static inline void pin_cs1(bool level) { gpio_set_level(EPD_PIN_CS1, level); }
static inline void pin_cs2(bool level) { gpio_set_level(EPD_PIN_CS2, level); }

/* Chunked blocking SPI write through the internal DMA bounce buffer. */
static void spi_write(const uint8_t *data, size_t len)
{
    size_t off = 0;
    while (off < len) {
        size_t n = len - off > EPD_SPI_CHUNK ? EPD_SPI_CHUNK : len - off;
        memcpy(s_dma_buf, data + off, n);
        spi_transaction_t t = { .length = n * 8, .tx_buffer = s_dma_buf };
        ESP_ERROR_CHECK(spi_device_polling_transmit(s_spi, &t));
        off += n;
    }
}

/* HAL: one command (index byte with DC low, then data with DC high), with the
 * per-target CS choreography from epd.py _send_index_data / _left / _right. */
static void hal_cmd(epd_target_t target, uint8_t index, const uint8_t *data, size_t len)
{
    bool both = (target == EPD_T_BOTH);
    bool left = (target != EPD_T_RIGHT);
    bool right = (target != EPD_T_LEFT);

    pin_dc(false);
    if (left)  pin_cs1(false);
    if (right) pin_cs2(false);
    spi_write(&index, 1);
    if (left)  pin_cs1(true);
    if (right) pin_cs2(true);

    pin_dc(true);
    if (left)  pin_cs1(false);
    if (right) pin_cs2(false);
    if (len) spi_write(data, len);
    if (left)  pin_cs1(true);
    if (right) pin_cs2(true);
    (void)both;
}

static void hal_dc(int level) { pin_dc(level); }
static void hal_cs(int line, int level) { if (line == 1) pin_cs1(level); else pin_cs2(level); }
static void hal_rst(int level) { gpio_set_level(EPD_PIN_RST, level); }
static void hal_pwr(int on) { gpio_set_level(EPD_PIN_PWR_EN, on); }
static void hal_delay_ms(unsigned ms) { vTaskDelay(pdMS_TO_TICKS(ms)); }
static void hal_wait_busy(void)
{
    while (gpio_get_level(EPD_PIN_BUSY1) == 0 || gpio_get_level(EPD_PIN_BUSY2) == 0) {
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

static const epd_seq_hal_t s_hal = {
    .cmd = hal_cmd, .dc = hal_dc, .cs = hal_cs, .rst = hal_rst,
    .pwr = hal_pwr, .delay_ms = hal_delay_ms, .wait_busy = hal_wait_busy,
};

/* Split both full planes into left/right half-planes (epd.py run() 218-246). */
static void split_planes(void)
{
    for (int row = 0; row < EPD_HEIGHT; row++) {
        const uint8_t *bk = s_black + (size_t)row * EPD_STRIDE_BYTES;
        const uint8_t *rd = s_red   + (size_t)row * EPD_STRIDE_BYTES;
        memcpy(s_black_left  + (size_t)row * EPD_HALF_STRIDE, bk,                   EPD_HALF_STRIDE);
        memcpy(s_black_right + (size_t)row * EPD_HALF_STRIDE, bk + EPD_HALF_STRIDE, EPD_HALF_STRIDE);
        memcpy(s_red_left    + (size_t)row * EPD_HALF_STRIDE, rd,                   EPD_HALF_STRIDE);
        memcpy(s_red_right   + (size_t)row * EPD_HALF_STRIDE, rd + EPD_HALF_STRIDE, EPD_HALF_STRIDE);
    }
}
#endif /* !CONFIG_BADGE_EMULATOR */

esp_err_t epd_init(void)
{
    if (s_inited) return ESP_OK;

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
    if (!s_black_left)  s_black_left  = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_black_right) s_black_right = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_red_left)    s_red_left    = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_red_right)   s_red_right   = heap_caps_malloc(EPD_HALF_PLANE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!s_dma_buf)     s_dma_buf     = heap_caps_malloc(EPD_SPI_CHUNK, MALLOC_CAP_DMA | MALLOC_CAP_INTERNAL);
    if (!s_black_left || !s_black_right || !s_red_left || !s_red_right || !s_dma_buf) {
        ESP_LOGE(TAG, "split/dma buffer alloc failed");
        return ESP_ERR_NO_MEM;
    }

    gpio_config_t out = {
        .pin_bit_mask = (1ULL << EPD_PIN_CS1) | (1ULL << EPD_PIN_CS2) |
                        (1ULL << EPD_PIN_DC)  | (1ULL << EPD_PIN_RST) | (1ULL << EPD_PIN_PWR_EN),
        .mode = GPIO_MODE_OUTPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&out));
    gpio_config_t in = {
        .pin_bit_mask = (1ULL << EPD_PIN_BUSY1) | (1ULL << EPD_PIN_BUSY2),
        .mode = GPIO_MODE_INPUT,
    };
    ESP_ERROR_CHECK(gpio_config(&in));
    pin_dc(true); pin_cs1(true); pin_cs2(true);
    gpio_set_level(EPD_PIN_RST, 1);
    gpio_set_level(EPD_PIN_PWR_EN, 1);   /* ASSUMPTION: drive panel power on (epd.py never toggles it) */

    spi_bus_config_t buscfg = {
        .mosi_io_num = EPD_PIN_MOSI, .miso_io_num = -1, .sclk_io_num = EPD_PIN_SCK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = EPD_SPI_CHUNK,
    };
    ESP_ERROR_CHECK(spi_bus_initialize(SPI2_HOST, &buscfg, SPI_DMA_CH_AUTO));
    spi_device_interface_config_t devcfg = {
        .clock_speed_hz = EPD_SPI_HZ, .mode = 0, .spics_io_num = -1, .queue_size = 1,
    };
    ESP_ERROR_CHECK(spi_bus_add_device(SPI2_HOST, &devcfg, &s_spi));

    epd_seq_set_hal(&s_hal);
    s_inited = true;
    ESP_LOGI(TAG, "epd_init: GPIO + SPI2 ready (%d Hz)", EPD_SPI_HZ);
    return ESP_OK;
#endif
}

uint8_t *epd_black_plane(void) { return s_black; }
uint8_t *epd_red_plane(void)   { return s_red; }

void epd_clear(void)
{
    if (s_black) memset(s_black, 0, EPD_PLANE_BYTES);
    if (s_red)   memset(s_red,   0, EPD_PLANE_BYTES);
}

esp_err_t epd_refresh(void)
{
    if (!s_inited) {
        ESP_LOGE(TAG, "epd_refresh before epd_init");
        return ESP_ERR_INVALID_STATE;
    }

#if !CONFIG_BADGE_EMULATOR
    split_planes();
    epd_seq_refresh(s_black_left, s_red_left, s_black_right, s_red_right,
                    EPD_HALF_PLANE_BYTES, s_reverse_fb_bits);
#endif

    s_refresh_count++;
    ESP_LOGI(TAG, "EMU_EPD_REFRESH %u", s_refresh_count);
    return ESP_OK;
}
