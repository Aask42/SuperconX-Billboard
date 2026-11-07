/* LED menu-bar colours, ported from led_bar.py menu_display. See badge_core.h. */
#include "badge_core.h"

static const uint8_t MENU_BG[6][3] = {
    {2, 1, 0}, {0, 1, 1}, {2, 0, 1}, {3, 0, 0}, {0, 2, 0}, {0, 0, 1},
};
static const uint8_t MENU_SEL[3] = {3, 2, 1};

void badge_menu_leds(int app_index, int total_apps, uint8_t out[8][3])
{
    for (int i = 0; i < 8; i++) {
        out[i][0] = out[i][1] = out[i][2] = 0;
    }
    int page = app_index / 8;
    int cursor = app_index % 8;
    const uint8_t *bg = MENU_BG[page];

    if (page < total_apps / 8) {
        for (int i = 0; i < 8; i++) {
            out[i][0] = bg[0]; out[i][1] = bg[1]; out[i][2] = bg[2];
        }
    } else {
        for (int i = 0; i < total_apps % 8; i++) {
            out[i][0] = bg[0]; out[i][1] = bg[1]; out[i][2] = bg[2];
        }
    }
    out[cursor][0] = MENU_SEL[0];
    out[cursor][1] = MENU_SEL[1];
    out[cursor][2] = MENU_SEL[2];
}
