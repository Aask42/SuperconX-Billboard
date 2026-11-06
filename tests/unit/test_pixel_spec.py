"""Unit tests for the e-paper colour/packing contract, cross-checked against the
emulator's framebuffer->PNG renderer (emu.write_png) so the two agree."""
import struct
import zlib
from pathlib import Path

import pytest

from support.badge_spec import classify_pixel, plane_index, EPD_W, EPD_H, PLANE_BYTES

emu = pytest.importorskip("emu", reason="emulator/emu.py not importable")


# ---- classification rule ---------------------------------------------------

def test_pure_colours_classify_as_expected():
    assert classify_pixel(0x00, 0x00, 0x00) == "black"   # RGB565 black
    assert classify_pixel(0x1F, 0x00, 0x00) == "red"     # full red
    assert classify_pixel(0x1F, 0x3F, 0x1F) == "white"   # full white
    assert classify_pixel(0x10, 0x00, 0x00) == "red"     # just the red hi-bit
    assert classify_pixel(0x0F, 0x1F, 0x0F) == "black"   # all hi-bits clear


def test_green_or_blue_force_white():
    # Red hi-bit set but green hi-bit also set -> not red, not all-clear -> white.
    assert classify_pixel(0x1F, 0x3F, 0x00) == "white"
    assert classify_pixel(0x1F, 0x00, 0x1F) == "white"


# ---- MONO_HLSB packing -----------------------------------------------------

def test_plane_index_is_msb_first():
    assert plane_index(0, 0) == (0, 0x80)
    assert plane_index(7, 0) == (0, 0x01)
    assert plane_index(8, 0) == (1, 0x80)
    off, mask = plane_index(0, 1)
    assert off == EPD_W // 8 and mask == 0x80


def test_plane_index_rejects_out_of_range():
    with pytest.raises(ValueError):
        plane_index(EPD_W, 0)


# ---- cross-check with the emulator's renderer ------------------------------

def _decode_png(data: bytes):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    pos, width, height, idat = 8, None, None, b""
    while pos < len(data):
        (length,) = struct.unpack(">I", data[pos:pos + 4])
        tag = data[pos + 4:pos + 8]
        body = data[pos + 8:pos + 8 + length]
        if tag == b"IHDR":
            width, height, depth, color = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and color == 2  # 8-bit RGB
        elif tag == b"IDAT":
            idat += body
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 3
    def pixel(x, y):
        row = y * (stride + 1)
        assert raw[row] == 0  # filter type 0 (None)
        i = row + 1 + x * 3
        return tuple(raw[i:i + 3])
    return width, height, pixel


def test_write_png_matches_classification(tmp_path: Path):
    """Set one black and one red pixel in the planes; the rendered PNG must show
    black/red at those coordinates and white elsewhere, per emu.write_png."""
    black = bytearray(PLANE_BYTES)
    red = bytearray(PLANE_BYTES)
    bo, bm = plane_index(3, 2)
    black[bo] |= bm
    ro, rm = plane_index(100, 50)
    red[ro] |= rm

    out = tmp_path / "planes.png"
    emu.write_png(out, bytes(black), bytes(red))
    w, h, pixel = _decode_png(out.read_bytes())
    assert (w, h) == (EPD_W, EPD_H)

    assert pixel(3, 2) == (0x10, 0x10, 0x10)      # black
    assert pixel(100, 50) == (0xD0, 0x10, 0x10)   # red
    assert pixel(500, 400) == (0xF4, 0xF1, 0xE8)  # white background


def test_red_wins_over_black_like_the_panel(tmp_path: Path):
    black = bytearray(PLANE_BYTES)
    red = bytearray(PLANE_BYTES)
    o, m = plane_index(10, 10)
    black[o] |= m
    red[o] |= m  # both set at the same pixel
    out = tmp_path / "both.png"
    emu.write_png(out, bytes(black), bytes(red))
    _, _, pixel = _decode_png(out.read_bytes())
    assert pixel(10, 10) == (0xD0, 0x10, 0x10)  # red precedence
