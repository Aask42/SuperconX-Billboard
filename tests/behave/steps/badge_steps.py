"""Step definitions mapping Gherkin to the emulator (emu.Qemu expect/send + gdb
framebuffer dump). Patterns are treated as regexes against the serial output.

Fixes from Copilot's validation (buffer turn 3):
  - interaction scenarios must wait for EMU_READY before sending input, else the
    @btn line is dropped during boot;
  - the refresh assertion polls to a deadline instead of reading once (racy);
  - LED check accepts the 'EMU_LED fill r g b' form;
  - the screenshot step fails on a blank (inkless) screen.
"""
import re
import time

from behave import given, when, then

BUTTONS = ("menu_minus", "menu_plus", "a", "b")


@given("the badge firmware is running in the emulator")
def step_running(context):
    assert context.qemu is not None, "QEMU was not started (scenario missing @emu?)"


@given("the badge firmware has booted")
def step_booted(context):
    # Wait for the console-ready marker before any input is sent (contract item 5).
    assert context.qemu is not None, "QEMU was not started (scenario missing @emu?)"
    context.qemu.expect(r"EMU_READY", 40)


@then('I should see "{pattern}" within {timeout:d} seconds')
@then('"{pattern}" appears within {timeout:d} seconds')
def step_expect(context, pattern, timeout):
    context.qemu.expect(pattern, timeout)


@then('I should not see "{pattern}"')
def step_reject(context, pattern):
    context.qemu.reject(pattern)


@when('I press the {button} button')
def step_button(context, button):
    assert button in BUTTONS, f"unknown button {button!r}; expected one of {BUTTONS}"
    # Emulator button contract (T55): a UART line '@btn <name>'.
    context.qemu.send(f"@btn {button}\r")


@when('I type "{text}" on the serial console')
def step_type(context, text):
    context.qemu.send(text + "\r")


@when("I wait {seconds:g} seconds")
def step_wait(context, seconds):
    time.sleep(seconds)


def _max_refreshes(buf):
    seen = [int(m.group(1)) for m in re.finditer(r"EMU_EPD_REFRESH (\d+)", buf)]
    return max(seen) if seen else 0


@then("at least {n:d} e-paper refresh has occurred")
@then("at least {n:d} e-paper refreshes have occurred")
def step_refreshes(context, n, deadline_s=10.0):
    # Poll: the refresh happens asynchronously after the triggering input.
    end = time.monotonic() + deadline_s
    got = _max_refreshes(context.qemu.buf)
    while got < n and time.monotonic() < end:
        time.sleep(0.1)
        got = _max_refreshes(context.qemu.buf)
    assert got >= n, f"expected >= {n} e-paper refreshes, saw {got}"


@then("LED {index:d} is lit")
def step_led_lit(context, index):
    # 'EMU_LED <i> r g b' (one LED) or 'EMU_LED fill r g b' (all LEDs).
    lit = False
    for m in re.finditer(r"EMU_LED (\d+|fill) (\d+) (\d+) (\d+)", context.qemu.buf):
        which = m.group(1)
        if (which == "fill" or int(which) == index) and any(int(m.group(g)) for g in (2, 3, 4)):
            lit = True
    assert lit, f"LED {index} was never lit (no matching EMU_LED line)"


def _plane_bit(buf, x, y):
    off = y * (960 // 8) + (x >> 3)
    return 1 if (buf[off] & (0x80 >> (x & 7))) else 0


@then('the panel pixel at {x:d},{y:d} is {color}')
def step_pixel(context, x, y, color):
    assert context.gdb_port, "scenario needs the @fbdump tag to open a gdb stub"
    black, red = context.emu.read_planes(
        context.build_dir, context.gdb_port, "badge_emu_fb_black", "badge_emu_fb_red"
    )
    b, r = _plane_bit(black, x, y), _plane_bit(red, x, y)
    got = "red" if r else ("black" if b else "white")
    assert got == color, f"pixel ({x},{y}) is {got}, expected {color}"


@then('the e-paper screen is saved to "{name}"')
def step_fbdump(context, name):
    assert context.gdb_port, "scenario needs the @fbdump tag to open a gdb stub"
    out = context.build_dir / name
    black, red = context.emu.read_planes(
        context.build_dir, context.gdb_port, "badge_emu_fb_black", "badge_emu_fb_red"
    )
    # A dump of a blank panel is almost always a bug, not a pass: require ink.
    assert any(black) or any(red), "framebuffer is blank (no black or red pixels set)"
    context.emu.write_png(out, black, red)
    assert out.exists() and out.stat().st_size > 0, f"framebuffer dump {out} is empty"
