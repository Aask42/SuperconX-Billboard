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

/* ---- boot flow / config (main.py) ---- */
/* Next load_counter: current+1 if present, else 1. */
int badge_next_load_counter(int has_counter, int current);
/* Full-screen boot image for a counter: 0 = none, 1 = splash, 2 = easter egg. */
int badge_boot_image(int counter);

#ifdef __cplusplus
}
#endif

#endif /* BADGE_CORE_H */
