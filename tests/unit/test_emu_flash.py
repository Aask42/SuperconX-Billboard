"""Verify LittleFS placement and per-scenario flash isolation on the host."""

from pathlib import Path

import littlefs
import pytest

import emu


PROJECT = Path(__file__).resolve().parents[2] / "native_firmware"


def read_fixture_file(image, partition_size, name):
    fs = littlefs.LittleFS(
        block_size=4096,
        block_count=partition_size // 4096,
        read_size=128,
        prog_size=128,
        lookahead_size=128,
        block_cycles=512,
        mount=False,
    )
    fs.context.buffer[:] = image
    fs.mount()
    with fs.open(name, "rb") as file:
        return file.read()


def test_make_flash_preserves_app_and_creates_independent_storage_images(
    monkeypatch, tmp_path
):
    bdir = tmp_path / "build"
    bdir.mkdir()
    monkeypatch.setattr(emu, "build_dir", lambda project: bdir)
    parts = {p["name"]: p for p in emu.partitions(PROJECT, bdir)}
    assert parts["factory"]["offset"] == 0x10000
    storage = parts["storage"]
    assert (storage["offset"], storage["size"]) == (0x310000, 0x4F0000)

    original = bytearray(b"\xff" * 0x800000)
    original[0x1000:0x1008] = b"BOOTMARK"
    original[0x10000:0x10008] = b"APPMARK!"
    original[storage["offset"]:storage["offset"] + 8] = b"OLD DATA"
    original = bytes(original)
    base = bdir / "emu_flash.bin"
    base.write_bytes(original)

    src_a = tmp_path / "fixture-a"
    src_b = tmp_path / "fixture-b"
    src_a.mkdir()
    src_b.mkdir()
    (src_a / "state.json").write_text('{"counter": 41}\n')
    (src_b / "state.json").write_text('{"counter": 8}\n')
    expected_a = tmp_path / "expected-a.bin"
    emu.make_littlefs(src_a, storage["size"], expected_a)

    out_a = tmp_path / "scenario-a.bin"
    out_b = tmp_path / "scenario-b.bin"
    assert emu.make_flash(PROJECT, src_a, out_a) == out_a
    assert emu.make_flash(PROJECT, src_b, out_b) == out_b
    assert not out_a.samefile(out_b)
    assert not out_a.samefile(base)

    image_a, image_b = out_a.read_bytes(), out_b.read_bytes()
    offset, size = storage["offset"], storage["size"]
    assert len(image_a) == len(image_b) == len(original) == 0x800000
    assert image_a[:offset] == image_b[:offset] == original[:offset]
    assert image_a[0x10000:0x10008] == image_b[0x10000:0x10008] == b"APPMARK!"
    assert image_a[offset:offset + size] == expected_a.read_bytes()
    assert read_fixture_file(image_a[offset:offset + size], size, "/state.json") == (
        b'{"counter": 41}\n'
    )
    assert read_fixture_file(image_b[offset:offset + size], size, "/state.json") == (
        b'{"counter": 8}\n'
    )
    assert base.read_bytes() == original
    assert not out_a.with_suffix(".fs.bin").exists()
    assert not out_b.with_suffix(".fs.bin").exists()

    with out_a.open("r+b") as file:
        file.seek(offset)
        file.write(b"changed")
    assert out_b.read_bytes() == image_b
    assert base.read_bytes() == original


def test_make_flash_uses_selected_partition_offsets_from_csv(monkeypatch, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "partitions.csv").write_text(
        "factory,app,factory,0x10000,0x10000,\n"
        "storage,data,spiffs,0x30000,0x20000,\n"
        "fixtures,data,spiffs,0x60000,0x10000,\n"
    )
    bdir = tmp_path / "build"
    bdir.mkdir()
    monkeypatch.setattr(emu, "build_dir", lambda project: bdir)
    original = b"\xa5" * 0x100000
    (bdir / "emu_flash.bin").write_bytes(original)
    source = tmp_path / "source"
    source.mkdir()
    (source / "hello.txt").write_text("selected partition\n")

    out = tmp_path / "selected.bin"
    emu.make_flash(project, source, out, fs_partition="fixtures")
    image = out.read_bytes()
    offset, size = 0x60000, 0x10000
    assert image[:offset] == original[:offset]
    assert image[offset + size:] == original[offset + size:]
    assert read_fixture_file(image[offset:offset + size], size, "/hello.txt") == (
        b"selected partition\n"
    )
    assert (bdir / "emu_flash.bin").read_bytes() == original


def test_make_flash_reports_missing_base_and_partition(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(emu, "build_dir", lambda project: tmp_path)
    with pytest.raises(SystemExit) as missing_base:
        emu.make_flash(PROJECT, tmp_path, tmp_path / "out.bin")
    assert missing_base.value.code == 2
    assert "emu_flash.bin missing; run `emu.py image` first" in capsys.readouterr().err

    (tmp_path / "emu_flash.bin").write_bytes(b"base")
    with pytest.raises(SystemExit) as missing_partition:
        emu.make_flash(PROJECT, tmp_path, tmp_path / "out.bin", fs_partition="absent")
    assert missing_partition.value.code == 2
    assert "no partition named 'absent' in partitions.csv" in capsys.readouterr().err
