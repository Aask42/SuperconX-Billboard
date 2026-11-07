"""Release-artifact contract tests (issue #13) for the #10 pipeline.

These pin the contract between scripts/make_manifest.py (writes the ESP Web Tools
manifest) and scripts/validate_release.py (gates the release in CI), against the
authoritative native_firmware/partitions.csv — without needing an ESP build or
esptool. They catch: wrong chipFamily, offset drift, and any accidental shipping
of the gated LittleFS `storage` partition in an app-only release.
"""
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPTS = REPO / "scripts"
PARTITIONS = REPO / "native_firmware" / "partitions.csv"

BOOTLOADER_OFFSET = 0x0
PARTITION_TABLE_OFFSET = 0x8000
STORAGE_OFFSET = 0x310000
FACTORY_OFFSET = 0x10000


def _load(mod_name):
    path = SCRIPTS / f"{mod_name}.py"
    if not path.exists():
        pytest.skip(f"{path} not present (release pipeline not merged?)")
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def validate_mod():
    return _load("validate_release")


@pytest.fixture(scope="module")
def manifest_mod():
    return _load("make_manifest")


# ---- parse_partitions against the real partitions.csv -----------------------

def test_partitions_csv_layout_is_authoritative(validate_mod):
    offs = validate_mod.parse_partitions(PARTITIONS)
    assert offs["factory"] == FACTORY_OFFSET, offs
    assert offs["storage"] == STORAGE_OFFSET, offs
    # nvs/phy_init live below the app; just confirm they precede the factory app.
    assert offs["nvs"] < FACTORY_OFFSET
    assert offs["phy_init"] < FACTORY_OFFSET


def test_parse_partitions_packs_omitted_offsets(validate_mod, tmp_path):
    csv = tmp_path / "p.csv"
    csv.write_text(
        "# Name, Type, SubType, Offset, Size\n"
        "nvs,      data, nvs,     0x9000,  0x6000\n"
        "phy_init, data, phy,     ,        0x1000\n"   # offset omitted -> packs after nvs
        "factory,  app,  factory, 0x10000, 1M\n"
    )
    offs = validate_mod.parse_partitions(csv)
    assert offs["nvs"] == 0x9000
    assert offs["phy_init"] == 0xF000          # 0x9000 + 0x6000
    assert offs["factory"] == 0x10000


# ---- make_manifest CLI produces a contract-valid manifest -------------------

@pytest.fixture
def manifest(tmp_path):
    flasher = tmp_path / "flasher_args.json"
    flasher.write_text(json.dumps({"flash_files": {
        "0x0": "bootloader/bootloader.bin",
        "0x8000": "partition_table/partition-table.bin",
        "0x10000": "superconx_billboard.bin",
    }}))
    out = tmp_path / "dist" / "manifest.json"
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "make_manifest.py"),
         "--version", "9.9.9", "--flasher-args", str(flasher),
         "--bootloader", "superconx-9.9.9-bootloader.bin",
         "--partition-table", "superconx-9.9.9-partitions.bin",
         "--factory", "superconx-9.9.9-factory.bin",
         "--out", str(out)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(out.read_text())


def test_manifest_shape(manifest):
    assert manifest["version"] == "9.9.9"
    assert manifest["new_install_prompt_erase"] is True
    assert len(manifest["builds"]) == 1
    assert manifest["builds"][0]["chipFamily"] == "ESP32-S3"


def test_manifest_offsets_match_partitions(manifest, validate_mod):
    offs = validate_mod.parse_partitions(PARTITIONS)
    want = {BOOTLOADER_OFFSET, PARTITION_TABLE_OFFSET, offs["factory"]}
    got = {p["offset"] for p in manifest["builds"][0]["parts"]}
    assert got == want, f"manifest offsets {sorted(map(hex, got))} != {sorted(map(hex, want))}"


def test_manifest_is_app_only_no_storage(manifest):
    offsets = {p["offset"] for p in manifest["builds"][0]["parts"]}
    assert STORAGE_OFFSET not in offsets, \
        "app-only release must not ship the gated storage (LittleFS) partition"
    # Exactly three app-only parts: bootloader, partition table, factory app.
    assert len(manifest["builds"][0]["parts"]) == 3


def test_manifest_fails_if_flasher_args_missing_app(tmp_path):
    """If the built app bin isn't in flasher_args, manifest generation must fail,
    not silently emit a manifest with a wrong/zero app offset."""
    flasher = tmp_path / "flasher_args.json"
    flasher.write_text(json.dumps({"flash_files": {
        "0x0": "bootloader/bootloader.bin",
        "0x8000": "partition_table/partition-table.bin",
    }}))  # no superconx_billboard.bin
    res = subprocess.run(
        [sys.executable, str(SCRIPTS / "make_manifest.py"),
         "--version", "1.0.0", "--flasher-args", str(flasher),
         "--bootloader", "b.bin", "--partition-table", "p.bin",
         "--factory", "f.bin", "--out", str(tmp_path / "m.json")],
        capture_output=True, text=True,
    )
    assert res.returncode != 0
