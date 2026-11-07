"""Reference op-stream for one e-paper global update, ported from badge_fs/lib/
epd.py. The golden-sequence test drives the shipped C (epd_seq.c) through a
capture HAL and asserts the transcript matches this, byte-for-byte for control
data (image planes are compared by length only).

Op tuples:
  ("cmd", target, index, data)   data = tuple(bytes) for control, ("LEN", n) for image
  ("dc", level) ("cs", line, level) ("rst", level) ("pwr", on)
  ("delay", ms) ("busy",)
target: 0=both, 1=left, 2=right
"""
from __future__ import annotations

BOTH, LEFT, RIGHT = 0, 1, 2
IMAGE_LEN_THRESHOLD = 16   # cmd data longer than this is recorded as ("LEN", n)


def build_reference(half: int = 40320):
    ops = []
    def cmd(t, index, data):
        if len(data) > IMAGE_LEN_THRESHOLD:
            ops.append(("cmd", t, index, ("LEN", len(data))))
        else:
            ops.append(("cmd", t, index, tuple(data)))
    def img(t, index):
        ops.append(("cmd", t, index, ("LEN", half)))
    def delay(ms): ops.append(("delay", ms))
    def busy(): ops.append(("busy",))

    # reset()
    delay(200); ops.append(("rst", 1)); delay(20); ops.append(("rst", 0))
    delay(200); ops.append(("rst", 1)); delay(200)
    ops.append(("cs", 1, 1)); ops.append(("cs", 2, 1)); delay(5)

    # send_images()
    cmd(BOTH, 0x13, (0x00, 0x3B, 0x00, 0x00, 0x9F, 0x02))
    cmd(BOTH, 0x90, (0x00, 0x3B, 0x00, 0xA9))
    ram = (0x3B, 0x00, 0x14)
    cmd(LEFT, 0x12, ram); img(LEFT, 0x10); cmd(LEFT, 0x12, ram); img(LEFT, 0x11)
    cmd(RIGHT, 0x12, ram); img(RIGHT, 0x10); cmd(RIGHT, 0x12, ram); img(RIGHT, 0x11)

    # cog_initial()
    cmd(BOTH, 0x05, (0x7D,)); delay(200)
    cmd(BOTH, 0x05, (0x00,)); delay(10)
    cmd(BOTH, 0xD8, (0x80,)); cmd(BOTH, 0xD6, (0x00,))
    cmd(BOTH, 0xA7, (0x10,)); delay(100); cmd(BOTH, 0xA7, (0x00,)); delay(100)
    cmd(BOTH, 0x03, (0x00, 0x11))
    for t in (LEFT, RIGHT):
        cmd(t, 0x44, (0x00,)); cmd(t, 0x45, (0x80,))
        cmd(t, 0xA7, (0x10,)); delay(100); cmd(t, 0xA7, (0x00,)); delay(100)
        cmd(t, 0x44, (0x06,)); cmd(t, 0x45, (0x82,))
        cmd(t, 0xA7, (0x10,)); delay(100); cmd(t, 0xA7, (0x00,)); delay(100)
    cmd(BOTH, 0x60, (0x25,)); cmd(LEFT, 0x61, (0x01,)); cmd(BOTH, 0x02, (0x00,))

    # dcdc_soft_start()
    cmd(BOTH, 0x51, (0x50, 0x01))
    for v in range(1, 5):
        cmd(BOTH, 0x09, (0x1F,)); cmd(BOTH, 0x51, (0x50, v)); cmd(BOTH, 0x09, (0x9F,)); delay(2)
    for v in range(1, 11):
        cmd(BOTH, 0x09, (0x1F,)); cmd(BOTH, 0x51, (0x0A, v)); cmd(BOTH, 0x09, (0x9F,)); delay(2)
    for v in range(3, 11):
        cmd(BOTH, 0x09, (0x7F,)); cmd(BOTH, 0x51, (0x0A, v)); cmd(BOTH, 0x09, (0xFF,)); delay(2)
    for v in range(9, 1, -1):
        cmd(BOTH, 0x09, (0x7F,)); cmd(BOTH, 0x51, (v, 0x0A)); cmd(BOTH, 0x09, (0xFF,)); delay(2)
    cmd(BOTH, 0x09, (0xFF,)); delay(10)

    # display_refresh()
    busy(); cmd(BOTH, 0x15, (0x3C,)); delay(5)

    # dcdc_soft_shutdown()
    busy()
    cmd(BOTH, 0x09, (0x7F,)); cmd(BOTH, 0x05, (0x7D,)); cmd(BOTH, 0x09, (0x00,))
    delay(200); busy()
    ops.append(("dc", 0)); ops.append(("cs", 1, 0)); ops.append(("rst", 0)); ops.append(("cs", 1, 1))
    # CS2 parked high explicitly (stock leaves it high after the last command;
    # the driver now asserts it rather than relying on the cmd() HAL). PWR_EN is
    # deliberately left powered — stock does not gate the panel rail on shutdown.
    ops.append(("cs", 2, 1))
    return ops
