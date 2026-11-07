"""Host-compiled tests of the firmware's pure C core (native_firmware/components/
badge_core). The SAME C the badge runs is built into a shared library and checked
against the reference models in support/badge_spec.py — so these prove the C
matches the spec, not just that the spec is self-consistent.

Skipped automatically if no C compiler is available.
"""
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from support import badge_spec as spec

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "native_firmware" / "components" / "badge_core"
SRCS = sorted((CORE / "src").glob("*.c"))


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler (cc/gcc/clang) on PATH")
    if not SRCS:
        pytest.skip("badge_core sources not found")
    out = tmp_path_factory.mktemp("badge_core") / "libbadgecore.so"
    cmd = [cc, "-shared", "-fPIC", "-O2", "-std=c99",
           "-I", str(CORE / "include"), *map(str, SRCS), "-o", str(out)]
    subprocess.run(cmd, check=True)
    dll = ctypes.CDLL(str(out))
    dll.badge_classify_pixel.restype = ctypes.c_int
    dll.badge_classify_pixel.argtypes = [ctypes.c_int] * 3
    dll.badge_menu_leds.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.POINTER(ctypes.c_uint8)]
    dll.badge_rgb565_put.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.POINTER(ctypes.c_uint8),
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint16]
    dll.badge_next_load_counter.restype = ctypes.c_int
    dll.badge_next_load_counter.argtypes = [ctypes.c_int, ctypes.c_int]
    dll.badge_boot_image.restype = ctypes.c_int
    dll.badge_boot_image.argtypes = [ctypes.c_int]
    dll.badge_submenu_leds.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_uint8)]
    dll.badge_switcher_init.argtypes = [ctypes.POINTER(CSwitcher), ctypes.c_int, ctypes.c_int]
    dll.badge_switcher_event.restype = ctypes.c_int
    dll.badge_switcher_event.argtypes = [ctypes.POINTER(CSwitcher), ctypes.c_int]
    return dll


class CSwitcher(ctypes.Structure):
    _fields_ = [("count", ctypes.c_int), ("selecting", ctypes.c_int),
                ("selection", ctypes.c_int), ("active", ctypes.c_int)]


_CLS = {"white": 0, "black": 1, "red": 2}


def test_classify_matches_spec_across_hi_bit_space(lib):
    # Exhaustive over the bits that matter + a spread of low bits.
    for r in (0x00, 0x0F, 0x10, 0x1F):
        for g in (0x00, 0x1F, 0x20, 0x3F):
            for b in (0x00, 0x0F, 0x10, 0x1F):
                assert lib.badge_classify_pixel(r, g, b) == _CLS[spec.classify_pixel(r, g, b)], (r, g, b)


def test_menu_leds_match_spec(lib):
    buf = (ctypes.c_uint8 * 24)()
    for total in (1, 5, 8, 9, 16, 20, 48, 49, 64):   # 48+ exercises the OOB fix (issue #5)
        for idx in range(total):
            lib.badge_menu_leds(idx, total, buf)
            got = [(buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2]) for i in range(8)]
            assert got == spec.menu_leds(idx, total), (idx, total)


def test_rgb565_put_sets_the_right_plane_and_bit(lib):
    stride = spec.STRIDE
    black = (ctypes.c_uint8 * spec.PLANE_BYTES)()
    red = (ctypes.c_uint8 * spec.PLANE_BYTES)()
    cases = {
        (3, 2): 0x0000,      # black
        (100, 50): 0xF800,   # red (full red)
        (200, 10): 0xFFFF,   # white
    }
    for (x, y), color in cases.items():
        lib.badge_rgb565_put(black, red, x, y, stride, color)
        off, mask = spec.plane_index(x, y)
        cls = spec.classify_pixel((color >> 11) & 0x1F, (color >> 5) & 0x3F, color & 0x1F)
        if cls == "black":
            assert black[off] & mask and not (red[off] & mask)
        elif cls == "red":
            assert red[off] & mask and not (black[off] & mask)
        else:
            assert not (black[off] & mask) and not (red[off] & mask)


def test_config_helpers_match_spec(lib):
    assert lib.badge_next_load_counter(0, 0) == spec.next_load_counter({})
    assert lib.badge_next_load_counter(1, 3) == spec.next_load_counter({"load_counter": 3})
    _img = {None: 0, spec.SPLASH_IMAGE: 1, spec.EGG_IMAGE: 2}
    for counter in (0, 1, 2, 3, 41, 42, 43):
        assert lib.badge_boot_image(counter) == _img[spec.boot_image_for(counter)], counter


def test_submenu_leds_match_spec(lib):
    buf = (ctypes.c_uint8 * 24)()
    for item in range(0, 64):   # past page 6 to cover the clamp (issue #5)
        lib.badge_submenu_leds(item, buf)
        got = [(buf[i * 3], buf[i * 3 + 1], buf[i * 3 + 2]) for i in range(8)]
        assert got == spec.submenu_leds(item), item


def test_switcher_nav_matches_spec(lib):
    # Run the same button script through the C state machine and the model and
    # assert identical state + launch signal after every event.
    script = [spec.BTN_MENU_PLUS, spec.BTN_MENU_PLUS, spec.BTN_MENU_MINUS,
              spec.BTN_A, spec.BTN_MENU_PLUS, spec.BTN_MENU_PLUS, spec.BTN_B,
              spec.BTN_MENU_MINUS, spec.BTN_MENU_MINUS, spec.BTN_A]
    for count in (1, 3, 5):
        cs = CSwitcher()
        lib.badge_switcher_init(ctypes.byref(cs), count, 0)
        model = spec.Switcher(count, 0)
        for btn in script:
            launched_c = lib.badge_switcher_event(ctypes.byref(cs), btn)
            launched_m = model.event(btn)
            assert bool(launched_c) == launched_m, (count, btn)
            assert (cs.selecting, cs.selection, cs.active) == \
                   (int(model.selecting), model.selection, model.active), (count, btn)


def test_switcher_menu_plus_wraps(lib):
    cs = CSwitcher()
    lib.badge_switcher_init(ctypes.byref(cs), 3, 0)
    for _ in range(3):
        lib.badge_switcher_event(ctypes.byref(cs), spec.BTN_MENU_PLUS)
    assert cs.selection == 0  # wrapped 0->1->2->0


def test_switcher_menu_minus_wraps_negative(lib):
    cs = CSwitcher()
    lib.badge_switcher_init(ctypes.byref(cs), 3, 0)
    lib.badge_switcher_event(ctypes.byref(cs), spec.BTN_MENU_MINUS)
    assert cs.selection == 2  # 0 -> 2 (no negative index)
