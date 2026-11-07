/* App-switcher navigation state machine, ported from app_switcher.py. */
#include "badge_core.h"

void badge_switcher_init(badge_switcher_t *s, int count, int initial)
{
    s->count = count;
    s->selecting = 1;
    s->selection = initial;
    s->active = initial;
}

int badge_switcher_event(badge_switcher_t *s, int button)
{
    if (s->count <= 0) {
        return 0;
    }
    if (s->selecting) {
        if (button == BADGE_BTN_MENU_PLUS) {
            s->selection = (s->selection + 1) % s->count;
        } else if (button == BADGE_BTN_MENU_MINUS) {
            s->selection = (s->selection - 1 + s->count) % s->count;
        } else if (button == BADGE_BTN_A || button == BADGE_BTN_B) {
            s->active = s->selection;   /* launch */
            s->selecting = 0;
            return 1;
        }
    } else {
        /* While an app runs, either menu key returns to the selector. */
        if (button == BADGE_BTN_MENU_MINUS || button == BADGE_BTN_MENU_PLUS) {
            s->selecting = 1;
            s->selection = s->active;
        }
    }
    return 0;
}
