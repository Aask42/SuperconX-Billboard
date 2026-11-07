"""behave <-> emulator glue.

Reuses Copilot's emulator/emu.py: builds + images the firmware once per run, then
hands each @emu scenario a fresh QEMU instance (emu.Qemu) with expect/send. If
ESP-IDF or the Espressif QEMU isn't available, @emu scenarios are skipped with a
clear reason rather than failing (so host-only CI stays green).

Run from this directory inside the IDF env:
    source ~/esp/esp-idf-v5.4.2/export.sh
    behave                     # implemented scenarios
    behave -t @wip             # the not-yet-built specs
    EMU_SKIP_BUILD=1 behave    # reuse the last emulator build
"""
import os
import sys
import time
from argparse import Namespace
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "emulator"))
import emu  # noqa: E402

PROJECT = REPO / "native_firmware"


def _availability():
    if not os.environ.get("IDF_PATH"):
        return False, "IDF_PATH not set (source export.sh)"
    try:
        emu.qemu_bin()
    except SystemExit:
        return False, "qemu-system-xtensa not found (see emulator/README.md)"
    return True, ""


def _image_args():
    return Namespace(project=str(PROJECT), fs=None, no_fs=False,
                     fs_partition="storage", black="badge_emu_fb_black",
                     red="badge_emu_fb_red", quiet=True)


def before_all(context):
    context.emu = emu
    context.project = PROJECT
    context.build_dir = emu.build_dir(PROJECT)
    context.emu_available, context.emu_reason = _availability()
    if not context.emu_available:
        return
    try:
        if os.environ.get("EMU_SKIP_BUILD") != "1":
            emu.cmd_build(Namespace(project=str(PROJECT), extra_defaults=[], clean_config=False))
        emu.cmd_image(_image_args())
    except Exception as exc:  # build/image failure -> skip, don't crash the run
        context.emu_available = False
        context.emu_reason = f"emulator build/image failed: {exc}"


def before_scenario(context, scenario):
    context.qemu = None
    context.gdb_port = None
    if "emu" not in scenario.effective_tags:
        return
    if not context.emu_available:
        scenario.skip(f"emulator unavailable: {context.emu_reason}")
        return
    context.gdb_port = emu.free_port() if "fbdump" in scenario.effective_tags else None
    safe = "".join(c if c.isalnum() else "_" for c in scenario.name)[:48]
    log = context.build_dir / f"behave_{safe}.log"
    context.qemu = emu.Qemu(
        context.build_dir, gdb_port=context.gdb_port, echo=False, log=log, snapshot=True
    )


def after_scenario(context, scenario):
    if getattr(context, "qemu", None) is not None:
        context.qemu.close()
        context.qemu = None
        # Let QEMU fully exit and free its resources before the next boot; the
        # Espressif 9.2.2 build occasionally SIGSEGVs when instances overlap.
        time.sleep(1.5)
