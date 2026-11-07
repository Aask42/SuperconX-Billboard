"""Static memory-budget checks (no hardware, no QEMU): the framebuffers, LLM
model, and filesystem image must fit the ESP32-S3's PSRAM and the flash
partitions. Catches an over-budget layout before anything is flashed."""
from pathlib import Path

import pytest

from support.badge_spec import PLANE_BYTES

emu = pytest.importorskip("emu", reason="emulator/emu.py not importable")

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "native_firmware"
DATA = PROJECT / "vfs_root" / "data"

PSRAM_BYTES = 8 * 1024 * 1024   # 8 MB octal PSRAM (badge module)


def test_two_planes_are_the_expected_size():
    assert PLANE_BYTES == 960 // 8 * 672 == 80640


def test_planes_plus_llm_fit_psram_with_headroom():
    model = DATA / "models" / "stories260K.bin"
    tok = DATA / "models" / "tok512.bin"
    model_sz = model.stat().st_size if model.exists() else 0
    tok_sz = tok.stat().st_size if tok.exists() else 0
    # Firmware RAM users that live in PSRAM: 2 full planes + 4 half-planes +
    # a DMA bounce buffer (hardware build), plus the LLM weights at runtime.
    epd_ram = 2 * PLANE_BYTES + 4 * (PLANE_BYTES // 2) + 4096
    runtime = epd_ram + model_sz + tok_sz
    assert runtime < PSRAM_BYTES, f"PSRAM users {runtime} exceed {PSRAM_BYTES}"
    # Insist on real headroom (stacks, heap, decode buffers): use < 60%.
    assert runtime < PSRAM_BYTES * 0.6, f"PSRAM budget too tight: {runtime}"


def test_littlefs_payload_fits_storage_partition():
    if not DATA.is_dir():
        pytest.skip("vfs_root/data not present")
    parts = {p["name"]: p for p in emu.partitions(PROJECT, PROJECT)}
    storage = parts["storage"]["size"]
    used = sum(f.stat().st_size for f in DATA.rglob("*") if f.is_file())
    # LittleFS has per-block overhead; require the raw payload under 85% of the part.
    assert used < storage * 0.85, f"FS payload {used} too large for storage {storage}"


def test_app_binary_fits_factory_partition():
    candidates = [
        REPO / "emulator" / "out" / "native_firmware" / "superconx_billboard.bin",
        PROJECT / "build" / "superconx_billboard.bin",
    ]
    binp = next((c for c in candidates if c.exists()), None)
    if binp is None:
        pytest.skip("no built app binary to size")
    parts = {p["name"]: p for p in emu.partitions(PROJECT, PROJECT)}
    factory = parts["factory"]["size"]
    assert binp.stat().st_size < factory, f"app {binp.stat().st_size} exceeds factory {factory}"
