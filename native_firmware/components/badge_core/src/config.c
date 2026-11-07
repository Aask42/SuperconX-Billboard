/* Boot-flow / config helpers, ported from main.py. See badge_core.h. */
#include "badge_core.h"

int badge_next_load_counter(int has_counter, int current)
{
    return has_counter ? current + 1 : 1;
}

int badge_boot_image(int counter)
{
    if (counter < 2) {
        return 1;   /* splash */
    }
    if (counter == 42) {
        return 2;   /* easter egg */
    }
    return 0;       /* none */
}
