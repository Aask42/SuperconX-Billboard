"""Exercise scenario files without an ESP-IDF build or a QEMU executable."""

import sys
from types import SimpleNamespace

import pytest

import emu


@pytest.fixture
def scenario_args(monkeypatch, tmp_path):
    monkeypatch.setattr(emu, "cmd_image", lambda args: None)
    monkeypatch.setattr(emu, "build_dir", lambda project: tmp_path)

    def make(scenario, quiet=True):
        return SimpleNamespace(
            project=tmp_path,
            scenario=scenario,
            quiet=quiet,
            black="default_black",
            red="default_red",
        )

    return make


def test_scenario_dispatches_all_directives(
    monkeypatch, tmp_path, scenario_args, capsys
):
    scenario = tmp_path / "grammar.scn"
    scenario.write_text(
        r"""# A scenario comment and an empty line

boots 3
  expect 1.25 serial .+
reject PANIC .*
send line\nnext\tcolumn # literal text
sendraw raw\r
button menu_minus
button menu_plus
button a
button b
sleep 0.125
fbdump default.png
fbdump images/custom.png custom_black custom_red
"""
    )
    events = []

    class RecordingQemu:
        def __init__(self, bdir, port, echo, log):
            events.append(("start", bdir, port, echo, log))
            self.max_boots = 1

        def expect(self, pattern, timeout):
            events.append(("expect", pattern, timeout, self.max_boots))

        def reject(self, pattern):
            events.append(("reject", pattern))

        def send(self, text):
            events.append(("send", text))

        def close(self):
            events.append(("close",))

    def read_planes(bdir, port, black, red):
        events.append(("read_planes", bdir, port, black, red))
        return b"black", b"red"

    def write_png(path, black, red):
        events.append(("write_png", path, black, red))

    monkeypatch.setattr(emu, "Qemu", RecordingQemu)
    monkeypatch.setattr(emu, "free_port", lambda: 3456)
    monkeypatch.setattr(emu, "read_planes", read_planes)
    monkeypatch.setattr(emu, "write_png", write_png)
    monkeypatch.setattr(
        emu, "time", SimpleNamespace(sleep=lambda delay: events.append(("sleep", delay)))
    )

    emu.cmd_test(scenario_args(scenario))
    assert events == [
        ("start", tmp_path, 3456, False, tmp_path / "grammar.log"),
        ("expect", "serial .+", 1.25, 3),
        ("reject", "PANIC .*"),
        ("send", "line\nnext\tcolumn # literal text\r"),
        ("send", "raw\r"),
        ("send", "@btn menu_minus\r"),
        ("send", "@btn menu_plus\r"),
        ("send", "@btn a\r"),
        ("send", "@btn b\r"),
        ("sleep", 0.125),
        ("read_planes", tmp_path, 3456, "default_black", "default_red"),
        ("write_png", tmp_path / "default.png", b"black", b"red"),
        ("read_planes", tmp_path, 3456, "custom_black", "custom_red"),
        ("write_png", tmp_path / "images" / "custom.png", b"black", b"red"),
        ("close",),
    ]
    assert "emu: PASS grammar.scn" in capsys.readouterr().out


def test_scenario_send_and_button_reach_a_real_fake_serial_process(
    monkeypatch, tmp_path, scenario_args, capsys
):
    scenario = tmp_path / "serial.scn"
    scenario.write_text(
        r"""# The fake replies only after receiving a carriage return.
expect 2 READY
send Hi\tbadge
expect 2 RX:4869096261646765
sendraw raw
sendraw \r
expect 2 RX:726177
button menu_plus
expect 2 RX:4062746e206d656e755f706c7573
sleep 0
reject PANIC
"""
    )
    program = """
import os
os.write(1, b'ESP-ROM: fake\\nREADY\\n')
line = bytearray()
while True:
    ch = os.read(0, 1)
    if not ch:
        break
    if ch == b'\\r':
        os.write(1, b'RX:' + line.hex().encode() + b'\\n')
        line.clear()
    else:
        line.extend(ch)
"""
    monkeypatch.setattr(
        emu, "qemu_args", lambda *args: [sys.executable, "-u", "-c", program]
    )

    emu.cmd_test(scenario_args(scenario))
    output = capsys.readouterr()
    assert "emu: PASS serial.scn" in output.out
    assert output.err == ""


@pytest.mark.parametrize(
    ("payload", "steps", "line", "reason"),
    [
        (b"READY\n", "expect 0.12 absent\n", 2, "timeout 0.12s waiting for /absent/"),
        (
            b"FORBIDDEN\nREADY\n",
            "expect 2 READY\nreject FORBIDDEN\n",
            3,
            "forbidden output matched /FORBIDDEN/",
        ),
        (
            b"ESP-ROM: one\nESP-ROM: two\n",
            "expect 2 absent\n",
            2,
            "firmware rebooted (2 boots)",
        ),
        (b"", "expectt 1 READY\n", 2, "unknown step 'expectt'"),
        (b"", "button menu_middle\n", 2, "unknown button 'menu_middle'"),
    ],
)
def test_bad_scenarios_fail_with_the_correct_reason(
    monkeypatch, tmp_path, scenario_args, capsys, payload, steps, line, reason
):
    scenario = tmp_path / "broken.scn"
    scenario.write_text("# preceding comment\n" + steps)
    program = f"import os; os.write(1, {payload!r}); os.read(0, 1)"
    monkeypatch.setattr(
        emu, "qemu_args", lambda *args: [sys.executable, "-u", "-c", program]
    )

    with pytest.raises(SystemExit) as error:
        emu.cmd_test(scenario_args(scenario))
    output = capsys.readouterr()
    # The documented contract is exit 1 for every scenario failure; stderr
    # distinguishes a timeout, forbidden output, reboot, and grammar errors.
    assert error.value.code == 1
    assert f"emu: FAIL broken.scn:{line}: " in output.err
    assert reason in output.err
    assert "emu: PASS" not in output.out
