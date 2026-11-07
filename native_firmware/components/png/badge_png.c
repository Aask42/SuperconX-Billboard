/* See badge_png.h. Decode-only lodepng wrapper feeding badge_core's blit. */
#include "badge_png.h"

#include "lodepng.h"
#include "badge_core.h"

#include <stdlib.h>

unsigned badge_png_decode_to_planes(const uint8_t *png, size_t len,
                                    uint8_t *black, uint8_t *red,
                                    int *out_w, int *out_h)
{
    unsigned char *rgba = NULL;
    unsigned w = 0, h = 0;
    unsigned err = lodepng_decode32(&rgba, &w, &h, png, len);
    if (err) {
        free(rgba);   /* lodepng frees on error, but be defensive */
        return err;
    }
    int ox = 0, oy = 0;
    badge_center_offset((int)w, (int)h, &ox, &oy);
    badge_blit_rgba(black, red, ox, oy, rgba, (int)w, (int)h);
    free(rgba);
    if (out_w) *out_w = (int)w;
    if (out_h) *out_h = (int)h;
    return 0;
}
