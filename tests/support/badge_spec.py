"""Executable reference models of badge behaviour.

These are the *contracts* the native firmware must satisfy, transcribed from the
stock MicroPython sources so the unit tests pin them down and document them in
one place. When a C module implements one of these, its host unit test should
assert it matches the model here (and the behave/QEMU tests assert the running
firmware does too).

Sources:
  - pixel classification + MONO_HLSB packing: badge_fs/lib/epd.py (_lv_flush)
  - schedule format:                          badge_fs/data/schedule.csv
  - LED menu:                                 badge_fs/lib/led_bar.py
"""
from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import List, Dict, Tuple

# ---- display geometry (epd.py) --------------------------------------------
EPD_W = 960
EPD_H = 672
STRIDE = EPD_W // 8          # 120 bytes per row, MONO_HLSB
PLANE_BYTES = STRIDE * EPD_H  # 80640


def classify_pixel(r5: int, g6: int, b5: int) -> str:
    """RGB565 component -> 'red' | 'black' | 'white'.

    Exact rule from epd.py _lv_flush: red is "red bit set, green/blue clear",
    black is "all three clear", everything else stays white. r is 5-bit, g 6-bit,
    b 5-bit (as unpacked from RGB565).
    """
    red_hi = r5 & 0x10
    grn_hi = g6 & 0x20
    blu_hi = b5 & 0x10
    if red_hi and not grn_hi and not blu_hi:
        return "red"
    if not red_hi and not grn_hi and not blu_hi:
        return "black"
    return "white"


def plane_index(x: int, y: int) -> Tuple[int, int]:
    """(x, y) -> (byte offset into a MONO_HLSB plane, bit mask). MSB = leftmost."""
    if not (0 <= x < EPD_W and 0 <= y < EPD_H):
        raise ValueError(f"pixel out of range: {x},{y}")
    return y * STRIDE + (x >> 3), 0x80 >> (x & 7)


# ---- schedule (schedule.csv) ----------------------------------------------
SCHEDULE_FIELDS = ("day", "time", "stage", "talk", "speaker", "image")
VALID_DAYS = {"Fri", "Sat", "Sun"}
# schedule.csv carries BOTH the talks (the two stages) and the workshops (rooms).
# The Schedule app shows talk stages; the Workshops app shows the rooms.
TALK_STAGES = {"Lamarr", "Hopper"}
WORKSHOP_STAGES = {"Room A", "Room B", "HQ 2nd Floor"}
VALID_STAGES = TALK_STAGES | WORKSHOP_STAGES
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}$")


def parse_schedule(path: Path) -> List[Dict[str, str]]:
    """Parse schedule.csv into a list of dict rows (RFC4180 quoting aware).

    The native Schedule app must tolerate commas inside quoted talk titles, which
    is exactly what csv.reader handles — a naive strtok(',') split would not.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != list(SCHEDULE_FIELDS):
            raise ValueError(f"unexpected header: {reader.fieldnames}")
        return [dict(row) for row in reader]


# ---- LED menu bar (led_bar.py) --------------------------------------------
NUM_LEDS = 8
MENU_BACKGROUND_COLORS = [(2, 1, 0), (0, 1, 1), (2, 0, 1), (3, 0, 0), (0, 2, 0), (0, 0, 1)]
MENU_SELECTED_COLORS = [(3, 2, 1)] * 6


def menu_leds(app_index: int, total_apps: int, num_leds: int = NUM_LEDS) -> List[Tuple[int, int, int]]:
    """Reference port of LedBar.menu_display -> the 8 LED colours.

    Page = index // 8, cursor = index % 8. A fully-populated page is filled with
    that page's background colour; a partial page fills only the first
    (total % 8) LEDs. The cursor LED is overpainted with the selected colour.
    """
    leds: List[Tuple[int, int, int]] = [(0, 0, 0)] * num_leds
    page = app_index // num_leds
    cursor = app_index % num_leds
    bg = MENU_BACKGROUND_COLORS[page]
    if page < total_apps // num_leds:
        leds = [bg] * num_leds
    else:
        for i in range(total_apps % num_leds):
            leds[i] = bg
    leds[cursor] = MENU_SELECTED_COLORS[page]
    return leds


def bitmask_to_leds(x: int, r: int = 3, g: int = 3, b: int = 3) -> List[Tuple[int, int, int]]:
    """Port of led_bar.bitmask_to_leds: 8-bit mask (MSB first) -> LED colours."""
    out = []
    for i in range(NUM_LEDS):
        v = (x >> (NUM_LEDS - 1 - i)) & 1
        out.append((v * r, v * g, v * b))
    return out


# ---- boot flow + config (main.py / json_configs.py) ------------------------
SPLASH_BELOW_COUNT = 2      # main.py draws the splash while load_counter < 2
EASTER_EGG_COUNT = 42       # main.py shows the egg image on boot #42
SPLASH_IMAGE = "supercon-x-splash.png"
# Stock main.py asked for "never-epd.png" (hyphen) but the file is "never_epd.png"
# (underscore) -> OSError crash on boot #42. The native rewrite must use this name.
EGG_IMAGE = "never_epd.png"


def next_load_counter(cfg: dict) -> int:
    """Port of main.py's counter bump: start at 1, otherwise +1."""
    return cfg["load_counter"] + 1 if "load_counter" in cfg else 1


def boot_image_for(counter: int):
    """Which full-screen image boot draws for a given load_counter, or None."""
    if counter < SPLASH_BELOW_COUNT:
        return SPLASH_IMAGE
    if counter == EASTER_EGG_COUNT:
        return EGG_IMAGE
    return None


# ---- assets the apps load (badge_fs/data) ----------------------------------
GALLERY_COUNT = 44          # gallery/001.png .. 044.png
REQUIRED_GRAPHICS = {
    "supercon-x-splash.png", "never_epd.png", "hello_i_am_blank.png",
    "lamarr.png", "hopper.png", "workshops.png",
    "friday.png", "saturday.png", "sunday.png",
}


def gallery_name(n: int) -> str:
    """Gallery filenames are zero-padded to 3 digits (001.png .. 044.png)."""
    if not (1 <= n <= GALLERY_COUNT):
        raise ValueError(f"gallery index {n} out of 1..{GALLERY_COUNT}")
    return f"{n:03d}.png"


# ---- on-device LLM (llama2.c / tinystories) --------------------------------
LLM_VOCAB_SIZE = 512        # stories260K + tok512 -> vocab 512 (model & tokenizer must agree)


def read_llama2_config(path: Path) -> dict:
    """Parse a llama2.c checkpoint header: 7 little-endian int32
    (dim, hidden_dim, n_layers, n_heads, n_kv_heads, vocab_size, seq_len)."""
    import struct
    with open(path, "rb") as fh:
        dim, hidden, layers, heads, kv, vocab, seq = struct.unpack("<7i", fh.read(28))
    return dict(dim=dim, hidden_dim=hidden, n_layers=layers, n_heads=heads,
                n_kv_heads=kv, vocab_size=vocab, seq_len=seq)
