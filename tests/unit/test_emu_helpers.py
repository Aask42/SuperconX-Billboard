"""Unit tests for the emulator harness's pure helpers (emu.py), so the pieces the
behave/QEMU tests rely on — partition math and the LittleFS packer — are trusted."""
from pathlib import Path

import pytest

emu = pytest.importorskip("emu", reason="emulator/emu.py not importable")

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "native_firmware"


def _parts():
    return {p["name"]: p for p in emu.partitions(PROJECT, PROJECT)}


def test_partition_table_matches_the_stock_layout():
    parts = _parts()
    assert parts["nvs"]["offset"] == 0x9000
    assert parts["factory"]["offset"] == 0x10000
    assert parts["factory"]["size"] == 0x300000       # 3 MB app
    assert parts["storage"]["offset"] == 0x310000      # LittleFS data
    assert parts["storage"]["size"] == 0x4F0000


def test_partitions_do_not_overlap_and_fit_8mb():
    parts = sorted(_parts().values(), key=lambda p: p["offset"])
    end = 0
    for p in parts:
        assert p["offset"] >= end, f"{p['name']} overlaps previous partition"
        end = p["offset"] + p["size"]
    assert end <= 0x800000, "partitions exceed 8 MB flash"


def test_make_littlefs_image_mounts_and_reads_back(tmp_path: Path):
    littlefs = pytest.importorskip("littlefs")

    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "hello.txt").write_text("emulator littlefs ok\n")
    (src / "sub" / "nested.bin").write_bytes(bytes(range(256)))

    size = 512 * 4096  # 2 MiB, multiple of the 4096 block size
    img = tmp_path / "fs.bin"
    emu.make_littlefs(src, size, img)
    assert img.stat().st_size == size

    fs = littlefs.LittleFS(
        block_size=4096, block_count=size // 4096,
        read_size=128, prog_size=128, lookahead_size=128, block_cycles=512,
        mount=False,
    )
    fs.context.buffer[:] = img.read_bytes()
    fs.mount()

    with fs.open("/hello.txt", "r") as fh:
        assert fh.read() == "emulator littlefs ok\n"
    with fs.open("/sub/nested.bin", "rb") as fh:
        assert fh.read() == bytes(range(256))


def test_write_png_emits_valid_signature_and_size(tmp_path: Path):
    black = bytes(emu.PLANE_BYTES)
    red = bytes(emu.PLANE_BYTES)
    out = tmp_path / "blank.png"
    emu.write_png(out, black, red)
    data = out.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert data[-8:] == b"IEND\xaeB`\x82"  # IEND chunk + CRC
