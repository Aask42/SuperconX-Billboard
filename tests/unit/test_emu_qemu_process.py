"""Host-only tests of the QEMU wrapper using real, short-lived fake children."""

import sys
import threading
import time

import pytest

import emu


@pytest.fixture
def fake_qemu(monkeypatch, tmp_path):
    children = []

    def start(program, **kwargs):
        monkeypatch.setattr(
            emu, "qemu_args", lambda *args: [sys.executable, "-u", "-c", program]
        )
        child = emu.Qemu(tmp_path, echo=False, **kwargs)
        children.append(child)
        return child

    yield start

    for child in children:
        child.close()
        child.proc.wait(timeout=5)


def test_expect_spans_reads_and_decodes_split_utf8(fake_qemu):
    output = []
    received = threading.Event()

    def on_output(text):
        output.append(text)
        if "DONE\n" in "".join(output):
            received.set()

    q = fake_qemu(
        """
import os
os.write(1, b'BOOT prefix\\xe2\\x82')
os.read(0, 1)
os.write(1, b'\\xac suffix\\nDONE\\n')
os.read(0, 1)
""",
        on_output=on_output,
    )
    q.expect("BOOT ", 2)
    q.send("!")
    assert q.expect("prefix€ suffix", 2).group() == "prefix€ suffix"
    q.expect("DONE", 2)
    assert received.wait(2)
    assert "".join(output) == "BOOT prefix€ suffix\nDONE\n"

    started = time.monotonic()
    with pytest.raises(AssertionError, match=r"timeout .* waiting for /BOOT/"):
        q.expect("BOOT", 0.12)
    assert 0.1 <= time.monotonic() - started < 2


def test_send_writes_exact_utf8_bytes_without_a_line_ending(fake_qemu):
    q = fake_qemu(
        """
import os, select, time
data = os.read(0, 1024)
time.sleep(0.1)
while select.select([0], [], [], 0)[0]:
    more = os.read(0, 1024)
    if not more:
        break
    data += more
os.write(1, b'HEX:' + data.hex().encode() + b'\\n')
os.read(0, 1)
"""
    )
    q.send("hé")
    assert q.expect(r"HEX:68c3a9\n", 2).group() == "HEX:68c3a9\n"


def test_reject_checks_even_output_already_consumed_by_expect(fake_qemu):
    q = fake_qemu(
        """
import os
os.write(1, b'fatal 42\\nREADY\\n')
os.read(0, 1)
"""
    )
    q.expect("READY", 2)
    q.reject("not present")
    with pytest.raises(AssertionError) as error:
        q.reject(r"fatal \d+")
    assert str(error.value) == r"forbidden output matched /fatal \d+/: 'fatal 42'"


def test_reboot_loop_is_detected_before_timeout(fake_qemu):
    q = fake_qemu(
        """
import os
os.write(1, b'ESP-ROM: first\\nESP-ROM: second\\n')
os.read(0, 1)
"""
    )
    with pytest.raises(AssertionError, match=r"firmware rebooted \(2 boots\)"):
        q.expect("never arrives", 2)

    q.max_boots = 2
    with pytest.raises(AssertionError, match="timeout"):
        q.expect("never arrives", 0.12)


def test_dead_child_is_reported_and_close_returns_promptly(fake_qemu):
    q = fake_qemu("raise SystemExit(23)")
    assert q.proc.wait(timeout=2) == 23
    with pytest.raises(AssertionError, match=r"QEMU exited \(23\)"):
        q.expect("never arrives", 2)

    started = time.monotonic()
    q.close()
    assert time.monotonic() - started < 1
    assert q.proc.returncode == 23


def test_close_terminates_a_running_child(fake_qemu):
    q = fake_qemu(
        """
import os
os.write(1, b'READY\\n')
os.read(0, 1)
"""
    )
    q.expect("READY", 2)
    assert q.proc.poll() is None
    q.close()
    assert q.proc.poll() is not None
    q.close()


def test_output_callback_works_without_retaining_a_serial_buffer(fake_qemu):
    received = threading.Event()
    output = []

    def on_output(text):
        output.append(text)
        if "READY\n" in "".join(output):
            received.set()

    q = fake_qemu(
        """
import os
os.write(1, b'READY\\n')
os.read(0, 1)
""",
        keep=False,
        on_output=on_output,
    )
    assert received.wait(2)
    with q.lock:
        assert q.buf == ""
    with pytest.raises(AssertionError, match="timeout"):
        q.expect("READY", 0.12)


def test_qemu_args_forward_flash_snapshot_serial_and_gdb(monkeypatch, tmp_path):
    monkeypatch.setattr(emu, "qemu_bin", lambda: "fake-qemu")
    monkeypatch.setattr(emu, "efuse_device", lambda _: "nvram.esp32s3.efuse")
    image = tmp_path / "isolated.bin"

    args = emu.qemu_args(tmp_path, "stdio", 3456, flash=image, snapshot=True)
    assert args[:5] == ["fake-qemu", "-M", "esp32s3", "-m", "8M"]
    assert "driver=ssi_psram,property=is_octal,value=true" in args
    assert f"file={image},if=mtd,format=raw,snapshot=on" in args
    assert f"file={tmp_path / 'emu_efuse.bin'},if=none,format=raw,id=efuse" in args
    assert args[args.index("-serial") + 1] == "stdio"
    assert args[args.index("-monitor") + 1] == "none"
    assert args[args.index("-gdb") + 1] == "tcp::3456"
    assert args[args.index("-nic") + 1] == "none"
    assert args[args.index("-display") + 1] == "none"

    interactive = emu.qemu_args(tmp_path, "mon:stdio")
    assert f"file={tmp_path / 'emu_flash.bin'},if=mtd,format=raw" in interactive
    assert "-monitor" not in interactive
    assert "-gdb" not in interactive
