/*
 * image.c — blit a decoded RGBA image into the e-paper colour planes.
 *
 * Pure, dependency-free (host-compilable with the rest of badge_core). The PNG
 * decode itself lives in the `png` component (lodepng); this is only the
 * pixel-classify + pack step, so it is unit-tested directly on the host.
 */
#include "badge_core.h"

#include <stddef.h>   /* size_t */

void badge_blit_rgba(uint8_t *black, uint8_t *red, int dst_x, int dst_y,
                     const uint8_t *rgba, int w, int h)
{
    if (!rgba || w <= 0 || h <= 0)
        return;
    for (int y = 0; y < h; ++y) {
        int py = dst_y + y;
        if (py < 0 || py >= BADGE_EPD_H)
            continue;
        const uint8_t *row = rgba + (size_t)y * w * 4;
        for (int x = 0; x < w; ++x) {
            int px = dst_x + x;
            if (px < 0 || px >= BADGE_EPD_W)
                continue;
            const uint8_t *p = row + (size_t)x * 4;
            int r5 = p[0] >> 3;   /* RGB888 -> RGB565 channel widths */
            int g6 = p[1] >> 2;
            int b5 = p[2] >> 3;
            int cls = badge_classify_pixel(r5, g6, b5);
            badge_plane_put(black, red, px, py, BADGE_EPD_STRIDE, cls);
        }
    }
}

void badge_center_offset(int w, int h, int *out_x, int *out_y)
{
    int x = (BADGE_EPD_W - w) / 2;
    int y = (BADGE_EPD_H - h) / 2;
    if (out_x) *out_x = x < 0 ? 0 : x;
    if (out_y) *out_y = y < 0 ? 0 : y;
}
