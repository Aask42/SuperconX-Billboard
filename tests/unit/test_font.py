"""Host-compiled tests of the firmware's 8x8 text renderer (badge_core/src/font.c).

The SAME C the badge runs is built into a shared library and checked against the
glyph bits parsed straight from the vendored font8x8_basic.h — so these prove the
renderer's bit order, scaling, advance, newline, clipping and centering math,
not just that a reference agrees with itself. (Issue #6.)

Skipped automatically if no C compiler is available.
"""
import ctypes
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "native_firmware" / "components" / "badge_core"
SRCS = sorted((CORE / "src").glob("*.c"))

EPD_W, EPD_H, STRIDE = 960, 672, 120
PLANE = STRIDE * EPD_H
WHITE, BLACK, RED = 0, 1, 2


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler (cc/gcc/clang) on PATH")
    if not SRCS:
        pytest.skip("badge_core sources not found")
    out = tmp_path_factory.mktemp("badge_core") / "libbadgecore.so"
    subprocess.run(
        [cc, "-shared", "-fPIC", "-O2", "-std=c99",
         "-I", str(CORE / "include"), *map(str, SRCS), "-o", str(out)],
        check=True,
    )
    dll = ctypes.CDLL(str(out))
    u8p = ctypes.POINTER(ctypes.c_uint8)
    dll.badge_draw_char.argtypes = [u8p, u8p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_char, ctypes.c_int, ctypes.c_int]
    dll.badge_draw_text.restype = ctypes.c_int
    dll.badge_draw_text.argtypes = [u8p, u8p, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_char_p, ctypes.c_int, ctypes.c_int]
    dll.badge_text_width.restype = ctypes.c_int
    dll.badge_text_width.argtypes = [ctypes.c_char_p, ctypes.c_int]
    return dll


@pytest.fixture(scope="module")
def glyphs():
    """Parse font8x8_basic[128][8] out of the vendored header (the ground truth)."""
    text = (CORE / "src" / "font8x8_basic.h").read_text()
    body = text.split("{", 1)[1]
    rows = re.findall(r"\{([^}]*)\}", body)
    table = []
    for r in rows[:128]:
        nums = [int(x, 0) for x in re.findall(r"0x[0-9A-Fa-f]+|\d+", r)]
        if len(nums) == 8:
            table.append(nums)
    assert len(table) == 128, f"parsed {len(table)} glyphs, expected 128"
    return table


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


def _lit(glyph):
    """Set of (col,row) dots a glyph lights — LSB is the leftmost column."""
    return {(col, row) for row in range(8) for col in range(8)
            if glyph[row] & (1 << col)}


def test_draw_char_matches_font_bits_for_printables(lib, glyphs):
    for ch in "AQ0 !gW~":
        black, red = _planes()
        lib.badge_draw_char(black, red, 100, 50, ch.encode(), BLACK, 1)
        lit = _lit(glyphs[ord(ch)])
        for row in range(8):
            for col in range(8):
                want = BLACK if (col, row) in lit else WHITE
                assert _px(black, red, 100 + col, 50 + row) == want, (ch, col, row)


def test_draw_char_colour_plane(lib, glyphs):
    black, red = _planes()
    lib.badge_draw_char(black, red, 0, 0, b"A", RED, 1)
    for (col, row) in _lit(glyphs[ord("A")]):
        assert _px(black, red, col, row) == RED


def test_scale_expands_each_dot(lib, glyphs):
    black, red = _planes()
    s = 3
    lib.badge_draw_char(black, red, 10, 20, b"A", BLACK, s)
    lit = _lit(glyphs[ord("A")])
    for (col, row) in [(c, r) for c in range(8) for r in range(8)]:
        on = (col, row) in lit
        for dy in range(s):
            for dx in range(s):
                px = _px(black, red, 10 + col * s + dx, 20 + row * s + dy)
                assert px == (BLACK if on else WHITE), (col, row, dx, dy)


def test_text_advances_nine_px_per_glyph(lib, glyphs):
    black, red = _planes()
    end = lib.badge_draw_text(black, red, 0, 0, b"AB", BLACK, 1)
    assert end == 2 * 9  # two glyphs, each advances 8+1
    # 'B' must appear starting at x=9, matching its glyph bits.
    for (col, row) in _lit(glyphs[ord("B")]):
        assert _px(black, red, 9 + col, row) == BLACK


def test_newline_drops_a_line_and_resets_x(lib, glyphs):
    black, red = _planes()
    lib.badge_draw_text(black, red, 5, 0, b"A\nB", BLACK, 2)
    # 'B' starts back at x=5, y = 9*2 down.
    for (col, row) in _lit(glyphs[ord("B")]):
        for dy in range(2):
            for dx in range(2):
                assert _px(black, red, 5 + col * 2 + dx, 18 + row * 2 + dy) == BLACK


def test_clipping_off_right_and_bottom_edges(lib, glyphs):
    black, red = _planes()
    # Place so cols 4..7 fall off the right edge; must not crash or wrap.
    lib.badge_draw_char(black, red, EPD_W - 4, EPD_H - 4, b"~", BLACK, 1)
    lit = _lit(glyphs[ord("~")])
    for (col, row) in lit:
        x, y = EPD_W - 4 + col, EPD_H - 4 + row
        if x < EPD_W and y < EPD_H:
            assert _px(black, red, x, y) == BLACK
    # No wrap to column 0 of the next row.
    for y in range(EPD_H):
        assert _px(black, red, 0, y) == WHITE


def test_nonprintable_draws_nothing(lib):
    black, red = _planes()
    lib.badge_draw_char(black, red, 0, 0, bytes([200]), BLACK, 1)  # >127
    assert all(b == 0 for b in black) and all(b == 0 for b in red)


@pytest.mark.parametrize("s,scale,expected", [
    (b"", 1, 0),
    (b"A", 1, 8),            # 9*1 - 1
    (b"AB", 1, 17),          # 9*2 - 1
    (b"ABC", 2, 2 * (9 * 3 - 1)),
    (b"A\nABC", 1, 9 * 3 - 1),   # widest line wins
])
def test_text_width(lib, s, scale, expected):
    assert lib.badge_text_width(s, scale) == expected
