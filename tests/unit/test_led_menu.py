"""Unit tests for the LED menu-bar contract (mirrors badge_fs/lib/led_bar.py)."""
from support.badge_spec import (
    menu_leds, bitmask_to_leds, NUM_LEDS,
    MENU_BACKGROUND_COLORS, MENU_SELECTED_COLORS,
)


def test_cursor_led_gets_selected_colour():
    leds = menu_leds(app_index=3, total_apps=5)
    assert leds[3] == MENU_SELECTED_COLORS[0]


def test_partial_page_fills_only_up_to_total():
    # 5 apps on page 0: LEDs 0..4 background, 5..7 off, cursor overpainted.
    leds = menu_leds(app_index=0, total_apps=5)
    bg = MENU_BACKGROUND_COLORS[0]
    assert leds[0] == MENU_SELECTED_COLORS[0]          # cursor at 0
    assert leds[1] == bg and leds[4] == bg
    assert leds[5] == (0, 0, 0) and leds[7] == (0, 0, 0)


def test_full_page_fills_all_eight():
    # 16 apps, cursor on page 0 -> page is full -> all 8 are background (+cursor).
    leds = menu_leds(app_index=2, total_apps=16)
    bg = MENU_BACKGROUND_COLORS[0]
    for i, c in enumerate(leds):
        assert c == (MENU_SELECTED_COLORS[0] if i == 2 else bg)


def test_second_page_uses_second_background_colour():
    # app_index 9 -> page 1, cursor at LED 1.
    leds = menu_leds(app_index=9, total_apps=16)
    assert leds[1] == MENU_SELECTED_COLORS[1]
    assert leds[0] == MENU_BACKGROUND_COLORS[1]


def test_large_app_count_does_not_overflow_palette():
    # Regression for the OOB flagged in issue #5: 48+ apps -> page >= 6.
    for total in (48, 49, 56, 64):
        for idx in range(total):
            leds = menu_leds(idx, total)          # must not raise
            assert len(leds) == 8
            assert leds[idx % 8] == MENU_SELECTED_COLORS[0]


def test_bitmask_msb_first():
    leds = bitmask_to_leds(0b1000_0001, r=3, g=3, b=3)
    assert leds[0] == (3, 3, 3)       # MSB -> LED 0
    assert leds[7] == (3, 3, 3)       # LSB -> LED 7
    assert leds[1] == (0, 0, 0)
    assert all(v == (0, 0, 0) for v in leds[2:7])


def test_bitmask_colour_channels():
    leds = bitmask_to_leds(0xFF, r=2, g=0, b=1)
    assert all(v == (2, 0, 1) for v in leds)
    assert len(leds) == NUM_LEDS
