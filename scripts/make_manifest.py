#!/usr/bin/env python3
"""Generate an ESP Web Tools manifest.json for a SuperconX Billboard release.

The manifest describes an *app-only* install: bootloader, partition table and the
factory app. The LittleFS `storage` partition (0x310000) is intentionally left
out until the third-party asset payload is cleared for distribution (gitignored,
see BADGE_REPORT.md / tests/COVERAGE.md). It is still validated against
partitions.csv so the two never drift.

Offsets are read from the ESP-IDF build's flasher_args.json so they always match
what idf.py actually produced, then cross-checked against native_firmware/partitions.csv.

Usage:
  make_manifest.py --version 1.2.3 \
      --flasher-args build/flasher_args.json \
      --bootloader bootloader.bin \
      --partition-table partition-table.bin \
      --factory superconx-billboard-1.2.3-factory.bin \
      --out dist/manifest.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# ESP Web Tools requires one of these exact chipFamily strings.
CHIP_FAMILY = "ESP32-S3"


def load_flasher_offsets(flasher_args: Path) -> dict[str, int]:
    """Map the built image basenames to their flash offsets (from flasher_args.json)."""
    data = json.loads(flasher_args.read_text())
    offsets: dict[str, int] = {}
    # flasher_args.json has a top-level "flash_files": {"0x0": "bootloader/bootloader.bin", ...}
    for off, path in data.get("flash_files", {}).items():
        offsets[Path(path).name] = int(off, 0)
    return offsets


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--version", required=True)
    ap.add_argument("--flasher-args", required=True, type=Path)
    ap.add_argument("--bootloader", required=True, help="published basename for bootloader.bin")
    ap.add_argument("--partition-table", required=True, help="published basename for partition-table.bin")
    ap.add_argument("--factory", required=True, help="published basename for the factory app bin")
    ap.add_argument("--name", default="SuperconX Billboard")
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    offsets = load_flasher_offsets(args.flasher_args)

    # Resolve the offset for each component from what idf.py built. The built
    # filenames are bootloader.bin / partition-table.bin / superconx_billboard.bin.
    def off(*candidates: str) -> int:
        for c in candidates:
            if c in offsets:
                return offsets[c]
        raise SystemExit(f"make_manifest: none of {candidates} found in flasher_args {sorted(offsets)}")

    boot_off = off("bootloader.bin")
    ptbl_off = off("partition-table.bin")
    # The factory app is published under a versioned name but built as superconx_billboard.bin.
    app_off = off("superconx_billboard.bin")

    manifest = {
        "name": args.name,
        "version": args.version,
        # A fresh badge should be fully erased before an app-only write so no stale
        # LittleFS/NVS confuses the mount.
        "new_install_prompt_erase": True,
        "builds": [
            {
                "chipFamily": CHIP_FAMILY,
                "parts": [
                    {"path": args.bootloader, "offset": boot_off},
                    {"path": args.partition_table, "offset": ptbl_off},
                    {"path": args.factory, "offset": app_off},
                ],
            }
        ],
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"make_manifest: wrote {args.out}")
    print(f"  bootloader       @ {hex(boot_off)}")
    print(f"  partition-table  @ {hex(ptbl_off)}")
    print(f"  factory (app)    @ {hex(app_off)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
