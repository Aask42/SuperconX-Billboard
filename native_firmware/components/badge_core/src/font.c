/*
 * font.c — 8x8 bitmap text into the e-paper colour planes.
 *
 * Pure, dependency-free (host-compilable with the rest of badge_core). The glyph
 * table is the vendored public-domain font8x8_basic (Daniel Hepper / IBM VGA),
 * included here so it has exactly one definition. Each glyph byte is one row;
 * the least-significant bit is the leftmost column — the font's own convention,
 * so this must stay LSB-first or hardware text comes out mirrored.
 */
#include "badge_core.h"

#include "font8x8_basic.h"   /* defines: char font8x8_basic[128][8] */

#define GLYPH_PX   8                     /* glyph is 8x8 dots              */
#define GLYPH_GAP  1                     /* 1 dot of inter-glyph spacing   */
#define ADVANCE    (GLYPH_PX + GLYPH_GAP)/* dots a glyph+gap advances       */

void badge_draw_char(uint8_t *black, uint8_t *red, int x, int y,
                     char c, int cls, int scale)
{
    unsigned uc = (unsigned char)c;
    if (uc >= 128 || scale < 1)
        return;

    const char *rows = font8x8_basic[uc];
    for (int row = 0; row < GLYPH_PX; ++row) {
        unsigned bits = (unsigned char)rows[row];
        for (int col = 0; col < GLYPH_PX; ++col) {
            if (!(bits & (1u << col)))   /* LSB = leftmost */
                continue;
            /* expand this dot to scale x scale, clipping to the panel */
            for (int dy = 0; dy < scale; ++dy) {
                int py = y + row * scale + dy;
                if (py < 0 || py >= BADGE_EPD_H)
                    continue;
                for (int dx = 0; dx < scale; ++dx) {
                    int px = x + col * scale + dx;
                    if (px < 0 || px >= BADGE_EPD_W)
                        continue;
                    badge_plane_put(black, red, px, py, BADGE_EPD_STRIDE, cls);
                }
            }
        }
    }
}

int badge_draw_text(uint8_t *black, uint8_t *red, int x, int y,
                    const char *s, int cls, int scale)
{
    if (scale < 1)
        scale = 1;
    int cur_x = x;
    int cur_y = y;
    for (; s && *s; ++s) {
        if (*s == '\n') {
            cur_x = x;
            cur_y += ADVANCE * scale;
            continue;
        }
        badge_draw_char(black, red, cur_x, cur_y, *s, cls, scale);
        cur_x += ADVANCE * scale;
    }
    return cur_x;
}

int badge_text_width(const char *s, int scale)
{
    if (scale < 1)
        scale = 1;
    int best = 0;
    int line = 0;   /* glyphs on the current line */
    for (; s && *s; ++s) {
        if (*s == '\n') {
            if (line > best) best = line;
            line = 0;
        } else {
            ++line;
        }
    }
    if (line > best) best = line;
    if (best == 0)
        return 0;
    /* N glyphs occupy N*9 - 1 dots (trailing gap trimmed), times scale. */
    return (ADVANCE * best - GLYPH_GAP) * scale;
}
