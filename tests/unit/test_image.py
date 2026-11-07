"""Host-compiled tests of the RGBA->planes blit (badge_core/src/image.c), the
bridge the Gallery app uses between a decoded PNG and the e-paper colour planes
(issue #6). Checked against the reference classifier in support/badge_spec.py.

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

EPD_W, EPD_H, STRIDE = 960, 672, 120
PLANE = STRIDE * EPD_H
WHITE, BLACK, RED = 0, 1, 2
_CLS = {"white": WHITE, "black": BLACK, "red": RED}


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler (cc/gcc/clang) on PATH")
    out = tmp_path_factory.mktemp("badge_core") / "libbadgecore.so"
    subprocess.run(
        [cc, "-shared", "-fPIC", "-O2", "-std=c99",
         "-I", str(CORE / "include"), *map(str, SRCS), "-o", str(out)],
        check=True,
    )
    dll = ctypes.CDLL(str(out))
    u8p = ctypes.POINTER(ctypes.c_uint8)
    dll.badge_blit_rgba.argtypes = [u8p, u8p, ctypes.c_int, ctypes.c_int,
                                    u8p, ctypes.c_int, ctypes.c_int]
    dll.badge_center_offset.argtypes = [ctypes.c_int, ctypes.c_int,
                                        ctypes.POINTER(ctypes.c_int),
                                        ctypes.POINTER(ctypes.c_int)]
    return dll


def _planes():
    return (ctypes.c_uint8 * PLANE)(), (ctypes.c_uint8 * PLANE)()


def _px(black, red, x, y):
    off = y * STRIDE + (x >> 3)
    mask = 0x80 >> (x & 7)
    if black[off] & mask:
        return BLACK
    if red[off] & mask:
        return RED
    return WHITE


def _rgba(pixels):
    """pixels: list of (r,g,b) rows-major -> ctypes RGBA buffer (alpha 255)."""
    buf = (ctypes.c_uint8 * (len(pixels) * 4))()
    for i, (r, g, b) in enumerate(pixels):
        buf[i * 4 + 0] = r
        buf[i * 4 + 1] = g
        buf[i * 4 + 2] = b
        buf[i * 4 + 3] = 255
    return buf


def _expect(r, g, b):
    return _CLS[spec.classify_pixel(r >> 3, g >> 2, b >> 3)]


def test_blit_classifies_each_channel_like_the_spec(lib):
    colors = [(255, 0, 0), (0, 0, 0), (255, 255, 255), (255, 0, 255),
              (0, 255, 0), (8, 250, 9), (250, 250, 8)]
    black, red = _planes()
    px = _rgba(colors)
    lib.badge_blit_rgba(black, red, 10, 20, px, len(colors), 1)
    for i, (r, g, b) in enumerate(colors):
        assert _px(black, red, 10 + i, 20) == _expect(r, g, b), (r, g, b)


def test_blit_lands_at_destination_offset(lib):
    black, red = _planes()
    px = _rgba([(255, 0, 0), (0, 0, 0)])   # red, black
    lib.badge_blit_rgba(black, red, 100, 200, px, 2, 1)
    assert _px(black, red, 100, 200) == RED
    assert _px(black, red, 101, 200) == BLACK
    # nothing bled elsewhere
    assert _px(black, red, 99, 200) == WHITE
    assert _px(black, red, 100, 201) == WHITE


def test_blit_clips_off_panel(lib):
    black, red = _planes()
    # 4x1 of black starting 2px before the right edge: 2 land, 2 clip.
    px = _rgba([(0, 0, 0)] * 4)
    lib.badge_blit_rgba(black, red, EPD_W - 2, 5, px, 4, 1)
    assert _px(black, red, EPD_W - 2, 5) == BLACK
    assert _px(black, red, EPD_W - 1, 5) == BLACK
    # no wrap to column 0
    for y in range(EPD_H):
        assert _px(black, red, 0, y) == WHITE


def test_blit_rejects_bad_args(lib):
    black, red = _planes()
    lib.badge_blit_rgba(black, red, 0, 0, None, 5, 5)          # NULL
    lib.badge_blit_rgba(black, red, 0, 0, _rgba([(0, 0, 0)]), 0, 5)   # w=0
    assert all(v == 0 for v in black) and all(v == 0 for v in red)


@pytest.mark.parametrize("w,h,ex,ey", [
    (960, 670, 0, 1),     # full gallery image: 1px top/bottom margin
    (960, 672, 0, 0),
    (100, 100, 430, 286),
    (2000, 2000, 0, 0),   # bigger than panel -> clamped to 0
])
def test_center_offset(lib, w, h, ex, ey):
    ox, oy = ctypes.c_int(), ctypes.c_int()
    lib.badge_center_offset(w, h, ctypes.byref(ox), ctypes.byref(oy))
    assert (ox.value, oy.value) == (ex, ey)
