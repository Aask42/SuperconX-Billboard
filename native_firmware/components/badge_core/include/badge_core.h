/*
 * badge_core.h — pure, dependency-free badge logic.
 *
 * No ESP-IDF / FreeRTOS / hardware headers: this compiles on the target AND on
 * the host, so the same code the firmware runs is unit-tested directly
 * (tests/unit/test_c_core.py loads it via ctypes and checks it against the
 * reference models in tests/support/badge_spec.py). Keep it that way.
 */
#ifndef BADGE_CORE_H
#define BADGE_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ---- e-paper framebuffer (MONO_HLSB, 1 bpp per colour plane) ---- */
#define BADGE_EPD_W       960
#define BADGE_EPD_H       672
#define BADGE_EPD_STRIDE  (BADGE_EPD_W / 8)   /* 120 bytes per row */

typedef enum {
    BADGE_PX_WHITE = 0,
    BADGE_PX_BLACK = 1,
    BADGE_PX_RED   = 2,
} badge_px_t;

/* Classify an RGB565 pixel (r:5, g:6, b:5 already unpacked) into a panel colour,
 * matching epd.py _lv_flush. Returns a badge_px_t. */
int badge_classify_pixel(int r5, int g6, int b5);

/* Write one classified pixel into the black/red planes (stride = bytes per row).
 * RED sets red & clears black; BLACK the reverse; WHITE clears both. */
void badge_plane_put(uint8_t *black, uint8_t *red, int x, int y, int stride, int cls);

/* Classify a packed RGB565 value and write it straight into the planes. */
void badge_rgb565_put(uint8_t *black, uint8_t *red, int x, int y, int stride, uint16_t rgb565);

/* ---- image blit (Gallery app, issue #6) ---- */
/* Classify a tightly-packed RGBA8888 image (w*h*4 bytes, row-major, no stride
 * padding) into the colour planes with its top-left at (dst_x,dst_y), matching
 * badge_classify_pixel (RGB888 -> RGB565 hi-bits). Alpha is ignored — gallery
 * art is opaque. Pixels that fall outside the 960x672 panel are clipped. */
void badge_blit_rgba(uint8_t *black, uint8_t *red, int dst_x, int dst_y,
                     const uint8_t *rgba, int w, int h);

/* Top-left offset that centers a w x h image on the panel (>=0, clamped). */
void badge_center_offset(int w, int h, int *out_x, int *out_y);

/* ---- text rendering (vendored 8x8 public-domain font, basic latin) ---- */
/* Draw one ASCII glyph with its top-left at pixel (x,y), in colour class `cls`
 * (BADGE_PX_BLACK/RED/WHITE). `scale` (>=1) enlarges each glyph dot to
 * scale*scale px. Dots outside the 960x672 panel are clipped. A code point
 * outside 0..127 draws nothing. Bit order matches the font: LSB = leftmost. */
void badge_draw_char(uint8_t *black, uint8_t *red, int x, int y, char c, int cls, int scale);

/* Draw a NUL-terminated string from (x,y) left to right. Each glyph advances
 * (8+1)*scale px; '\n' returns to the start column and drops one line
 * ((8+1)*scale px). Returns the x pixel just past the last glyph on the final
 * line (useful for appending). Clips to the panel. */
int badge_draw_text(uint8_t *black, uint8_t *red, int x, int y, const char *s, int cls, int scale);

/* Pixel width the widest line of `s` occupies at `scale` (for centering).
 * Matches badge_draw_text advance: (9*chars - 1)*scale, max over lines, >=0. */
int badge_text_width(const char *s, int scale);

/* ---- LED menu bar (ported from led_bar.py menu_display) ---- */
/* Fill out[8][3] (r,g,b, 0..3 range) for the given selection cursor. */
void badge_menu_leds(int app_index, int total_apps, uint8_t out[8][3]);

/* Submenu LED (led_bar.py submenu_display): a single lit LED per page. */
void badge_submenu_leds(int item_index, uint8_t out[8][3]);

/* ---- app switcher navigation (app_switcher.py) ---- */
enum {
    BADGE_BTN_MENU_MINUS = 0,
    BADGE_BTN_MENU_PLUS  = 1,
    BADGE_BTN_A          = 2,
    BADGE_BTN_B          = 3,
};

typedef struct {
    int count;      /* number of apps */
    int selecting;  /* 1 = menu/selection mode, 0 = an app is running */
    int selection;  /* cursor index while selecting */
    int active;     /* launched app index */
} badge_switcher_t;

void badge_switcher_init(badge_switcher_t *s, int count, int initial);
/* Apply a button event (BADGE_BTN_*). Returns 1 iff an app was just launched. */
int badge_switcher_event(badge_switcher_t *s, int button);

/* ---- schedule.csv parsing (Schedule / Workshops apps) ---- */
typedef struct {
    char day[8];
    char time[8];
    char stage[24];
    char talk[128];
    char speaker[64];
    char image[40];
} badge_talk_t;

/* Parse schedule.csv text into out[0..max). Skips the header, blank lines, and
 * malformed rows (field count != 6, or a time that is not H:MM / HH:MM). Handles
 * RFC4180 quoting so a comma inside a quoted talk title stays one field, and
 * CRLF line endings. Returns the number of talks written. No allocation. */
int badge_schedule_parse(const char *csv, badge_talk_t *out, int max);

/* True if a stage is a talk track (Lamarr/Hopper) vs a workshop room. */
int badge_schedule_is_talk(const char *stage);

/* ---- boot flow / config (main.py) ---- */
/* Next load_counter: current+1 if present, else 1. */
int badge_next_load_counter(int has_counter, int current);
/* Full-screen boot image for a counter: 0 = none, 1 = splash, 2 = easter egg. */
int badge_boot_image(int counter);

#ifdef __cplusplus
}
#endif

#endif /* BADGE_CORE_H */
