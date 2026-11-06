"""Step definitions mapping Gherkin to the emulator (emu.Qemu expect/send + gdb
framebuffer dump). Patterns are treated as regexes against the serial output."""
import re
import time

from behave import given, when, then

BUTTONS = ("menu_minus", "menu_plus", "a", "b")


@given("the badge firmware is running in the emulator")
def step_running(context):
    assert context.qemu is not None, "QEMU was not started (scenario missing @emu?)"


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


@then("at least {n:d} e-paper refresh has occurred")
@then("at least {n:d} e-paper refreshes have occurred")
def step_refreshes(context, n):
    # Emulator display contract (T55): firmware logs 'EMU_EPD_REFRESH <count>'.
    seen = [int(m.group(1)) for m in re.finditer(r"EMU_EPD_REFRESH (\d+)", context.qemu.buf)]
    got = max(seen) if seen else 0
    assert got >= n, f"expected >= {n} e-paper refreshes, saw {got}"


@then('LED {index:d} is lit')
def step_led_lit(context, index):
    # 'EMU_LED <i> <r> <g> <b>' with any non-zero channel.
    lit = False
    for m in re.finditer(r"EMU_LED (\d+) (\d+) (\d+) (\d+)", context.qemu.buf):
        if int(m.group(1)) == index and any(int(m.group(g)) for g in (2, 3, 4)):
            lit = True
    assert lit, f"LED {index} was never lit (no matching EMU_LED line)"


@then('the e-paper screen is saved to "{name}"')
def step_fbdump(context, name):
    assert context.gdb_port, "scenario needs the @fbdump tag to open a gdb stub"
    out = context.build_dir / name
    black, red = context.emu.read_planes(
        context.build_dir, context.gdb_port, "badge_emu_fb_black", "badge_emu_fb_red"
    )
    context.emu.write_png(out, black, red)
    assert out.exists() and out.stat().st_size > 0, f"framebuffer dump {out} is empty"
