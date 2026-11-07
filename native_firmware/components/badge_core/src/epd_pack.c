/* Pixel classification + MONO_HLSB plane packing. See badge_core.h. */
#include "badge_core.h"

int badge_classify_pixel(int r5, int g6, int b5)
{
    int red_hi = r5 & 0x10;
    int grn_hi = g6 & 0x20;
    int blu_hi = b5 & 0x10;
    if (red_hi && !grn_hi && !blu_hi) {
        return BADGE_PX_RED;
    }
    if (!red_hi && !grn_hi && !blu_hi) {
        return BADGE_PX_BLACK;
    }
    return BADGE_PX_WHITE;
}

void badge_plane_put(uint8_t *black, uint8_t *red, int x, int y, int stride, int cls)
{
    int off = y * stride + (x >> 3);
    uint8_t mask = (uint8_t)(0x80 >> (x & 7));
    switch (cls) {
    case BADGE_PX_RED:
        red[off]   |= mask;
        black[off] &= (uint8_t)~mask;
        break;
    case BADGE_PX_BLACK:
        black[off] |= mask;
        red[off]   &= (uint8_t)~mask;
        break;
    default: /* white */
        black[off] &= (uint8_t)~mask;
        red[off]   &= (uint8_t)~mask;
        break;
    }
}

void badge_rgb565_put(uint8_t *black, uint8_t *red, int x, int y, int stride, uint16_t rgb565)
{
    int r = (rgb565 >> 11) & 0x1F;
    int g = (rgb565 >> 5) & 0x3F;
    int b = rgb565 & 0x1F;
    badge_plane_put(black, red, x, y, stride, badge_classify_pixel(r, g, b));
}
