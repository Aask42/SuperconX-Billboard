"""Partition-layout guard (issue #9). Locks partitions.csv to the stock badge
layout from BADGE_REPORT.md. A wrong offset/size here would write the app over
the filesystem (or vice versa) and brick/wipe a badge — so any change must be
made deliberately, updating BADGE_REPORT.md and this table together."""
from pathlib import Path

import pytest

emu = pytest.importorskip("emu", reason="emulator/emu.py not importable")

REPO = Path(__file__).resolve().parents[2]
PROJECT = REPO / "native_firmware"
FLASH_SIZE = 0x800000  # 8 MB

# Stock layout, BADGE_REPORT.md: (type, subtype, offset, size)
STOCK = {
    "nvs":      ("data", "nvs",    0x9000,   0x6000),
    "phy_init": ("data", "phy",    0xF000,   0x1000),
    "factory":  ("app",  "factory", 0x10000,  0x300000),
    "storage":  ("data", "spiffs", 0x310000, 0x4F0000),
}


def _parts():
    return {p["name"]: p for p in emu.partitions(PROJECT, PROJECT)}


def test_partition_set_is_exactly_the_stock_set():
    assert set(_parts()) == set(STOCK), "partition names drifted from BADGE_REPORT.md"


def test_each_partition_matches_stock_offset_and_size():
    parts = _parts()
    for name, (ptype, subtype, off, size) in STOCK.items():
        p = parts[name]
        assert p["type"] == ptype, f"{name} type {p['type']} != {ptype}"
        assert p["subtype"] == subtype, f"{name} subtype {p['subtype']} != {subtype}"
        assert p["offset"] == off, f"{name} offset {hex(p['offset'])} != {hex(off)}"
        assert p["size"] == size, f"{name} size {hex(p['size'])} != {hex(size)}"


def test_partitions_are_contiguous_and_fit_8mb():
    parts = sorted(_parts().values(), key=lambda p: p["offset"])
    end = 0
    for p in parts:
        assert p["offset"] >= end, f"{p['name']} overlaps the previous partition"
        end = p["offset"] + p["size"]
    assert end <= FLASH_SIZE, f"layout ends at {hex(end)}, past 8 MB"


def test_factory_and_storage_do_not_collide():
    parts = _parts()
    fac = parts["factory"]
    sto = parts["storage"]
    assert fac["offset"] + fac["size"] <= sto["offset"], "app partition would run into storage"
