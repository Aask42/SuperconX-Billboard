/* epd_seq.c — e-paper update sequence (port of epd.py global_update), driven
 * through epd_seq_hal_t. No ESP-IDF dependencies: see epd_seq.h. */
#include "epd_seq.h"

#include <string.h>

#define EPD_SEQ_MAX_HALF 40320   /* 960/2/8 * 672 — one colour half-plane */

static const epd_seq_hal_t *g_hal;
static uint8_t s_scratch[EPD_SEQ_MAX_HALF];

void epd_seq_set_hal(const epd_seq_hal_t *hal) { g_hal = hal; }

/* reverse_byte() port (epd.py 70-74). */
static uint8_t reverse_byte(uint8_t b)
{
    b = (uint8_t)(((b & 0xF0) >> 4) | ((b & 0x0F) << 4));
    b = (uint8_t)(((b & 0xCC) >> 2) | ((b & 0x33) << 2));
    b = (uint8_t)(((b & 0xAA) >> 1) | ((b & 0x55) << 1));
    return b;
}

static void cmd(epd_target_t t, uint8_t index, const uint8_t *data, size_t len)
{
    g_hal->cmd(t, index, data, len);
}

/* An image plane, optionally bit-reversed through the scratch buffer first. */
static void cmd_image(epd_target_t t, uint8_t index, const uint8_t *plane,
                      size_t len, int reverse_bits)
{
    if (reverse_bits && len <= EPD_SEQ_MAX_HALF) {
        for (size_t i = 0; i < len; i++) s_scratch[i] = reverse_byte(plane[i]);
        g_hal->cmd(t, index, s_scratch, len);
    } else {
        g_hal->cmd(t, index, plane, len);
    }
}

/* _reset (epd.py 378-389). */
static void reset(void)
{
    g_hal->delay_ms(200);
    g_hal->rst(1);
    g_hal->delay_ms(20);
    g_hal->rst(0);
    g_hal->delay_ms(200);
    g_hal->rst(1);
    g_hal->delay_ms(200);
    g_hal->cs(1, 1);
    g_hal->cs(2, 1);
    g_hal->delay_ms(5);
}

/* _send_images (epd.py 391-421). */
static void send_images(const uint8_t *bl, const uint8_t *rl,
                        const uint8_t *br, const uint8_t *rr,
                        size_t half, int reverse_bits)
{
    static const uint8_t duw[]  = {0x00, 0x3B, 0x00, 0x00, 0x9F, 0x02};
    static const uint8_t drfw[] = {0x00, 0x3B, 0x00, 0xA9};
    static const uint8_t ram[]  = {0x3B, 0x00, 0x14};
    cmd(EPD_T_BOTH, 0x13, duw, sizeof duw);
    cmd(EPD_T_BOTH, 0x90, drfw, sizeof drfw);
    cmd(EPD_T_LEFT, 0x12, ram, sizeof ram);
    cmd_image(EPD_T_LEFT, 0x10, bl, half, reverse_bits);
    cmd(EPD_T_LEFT, 0x12, ram, sizeof ram);
    cmd_image(EPD_T_LEFT, 0x11, rl, half, reverse_bits);
    cmd(EPD_T_RIGHT, 0x12, ram, sizeof ram);
    cmd_image(EPD_T_RIGHT, 0x10, br, half, reverse_bits);
    cmd(EPD_T_RIGHT, 0x12, ram, sizeof ram);
    cmd_image(EPD_T_RIGHT, 0x11, rr, half, reverse_bits);
}

