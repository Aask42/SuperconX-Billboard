"""Exercise framebuffer reads with a scripted GDB executable, not QEMU."""

import json

import pytest

import emu


FAKE_GDB = r"""
import json
import re
import sys
from pathlib import Path

args = sys.argv[1:]
elf = Path(args[args.index("-nx") + 1])
(elf.parent / "gdb-args.json").write_text(json.dumps(args))
commands = [args[i + 1] for i, arg in enumerate(args) if arg == "-ex"]
failed = False
for command in commands:
    match = re.fullmatch(
        r"dump binary memory (.+) \(([^()]*)\) \(\2\)\+(\d+)", command
    )
    if not match:
        continue
    path, symbol, size = match.groups()
    path, size = Path(path), int(size)
    if symbol == "fixture_black":
        path.write_bytes(b"\x35" * size)
    elif symbol == "fixture_red":
        path.write_bytes(b"\xc1" * size)
    elif symbol == "short_red":
        path.write_bytes(b"\xc1" * (size - 1))
    elif symbol == "error_after_dump":
        path.write_bytes(b"\xc1" * size)
        print("Target disconnected after dump.", file=sys.stderr)
        failed = True
    elif symbol == "red_not_created":
        print("Red dump was not created.", file=sys.stderr)
    else:
        path.write_bytes(b"")
        print(f'No symbol "{symbol}" in current context.', file=sys.stderr)
        failed = True
sys.exit(1 if failed else 0)
"""


@pytest.fixture
def fake_gdb(monkeypatch, tmp_path):
    executable = tmp_path / "fake-gdb"
    executable.write_text("#!/usr/bin/env python3\n" + FAKE_GDB)
    executable.chmod(0o755)
    elf = tmp_path / "fixture.elf"
    elf.write_bytes(b"fake ELF")
    monkeypatch.setattr(emu, "gdb_bin", lambda: str(executable))
    return tmp_path, elf


def test_read_planes_requests_each_symbol_and_returns_exact_bytes(fake_gdb):
    bdir, elf = fake_gdb
    black, red = emu.read_planes(bdir, 12345, "fixture_black", "fixture_red")
    assert black == b"\x35" * emu.PLANE_BYTES
    assert red == b"\xc1" * emu.PLANE_BYTES

    args = json.loads((bdir / "gdb-args.json").read_text())
    assert args == [
        "-q",
        "-batch",
        "-nx",
        str(elf),
        "-ex",
        "set pagination off",
        "-ex",
        "target remote 127.0.0.1:12345",
        "-ex",
        f"dump binary memory {bdir / 'emu_black.bin'} "
        f"(fixture_black) (fixture_black)+{emu.PLANE_BYTES}",
        "-ex",
        f"dump binary memory {bdir / 'emu_red.bin'} "
        f"(fixture_red) (fixture_red)+{emu.PLANE_BYTES}",
        "-ex",
        "detach",
    ]


@pytest.mark.parametrize(
    ("black_symbol", "red_symbol", "detail"),
    [
        ("fixture_black", "missing_red", 'No symbol "missing_red" in current context.'),
        ("missing_black", "fixture_red", 'No symbol "missing_black" in current context.'),
        ("fixture_black", "short_red", "80639"),
        ("fixture_black", "error_after_dump", "Target disconnected after dump."),
        ("fixture_black", "red_not_created", "Red dump was not created."),
    ],
)
def test_read_planes_rejects_gdb_errors_and_incomplete_dumps(
    fake_gdb, capsys, black_symbol, red_symbol, detail
):
    bdir, _ = fake_gdb
    # Leftover files must not make a failed GDB read appear successful.
    (bdir / "emu_black.bin").write_bytes(b"old black")
    (bdir / "emu_red.bin").write_bytes(b"old red")

    with pytest.raises(SystemExit) as error:
        emu.read_planes(bdir, 12345, black_symbol, red_symbol)
    assert error.value.code == 2
    diagnostics = capsys.readouterr().err
    assert f"gdb could not read planes ({black_symbol}, {red_symbol})" in diagnostics
    assert detail in diagnostics
    if red_symbol == "red_not_created":
        assert not (bdir / "emu_red.bin").exists()


def test_read_planes_reports_missing_elf_without_running_gdb(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setattr(emu, "gdb_bin", lambda: pytest.fail("unexpected GDB invocation"))
    with pytest.raises(SystemExit) as error:
        emu.read_planes(tmp_path, 12345, "black", "red")
    assert error.value.code == 2
    assert f"no ELF in {tmp_path}" in capsys.readouterr().err
