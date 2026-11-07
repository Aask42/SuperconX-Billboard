/*
 * epd.h — E-paper driver for the Billboard Badge.
 *
 * Pervasive Displays 9.69" 960x672 three-colour (black / red / white) panel,
 * driven as two halves (left CoG via CS1, right CoG via CS2) over SoftSPI.
 * Global refresh only (no partial update); a full refresh takes several seconds.
 *
 * Port of badge_fs/lib/epd.py. The stock driver keeps two 1-bpp framebuffers in
 * MONO_HLSB order (MSB = leftmost pixel): a "black" plane and a "red" plane.
 * For any pixel, at most one plane's bit is set; both clear = white.
 *
 * Buffer geometry:
 *   stride   = 960 / 8            = 120 bytes per row
 *   plane    = 120 * 672          = 80640 bytes per colour plane
 * On refresh each plane is split into left/right half-rows (60 bytes each) and
 * sent to the matching CoG.
 */
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "esp_err.h"

#ifdef __cplusplus
extern "C" {
#endif

#define EPD_WIDTH          960
#define EPD_HEIGHT         672
#define EPD_STRIDE_BYTES   (EPD_WIDTH / 8)              /* 120 */
#define EPD_HALF_STRIDE    (EPD_STRIDE_BYTES / 2)       /* 60  */
#define EPD_PLANE_BYTES    (EPD_STRIDE_BYTES * EPD_HEIGHT) /* 80640 */

/* Initialise GPIO, SoftSPI, power rail and allocate the two framebuffers
 * (preferably in PSRAM). Safe to call once at boot. */
esp_err_t epd_init(void);

/* Direct access to the 1-bpp planes (MONO_HLSB, EPD_PLANE_BYTES each). The
 * framebuffer/drawing component writes straight into these. */
uint8_t *epd_black_plane(void);
uint8_t *epd_red_plane(void);

/* Set both planes to white (all zero). */
void epd_clear(void);

/* Push the current framebuffers to the panel with a full global refresh.
 * Blocks until the panel reports done (polls BUSY1/BUSY2). Multi-second. */
esp_err_t epd_refresh(void);

#ifdef __cplusplus
}
#endif
