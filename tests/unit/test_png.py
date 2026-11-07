"""Host-compiled integration test of the Gallery decode path: the vendored
lodepng (decode-only) + badge_png wrapper + badge_core blit, run against the
real staged gallery PNGs. Proves a decoded image actually populates the planes
(palette images carry red; the 1-bit grayscale image does not).

Skipped if no C compiler, or if the gallery assets aren't staged (gitignored;
present only locally / not in CI).
"""
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CORE = REPO / "native_firmware" / "components" / "badge_core"
PNG = REPO / "native_firmware" / "components" / "png"
GALLERY = REPO / "native_firmware" / "vfs_root" / "data" / "gallery"

STRIDE, EPD_H = 120, 672
PLANE = STRIDE * EPD_H


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler (cc/gcc/clang) on PATH")
    srcs = [PNG / "lodepng.c", PNG / "badge_png.c",
            CORE / "src" / "image.c", CORE / "src" / "epd_pack.c"]
    out = tmp_path_factory.mktemp("png") / "libbadgepng.so"
    subprocess.run(
        [cc, "-shared", "-fPIC", "-O2", "-std=c99",
         "-DLODEPNG_NO_COMPILE_ENCODER", "-DLODEPNG_NO_COMPILE_CPP",
         "-I", str(CORE / "include"), "-I", str(PNG / "include"),
         *map(str, srcs), "-o", str(out)],
        check=True,
    )
    dll = ctypes.CDLL(str(out))
    u8p = ctypes.POINTER(ctypes.c_uint8)
    dll.badge_png_decode_to_planes.restype = ctypes.c_uint
    dll.badge_png_decode_to_planes.argtypes = [
        u8p, ctypes.c_size_t, u8p, u8p,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
    return dll


def _decode(lib, path):
    data = path.read_bytes()
    buf = (ctypes.c_uint8 * len(data)).from_buffer_copy(data)
    black = (ctypes.c_uint8 * PLANE)()
    red = (ctypes.c_uint8 * PLANE)()
    w, h = ctypes.c_int(), ctypes.c_int()
    err = lib.badge_png_decode_to_planes(buf, len(data), black, red,
                                         ctypes.byref(w), ctypes.byref(h))
    nb = sum(bin(b).count("1") for b in black)
    nr = sum(bin(b).count("1") for b in red)
    return err, w.value, h.value, nb, nr


def test_decodes_a_palette_image_with_ink(lib):
    img = GALLERY / "001.png"
    if not img.exists():
        pytest.skip("gallery assets not staged (gitignored)")
    err, w, h, nb, nr = _decode(lib, img)
    assert err == 0
    assert (w, h) == (960, 670)
    assert nb > 0, "no black pixels decoded"
    assert nr > 0, "palette gallery image should carry red ink"


def test_decodes_all_staged_gallery_images(lib):
    if not GALLERY.is_dir():
        pytest.skip("gallery assets not staged (gitignored)")
    pngs = sorted(GALLERY.glob("*.png"))
    if not pngs:
        pytest.skip("no gallery PNGs present")
    for img in pngs:
        err, w, h, nb, nr = _decode(lib, img)
        assert err == 0, f"{img.name} decode error {err}"
        assert w == 960 and 668 <= h <= 672, f"{img.name} unexpected size {w}x{h}"
        assert nb + nr > 0, f"{img.name} decoded to a blank screen"


def test_rejects_non_png_bytes(lib):
    black = (ctypes.c_uint8 * PLANE)()
    red = (ctypes.c_uint8 * PLANE)()
    junk = (ctypes.c_uint8 * 16)(*range(16))
    err = lib.badge_png_decode_to_planes(junk, 16, black, red, None, None)
    assert err != 0
    assert all(v == 0 for v in black) and all(v == 0 for v in red)