/* cog_initial (epd.py 278-321). */
static void cog_initial(void)
{
    uint8_t d; uint8_t d2[2];
    d = 0x7D; cmd(EPD_T_BOTH, 0x05, &d, 1); g_hal->delay_ms(200);
    d = 0x00; cmd(EPD_T_BOTH, 0x05, &d, 1); g_hal->delay_ms(10);
    d = 0x80; cmd(EPD_T_BOTH, 0xD8, &d, 1);
    d = 0x00; cmd(EPD_T_BOTH, 0xD6, &d, 1);
    d = 0x10; cmd(EPD_T_BOTH, 0xA7, &d, 1); g_hal->delay_ms(100);
    d = 0x00; cmd(EPD_T_BOTH, 0xA7, &d, 1); g_hal->delay_ms(100);
    d2[0] = 0x00; d2[1] = 0x11; cmd(EPD_T_BOTH, 0x03, d2, 2);

    const epd_target_t halves[2] = {EPD_T_LEFT, EPD_T_RIGHT};
    for (int i = 0; i < 2; i++) {
        epd_target_t t = halves[i];
        d = 0x00; cmd(t, 0x44, &d, 1);
        d = 0x80; cmd(t, 0x45, &d, 1);
        d = 0x10; cmd(t, 0xA7, &d, 1); g_hal->delay_ms(100);
        d = 0x00; cmd(t, 0xA7, &d, 1); g_hal->delay_ms(100);
        d = 0x06; cmd(t, 0x44, &d, 1);
        d = 0x82; cmd(t, 0x45, &d, 1);
        d = 0x10; cmd(t, 0xA7, &d, 1); g_hal->delay_ms(100);
        d = 0x00; cmd(t, 0xA7, &d, 1); g_hal->delay_ms(100);
    }

    d = 0x25; cmd(EPD_T_BOTH, 0x60, &d, 1);
    d = 0x01; cmd(EPD_T_LEFT, 0x61, &d, 1);
    d = 0x00; cmd(EPD_T_BOTH, 0x02, &d, 1);
}

/* _dcdc_soft_start (epd.py 424-456). */
static void dcdc_soft_start(void)
{
    uint8_t d; uint8_t d2[2];
    d2[0] = 0x50; d2[1] = 0x01; cmd(EPD_T_BOTH, 0x51, d2, 2);
    for (int v = 1; v < 5; v++) {
        d = 0x1F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        d2[0] = 0x50; d2[1] = (uint8_t)v; cmd(EPD_T_BOTH, 0x51, d2, 2);
        d = 0x9F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        g_hal->delay_ms(2);
    }
    for (int v = 1; v < 11; v++) {
        d = 0x1F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        d2[0] = 0x0A; d2[1] = (uint8_t)v; cmd(EPD_T_BOTH, 0x51, d2, 2);
        d = 0x9F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        g_hal->delay_ms(2);
    }
    for (int v = 3; v < 11; v++) {
        d = 0x7F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        d2[0] = 0x0A; d2[1] = (uint8_t)v; cmd(EPD_T_BOTH, 0x51, d2, 2);
        d = 0xFF; cmd(EPD_T_BOTH, 0x09, &d, 1);
        g_hal->delay_ms(2);
    }
    for (int v = 9; v > 1; v--) {
        d = 0x7F; cmd(EPD_T_BOTH, 0x09, &d, 1);
        d2[0] = (uint8_t)v; d2[1] = 0x0A; cmd(EPD_T_BOTH, 0x51, d2, 2);
        d = 0xFF; cmd(EPD_T_BOTH, 0x09, &d, 1);
        g_hal->delay_ms(2);
    }
    d = 0xFF; cmd(EPD_T_BOTH, 0x09, &d, 1);
    g_hal->delay_ms(10);
}

/* _display_refresh (epd.py 372-376). */
static void display_refresh(void)
{
    g_hal->wait_busy();
    uint8_t d = 0x3C; cmd(EPD_T_BOTH, 0x15, &d, 1);
    g_hal->delay_ms(5);
}

/* _dcdc_soft_shutdown (epd.py 458-474). */
static void dcdc_soft_shutdown(void)
{
    uint8_t d;
    g_hal->wait_busy();
    d = 0x7F; cmd(EPD_T_BOTH, 0x09, &d, 1);
    d = 0x7D; cmd(EPD_T_BOTH, 0x05, &d, 1);
    d = 0x00; cmd(EPD_T_BOTH, 0x09, &d, 1);
    g_hal->delay_ms(200);
    g_hal->wait_busy();
    g_hal->dc(0);
    g_hal->cs(1, 0);
    g_hal->rst(0);
    g_hal->cs(1, 1);
}

void epd_seq_refresh(const uint8_t *bl, const uint8_t *rl,
                     const uint8_t *br, const uint8_t *rr,
                     size_t half, int reverse_bits)
{
    reset();
    send_images(bl, rl, br, rr, half, reverse_bits);
    cog_initial();
    dcdc_soft_start();
    display_refresh();
    dcdc_soft_shutdown();
}
