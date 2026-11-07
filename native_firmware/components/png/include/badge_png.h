/*
 * badge_png.h — decode a PNG straight into the e-paper colour planes.
 *
 * Thin wrapper over the vendored lodepng (decode path only). The pixel
 * classification + packing is badge_core's badge_blit_rgba, so the colour
 * decisions stay host-tested; this just owns the decode + centering + free.
 */
#ifndef BADGE_PNG_H
#define BADGE_PNG_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Decode `len` bytes of PNG at `png`, classify every pixel into the black/red
 * planes centered on the panel. out_w/out_h (may be NULL) receive the decoded
 * size. Returns 0 on success, or the lodepng error code (nonzero) on failure;
 * on failure the planes are left untouched. Allocates/frees internally (the
 * RGBA buffer is ~w*h*4 bytes — expect it to land in PSRAM on hardware). */
unsigned badge_png_decode_to_planes(const uint8_t *png, size_t len,
                                    uint8_t *black, uint8_t *red,
                                    int *out_w, int *out_h);

#ifdef __cplusplus
}
#endif

#endif /* BADGE_PNG_H */
