#!/usr/bin/env python3
"""In-workflow validation of a built SuperconX Billboard release.

Checks (all must pass):
  1. esptool image_info parses the merged full image and the factory app image.
  2. The factory app's embedded version equals the release version (PROJECT_VER
     made it into esp_app_desc_t), so firmware + flasher can report it.
  3. manifest.json is valid JSON and every referenced part file exists.
  4. Each manifest part's offset matches the authoritative flash layout taken from
     native_firmware/partitions.csv (factory @ 0x10000) plus the fixed
     bootloader (0x0) / partition-table (0x8000) offsets for ESP32-S3.
  5. The app-only invariant holds: the manifest does NOT reference the gated
     `storage` LittleFS partition, whose intended offset (0x310000) is still
     confirmed present in partitions.csv so the layout never silently drifts.

Run inside the ESP-IDF container after `. $IDF_PATH/export.sh` so `esptool.py`
is importable. Exits non-zero on the first failure.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

# Fixed ESP32-S3 boot offsets (bootloader is at 0x0 on the S3, unlike the ESP32).
BOOTLOADER_OFFSET = 0x0
PARTITION_TABLE_OFFSET = 0x8000


def fail(msg: str) -> "NoReturn":  # type: ignore[name-defined]
    print(f"validate_release: FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def ok(msg: str) -> None:
    print(f"validate_release: ok: {msg}")


def parse_partitions(csv_path: Path) -> dict[str, int]:
    """Return {partition_name: offset} from an ESP-IDF partitions.csv.

    Offsets default to packing after the previous entry when a row omits one,
    mirroring the gen_esp32part.py behaviour (and emulator/emu.py).
    """
    offsets: dict[str, int] = {}
    cursor = 0x9000  # first app/data region after the (implicit) partition table
    for raw in csv_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cells = [c.strip() for c in line.split(",")]
        name = cells[0]
        off_cell = cells[3] if len(cells) > 3 else ""
        off = int(off_cell, 0) if off_cell else cursor
        size_cell = cells[4] if len(cells) > 4 else "0"
        mult = 1
        if size_cell and size_cell[-1] in "Kk":
            mult, size_cell = 1024, size_cell[:-1]
        elif size_cell and size_cell[-1] in "Mm":
            mult, size_cell = 1024 * 1024, size_cell[:-1]
        size = int(size_cell, 0) * mult if size_cell else 0
        offsets[name] = off
        cursor = off + size
    return offsets


def esptool_image_info(binary: Path) -> str:
    """Run `esptool.py image_info <binary>` and return combined output (fails hard)."""
    for argv in (
        [sys.executable, "-m", "esptool", "image_info", str(binary)],
        ["esptool.py", "image_info", str(binary)],
    ):
        try:
            res = subprocess.run(argv, capture_output=True, text=True)
        except FileNotFoundError:
            continue
        out = (res.stdout or "") + (res.stderr or "")
        if res.returncode != 0:
            fail(f"esptool image_info {binary.name} exited {res.returncode}:\n{out}")
        return out
    fail("esptool not found (did you source $IDF_PATH/export.sh?)")
    return ""  # unreachable


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", required=True, type=Path, help="directory holding the release assets")
    ap.add_argument("--manifest", required=True, type=Path)
    ap.add_argument("--merged", required=True, type=Path, help="merged full-flash image (app-only)")
    ap.add_argument("--factory", required=True, type=Path, help="factory app image")
    ap.add_argument("--partitions", required=True, type=Path, help="native_firmware/partitions.csv")
    ap.add_argument("--version", required=True)
    args = ap.parse_args()

    # 1. esptool image_info parses the merged image (first image = bootloader) and the app.
    merged_info = esptool_image_info(args.merged)
    ok(f"esptool image_info parsed merged image {args.merged.name}")
    app_info = esptool_image_info(args.factory)
    ok(f"esptool image_info parsed factory app {args.factory.name}")

    # 2. Embedded version string is the release version.
    if args.version not in app_info:
        print(app_info, file=sys.stderr)
        fail(
            f"release version {args.version!r} not found in the factory app's image_info; "
            "PROJECT_VER was not embedded (firmware/flasher could not report it)"
        )
    ok(f"factory app reports embedded version {args.version!r}")

    # 3. manifest parses and references real files.
    try:
        manifest = json.loads(args.manifest.read_text())
    except json.JSONDecodeError as e:
        fail(f"manifest.json is not valid JSON: {e}")
    ok("manifest.json is valid JSON")

    builds = manifest.get("builds") or []
    if not builds:
        fail("manifest has no builds[]")
    build = builds[0]
    if build.get("chipFamily") != "ESP32-S3":
        fail(f"manifest chipFamily must be 'ESP32-S3', got {build.get('chipFamily')!r}")
    ok("manifest chipFamily is ESP32-S3")
    if manifest.get("version") != args.version:
        fail(f"manifest version {manifest.get('version')!r} != release version {args.version!r}")
    ok(f"manifest version is {args.version!r}")

    parts = build.get("parts") or []
    if not parts:
        fail("manifest build has no parts[]")

    # 4. Offsets match the authoritative layout.
    part_offsets = parse_partitions(args.partitions)
    expected = {
        BOOTLOADER_OFFSET: "bootloader",
        PARTITION_TABLE_OFFSET: "partition table",
        part_offsets["factory"]: "factory app",  # 0x10000 per partitions.csv
    }
    for part in parts:
        path = args.dist / part["path"]
        if not path.exists():
            fail(f"manifest references missing file {part['path']!r}")
        off = part["offset"]
        if off not in expected:
            fail(
                f"manifest part {part['path']!r} has offset {hex(off)}, "
                f"not one of the expected {[hex(o) for o in expected]}"
            )
        ok(f"part {part['path']} @ {hex(off)} ({expected[off]}) exists and matches layout")

    # 5. app-only invariant + storage offset confirmed in partitions.csv.
    if part_offsets["factory"] != 0x10000:
        fail(f"partitions.csv factory offset is {hex(part_offsets['factory'])}, expected 0x10000")
    if part_offsets.get("storage") != 0x310000:
        fail(
            f"partitions.csv storage offset is {part_offsets.get('storage')}, expected 0x310000 "
            "(manifest/firmware offsets would drift)"
        )
    referenced_offsets = {p["offset"] for p in parts}
    if 0x310000 in referenced_offsets:
        fail("this is an APP-ONLY release but the manifest references the gated storage partition (0x310000)")
    ok("app-only invariant holds: storage @ 0x310000 present in partitions.csv but not shipped")

    print("validate_release: ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
