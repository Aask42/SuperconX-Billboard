"""Golden-sequence test for the e-paper driver (issue #4).

Compiles the SHIPPED sequence (native_firmware/components/epd/src/epd_seq.c) and
drives it through a capture HAL, asserting the command order, data, delays and
the shutdown pin states match the stock epd.py reference. This exercises the real
driver code path, not a model of it.
"""
import ctypes
import shutil
import subprocess
from pathlib import Path

import pytest

from support.epd_sequence import build_reference, BOTH, LEFT, RIGHT, IMAGE_LEN_THRESHOLD

REPO = Path(__file__).resolve().parents[2]
EPD = REPO / "native_firmware" / "components" / "epd"
HALF = 40320
captured_cbs = []  # keep ctypes callbacks + hal struct alive for the test session


CMD_T = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_uint8, ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t)
DC_T = ctypes.CFUNCTYPE(None, ctypes.c_int)
CS_T = ctypes.CFUNCTYPE(None, ctypes.c_int, ctypes.c_int)
RST_T = ctypes.CFUNCTYPE(None, ctypes.c_int)
PWR_T = ctypes.CFUNCTYPE(None, ctypes.c_int)
DELAY_T = ctypes.CFUNCTYPE(None, ctypes.c_uint)
BUSY_T = ctypes.CFUNCTYPE(None)


class _HAL(ctypes.Structure):
    _fields_ = [
        ("cmd", CMD_T), ("dc", DC_T), ("cs", CS_T), ("rst", RST_T),
        ("pwr", PWR_T), ("delay_ms", DELAY_T), ("wait_busy", BUSY_T),
    ]


@pytest.fixture(scope="module")
def captured():
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if cc is None:
        pytest.skip("no C compiler")
    src = EPD / "src" / "epd_seq.c"
    if not src.exists():
        pytest.skip("epd_seq.c not found")
    out = Path(subprocess.run(["mktemp", "-d"], capture_output=True, text=True).stdout.strip()) / "libepdseq.so"
    subprocess.run([cc, "-shared", "-fPIC", "-O2", "-std=c99",
                    "-I", str(EPD / "include"), str(src), "-o", str(out)], check=True)
    lib = ctypes.CDLL(str(out))

    ops = []
    # Keep Python callback objects alive for the lifetime of the test.
    cbs = {}

    def on_cmd(target, index, data, length):
        if length > IMAGE_LEN_THRESHOLD:
            ops.append(("cmd", int(target), int(index), ("LEN", int(length))))
        else:
            ops.append(("cmd", int(target), int(index), tuple(data[i] for i in range(length))))
    def on_dc(level): ops.append(("dc", int(level)))
    def on_cs(line, level): ops.append(("cs", int(line), int(level)))
    def on_rst(level): ops.append(("rst", int(level)))
    def on_pwr(on): ops.append(("pwr", int(on)))
    def on_delay(ms): ops.append(("delay", int(ms)))
    def on_busy(): ops.append(("busy",))

    hal = _HAL()
    cbs["cmd"] = CMD_T(on_cmd); hal.cmd = cbs["cmd"]
    cbs["dc"] = DC_T(on_dc); hal.dc = cbs["dc"]
    cbs["cs"] = CS_T(on_cs); hal.cs = cbs["cs"]
    cbs["rst"] = RST_T(on_rst); hal.rst = cbs["rst"]
    cbs["pwr"] = PWR_T(on_pwr); hal.pwr = cbs["pwr"]
    cbs["delay"] = DELAY_T(on_delay); hal.delay_ms = cbs["delay"]
    cbs["busy"] = BUSY_T(on_busy); hal.wait_busy = cbs["busy"]
    captured_cbs.append((hal, cbs))

    lib.epd_seq_set_hal(ctypes.byref(hal))
    zero = (ctypes.c_uint8 * HALF)()
    lib.epd_seq_refresh.argtypes = [ctypes.POINTER(ctypes.c_uint8)] * 4 + [ctypes.c_size_t, ctypes.c_int]
    lib.epd_seq_refresh(zero, zero, zero, zero, HALF, 1)
    return ops


def test_transcript_matches_stock_epd_py(captured):
    assert captured == build_reference(HALF)


def test_images_sent_before_cog_init(captured):
    cmds = [o for o in captured if o[0] == "cmd"]
    first_image = next(i for i, o in enumerate(cmds) if o[3] == ("LEN", HALF))
    first_cog = next(i for i, o in enumerate(cmds) if o[2] == 0x05)  # cog_initial's first cmd
    assert first_image < first_cog, "stock uploads images before CoG init; order changed"


def test_dcdc_ramp_values_present_in_order(captured):
    ramp = [o[3] for o in captured if o[0] == "cmd" and o[2] == 0x51]
    assert ramp[0] == (0x50, 0x01)
    assert (0x50, 0x04) in ramp and (0x0A, 0x0A) in ramp
    assert ramp[-1] == (0x02, 0x0A)  # last step of the final descending loop


def test_shutdown_leaves_documented_pin_states(captured):
    # Tail of the stream is the shutdown pin choreography.
    tail = [o for o in captured if o[0] in ("dc", "cs", "rst")][-4:]
    assert tail == [("dc", 0), ("cs", 1, 0), ("rst", 0), ("cs", 1, 1)]
    # DOCUMENTED stock behaviour / known deviations for hardware bring-up:
    #  - final CS1 = high, RST = low, DC = low;
    #  - CS2 is left as the last image/cog write set it (high), not re-cleared;
    #  - panel power (GPIO48) is NOT turned off here (epd.py never toggles it).
    assert not any(o[0] == "pwr" for o in captured), "seq must not touch power rail"
