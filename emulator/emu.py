#!/usr/bin/env python3
"""QEMU emulator harness for the SuperconX Billboard native firmware.

Run inside the ESP-IDF environment (`source ~/esp/esp-idf-v5.4.2/export.sh`).

  emu.py build   [--project DIR]            idf.py build with the emulator overlay
  emu.py image   [--project DIR] [--fs DIR] merge app + optional LittleFS into a flash image
  emu.py run     [--project DIR]            interactive serial console (Ctrl-A X quits)
  emu.py test    SCENARIO [--project DIR]   scripted boot/expect/send test, exit 0 on pass
  emu.py fbdump  OUT.png [--project DIR]    boot, wait, then dump the e-paper planes to PNG

Builds go to emulator/out/<project-name>/ so the hardware build/ dir is never touched.
"""
from __future__ import annotations

import argparse
import codecs
import csv
import os
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
DEFAULT_PROJECT = REPO / 'native_firmware'
OVERLAY = HERE / 'sdkconfig.emulator'
FLASH_SIZE = '8MB'
PSRAM = '8M'  # badge: 8 MB octal (needs Espressif QEMU >= 9.2.2)
EPD_W, EPD_H = 960, 672
PLANE_BYTES = EPD_W // 8 * EPD_H
BUTTONS = ('menu_minus', 'menu_plus', 'a', 'b')


def die(msg: str) -> None:
    print(f'emu: {msg}', file=sys.stderr)
    sys.exit(2)


def idf_path() -> Path:
    p = os.environ.get('IDF_PATH')
    if not p:
        die('IDF_PATH not set; run `source ~/esp/esp-idf-v5.4.2/export.sh` first')
    return Path(p)


def build_dir(project: Path) -> Path:
    return HERE / 'out' / project.name


def qemu_bin() -> str:
    """$EMU_QEMU, else the newest Espressif QEMU under ~/.espressif, else PATH."""
    if os.environ.get('EMU_QEMU'):
        return os.environ['EMU_QEMU']
    found = sorted(Path.home().glob('.espressif/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa'))
    if found:
        return str(found[-1])
    q = shutil.which('qemu-system-xtensa')
    if not q:
        die('qemu-system-xtensa not found; see emulator/README.md')
    return q


def efuse_device(qemu: str) -> str:
    # Older builds only had the C3 efuse model wired to the S3 machine (QEMU-201).
    return 'nvram.esp32s3.efuse' if b'nvram.esp32s3.efuse' in Path(qemu).read_bytes() else 'nvram.esp32c3.efuse'


def gdb_bin() -> str:
    g = shutil.which('xtensa-esp32s3-elf-gdb')
    if not g:
        die('xtensa-esp32s3-elf-gdb not on PATH (export.sh adds it)')
    return g


# ---------------------------------------------------------------- build

def cmd_build(args) -> None:
    project = Path(args.project).resolve()
    bdir = build_dir(project)
    bdir.mkdir(parents=True, exist_ok=True)
    defaults = [project / 'sdkconfig.defaults', OVERLAY] + [Path(x).resolve() for x in args.extra_defaults]
    defaults = [d for d in defaults if d.exists()]
    sdkconfig = bdir / 'sdkconfig'
    if args.clean_config and sdkconfig.exists():
        sdkconfig.unlink()
    cmd = ['idf.py', '-C', str(project), '-B', str(bdir),
           '-D', f'SDKCONFIG={sdkconfig}',
           '-D', 'SDKCONFIG_DEFAULTS=' + ';'.join(str(d) for d in defaults),
           'build']
    print('+', shlex.join(cmd))
    subprocess.run(cmd, check=True)
    check_config(sdkconfig)


def check_config(sdkconfig: Path) -> None:
    text = sdkconfig.read_text()
    problems = []
    if 'CONFIG_ESPTOOLPY_FLASHMODE_DIO=y' not in text:
        problems.append('flash mode is not DIO (QEMU boot-loops on QIO)')
    if 'CONFIG_ESP_CONSOLE_UART_DEFAULT=y' not in text:
        problems.append('console is not UART0 (QEMU has no USB-Serial/JTAG)')
    if 'CONFIG_BADGE_EMULATOR=y' not in text:
        problems.append('CONFIG_BADGE_EMULATOR missing: project has no Kconfig symbol for it, '
                        'so firmware cannot skip real-hardware waits')
    for p in problems:
        print(f'emu: warning: {p}', file=sys.stderr)


# ---------------------------------------------------------------- image

def partitions(project: Path, bdir: Path) -> list[dict]:
    csv_path = project / 'partitions.csv'
    rows = []
    offset = 0x9000
    for raw in csv.reader(l for l in csv_path.read_text().splitlines() if l.strip() and not l.lstrip().startswith('#')):
        cells = [c.strip() for c in raw]
        name, ptype, subtype = cells[0], cells[1], cells[2]
        off = int(cells[3], 0) if len(cells) > 3 and cells[3] else offset
        size = cells[4]
        mult = 1
        if size[-1] in 'Kk':
            mult, size = 1024, size[:-1]
        elif size[-1] in 'Mm':
            mult, size = 1024 * 1024, size[:-1]
        size_b = int(size, 0) * mult
        rows.append(dict(name=name, type=ptype, subtype=subtype, offset=off, size=size_b))
        offset = off + size_b
    return rows


def make_littlefs(src: Path, size: int, out: Path) -> None:
    try:
        from littlefs import LittleFS  # type: ignore
    except ImportError:
        die('littlefs-python missing: `python -m pip install littlefs-python`')
    block = 4096
    # Match joltwallet/littlefs defaults (CONFIG_LITTLEFS_*_SIZE=128, block_cycles=512).
    fs = LittleFS(block_size=block, block_count=size // block, read_size=128, prog_size=128,
                  lookahead_size=128, block_cycles=512)
    for path in sorted(src.rglob('*')):
        rel = '/' + path.relative_to(src).as_posix()
        if path.is_dir():
            fs.makedirs(rel, exist_ok=True)
        else:
            with fs.open(rel, 'wb') as fh:
                fh.write(path.read_bytes())
    out.write_bytes(fs.context.buffer)


def default_efuse() -> bytes:
    sys.path.insert(0, str(idf_path() / 'tools'))
    try:
        from idf_py_actions.qemu_ext import QEMU_TARGETS  # type: ignore
        return QEMU_TARGETS['esp32s3'].default_efuse
    except Exception:
        blob = bytearray(1024)
        blob[0x26] = 0x0C
        return bytes(blob)


def cmd_image(args) -> Path:
    project = Path(args.project).resolve()
    bdir = build_dir(project)
    if not (bdir / 'flash_args').exists():
        die(f'{bdir} has no flash_args; run `emu.py build` first')
    extra: list[str] = []
    fs_dir = Path(args.fs).resolve() if args.fs else None
    if fs_dir is None and (project / 'vfs_root' / 'data').is_dir():
        fs_dir = project / 'vfs_root' / 'data'
    if not args.no_fs:
        if fs_dir is None:
            empty = bdir / 'emu_fs_empty'
            empty.mkdir(exist_ok=True)
            fs_dir = empty
            print('emu: no vfs_root/data in project; packing an empty LittleFS (use --fs DIR for assets)')
        part = next((p for p in partitions(project, bdir) if p['name'] == args.fs_partition), None)
        if part is None:
            die(f'no partition named {args.fs_partition!r} in partitions.csv')
        fs_img = bdir / 'emu_fs.bin'
        make_littlefs(fs_dir, part['size'], fs_img)
        extra = [hex(part['offset']), str(fs_img)]
        print(f'emu: LittleFS {fs_dir} -> {args.fs_partition}@{hex(part["offset"])} ({part["size"]} bytes)')
    flash = bdir / 'emu_flash.bin'
    cmd = [sys.executable, '-m', 'esptool', '--chip', 'esp32s3', 'merge_bin', '--output', str(flash),
           '--fill-flash-size', FLASH_SIZE, '@flash_args', *extra]
    subprocess.run(cmd, check=True, cwd=bdir, stdout=subprocess.DEVNULL)
    efuse = bdir / 'emu_efuse.bin'
    if not efuse.exists():
        efuse.write_bytes(default_efuse())
    return flash


def make_flash(project: Path, fs_dir: Path, out: Path, fs_partition: str = 'storage') -> Path:
    """Copy the built emu_flash.bin to `out` with `fs_dir` packed into the LittleFS partition.

    For per-test filesystem fixtures (e.g. a doctored panel_config.json); the shared image is untouched.
    """
    project = Path(project).resolve()
    bdir = build_dir(project)
    base = bdir / 'emu_flash.bin'
    if not base.exists():
        die(f'{base} missing; run `emu.py image` first')
    part = next((p for p in partitions(project, bdir) if p['name'] == fs_partition), None)
    if part is None:
        die(f'no partition named {fs_partition!r} in partitions.csv')
    out = Path(out)
    fs_img = out.with_suffix('.fs.bin')
    make_littlefs(Path(fs_dir), part['size'], fs_img)
    img = bytearray(base.read_bytes())
    img[part['offset']:part['offset'] + part['size']] = fs_img.read_bytes()
    out.write_bytes(img)
    fs_img.unlink()
    return out


# ---------------------------------------------------------------- qemu

def qemu_args(bdir: Path, serial: str, gdb_port: int | None = None, flash: Path | None = None,
              snapshot: bool = False) -> list[str]:
    """snapshot=True discards the firmware's flash writes (NVS, LittleFS) when QEMU exits."""
    q = qemu_bin()
    drive = f'file={flash or bdir / "emu_flash.bin"},if=mtd,format=raw' + (',snapshot=on' if snapshot else '')
    args = [q, '-M', 'esp32s3', '-m', PSRAM,
            '-global', 'driver=ssi_psram,property=is_octal,value=true',
            '-drive', drive,
            '-drive', f'file={bdir / "emu_efuse.bin"},if=none,format=raw,id=efuse',
            '-global', f'driver={efuse_device(q)},property=drive,value=efuse',
            '-nic', 'none', '-display', 'none', '-serial', serial]
    if serial != 'mon:stdio':
        args += ['-monitor', 'none']
    if gdb_port:
        args += ['-gdb', f'tcp::{gdb_port}']
    return args


class Qemu:
    """QEMU with UART0 on a pipe; output is accumulated for expect()."""

    def __init__(self, bdir: Path, gdb_port: int | None = None, echo: bool = True, log: Path | None = None,
                 on_output=None, keep: bool = True, flash: Path | None = None, snapshot: bool = False):
        self.proc = subprocess.Popen(qemu_args(bdir, 'stdio', gdb_port, flash, snapshot), stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        self.buf = ''
        self.pos = 0
        self.lock = threading.Lock()
        self.echo = echo
        self.log = open(log, 'w') if log else None
        self.max_boots = 1
        self.on_output = on_output
        self.keep = keep
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        assert self.proc.stdout
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        while True:
            chunk = self.proc.stdout.read1(4096)
            if not chunk:
                return
            text = decoder.decode(chunk)
            if self.keep:
                with self.lock:
                    self.buf += text
            if self.on_output:
                self.on_output(text)
            if self.echo:
                sys.stdout.write(text)
                sys.stdout.flush()
            if self.log:
                self.log.write(text)
                self.log.flush()

    def expect(self, pattern: str, timeout: float) -> re.Match:
        rx = re.compile(pattern)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            with self.lock:
                m = rx.search(self.buf, self.pos)
                if m:
                    self.pos = m.end()
                    return m
                boots = self.buf.count('ESP-ROM:')
            if boots > self.max_boots:
                raise AssertionError(f'firmware rebooted ({boots} boots) while waiting for /{pattern}/; '
                                     'see serial log for the panic')
            if self.proc.poll() is not None:
                raise AssertionError(f'QEMU exited ({self.proc.returncode}) while waiting for /{pattern}/')
            time.sleep(0.05)
        raise AssertionError(f'timeout {timeout}s waiting for /{pattern}/')

    def reject(self, pattern: str) -> None:
        with self.lock:
            m = re.search(pattern, self.buf)
        if m:
            raise AssertionError(f'forbidden output matched /{pattern}/: {m.group(0)!r}')

    def send(self, text: str) -> None:
        assert self.proc.stdin
        self.proc.stdin.write(text.encode())
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        if self.log:
            self.log.close()


def cmd_run(args) -> None:
    project = Path(args.project).resolve()
    bdir = build_dir(project)
    cmd_image(args)
    a = qemu_args(bdir, 'mon:stdio', args.gdb_port)
    print('+', shlex.join(a))
    print('emu: Ctrl-A X to quit QEMU')
    subprocess.run(a)


# ---------------------------------------------------------------- framebuffer dump

def free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def read_planes(bdir: Path, port: int, black: str, red: str) -> tuple[bytes, bytes]:
    elf = next(bdir.glob('*.elf'), None)
    if elf is None:
        die(f'no ELF in {bdir}')
    out_b, out_r = bdir / 'emu_black.bin', bdir / 'emu_red.bin'
    for f in (out_b, out_r):
        f.unlink(missing_ok=True)
    cmd = [gdb_bin(), '-q', '-batch', '-nx', str(elf),
           '-ex', 'set pagination off',
           '-ex', f'target remote 127.0.0.1:{port}',
           '-ex', f'dump binary memory {out_b} ({black}) ({black})+{PLANE_BYTES}',
           '-ex', f'dump binary memory {out_r} ({red}) ({red})+{PLANE_BYTES}',
           '-ex', 'detach']
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if not out_b.exists() or not out_r.exists():
        die(f'gdb could not read planes ({black}, {red}):\n{res.stdout}{res.stderr}')
    return out_b.read_bytes(), out_r.read_bytes()


def write_png(path: Path, black: bytes, red: bytes, scale: int = 1) -> None:
    """MONO_HLSB planes -> RGB PNG. Red wins over black, matching the panel."""
    rows = bytearray()
    stride = EPD_W // 8
    for y in range(EPD_H):
        line = bytearray()
        for x in range(EPD_W):
            i = y * stride + x // 8
            bit = 0x80 >> (x % 8)
            px = b'\xd0\x10\x10' if red[i] & bit else (b'\x10\x10\x10' if black[i] & bit else b'\xf4\xf1\xe8')
            line += px * scale
        for _ in range(scale):
            rows += b'\x00' + line

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack('>IIBBBBB', EPD_W * scale, EPD_H * scale, 8, 2, 0, 0, 0)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) + chunk(b'IDAT', zlib.compress(bytes(rows), 6))
                     + chunk(b'IEND', b''))


# ---------------------------------------------------------------- scenario tests

SCENARIO_HELP = """Scenario file: one step per line, '#' comments.
  expect <timeout_s> <regex>     wait for regex in serial output (after previous match)
  reject <regex>                 fail if regex appears anywhere in output so far
  send <text>                    send text + '\\r' to UART0 (use \\n, \\t escapes)
  sendraw <text>                 send text without a trailing CR
  button <menu_minus|menu_plus|a|b>   press a badge button (sends '@btn <name>' per the emulator contract)
  sleep <seconds>
  boots <n>                      allow up to n boot banners (default 1; extra reboots fail the test)
  fbdump <out.png> [black_expr red_expr]   dump e-paper planes via gdb (paths relative to scenario)
"""


def cmd_test(args) -> None:
    project = Path(args.project).resolve()
    bdir = build_dir(project)
    cmd_image(args)
    scenario = Path(args.scenario).resolve()
    steps = []
    for n, line in enumerate(scenario.read_text().splitlines(), 1):
        line = line.strip()
        if line and not line.startswith('#'):
            op, _, rest = line.partition(' ')
            steps.append((n, op, rest))
    needs_gdb = any(op == 'fbdump' for _, op, _ in steps)
    port = free_port() if needs_gdb else None
    log = bdir / f'{scenario.stem}.log'
    q = Qemu(bdir, port, echo=not args.quiet, log=log)
    failed = None
    try:
        for n, op, rest in steps:
            if op == 'expect':
                t, _, rx = rest.partition(' ')
                q.expect(rx, float(t))
            elif op == 'reject':
                q.reject(rest)
            elif op in ('send', 'sendraw'):
                text = rest.encode().decode('unicode_escape')
                q.send(text + ('\r' if op == 'send' else ''))
            elif op == 'button':
                if rest not in BUTTONS:
                    raise AssertionError(f'unknown button {rest!r}; expected one of {BUTTONS}')
                q.send(f'@btn {rest}\r')
            elif op == 'boots':
                q.max_boots = int(rest)
            elif op == 'sleep':
                time.sleep(float(rest))
            elif op == 'fbdump':
                parts = rest.split()
                out = (scenario.parent / parts[0]).resolve()
                black, red = (parts[1], parts[2]) if len(parts) == 3 else (args.black, args.red)
                b, r = read_planes(bdir, port, black, red)
                write_png(out, b, r)
                print(f'\nemu: wrote {out}')
            else:
                raise AssertionError(f'unknown step {op!r}')
    except AssertionError as e:
        failed = f'{scenario.name}:{n}: {e}'
    finally:
        q.close()
    print(f'\nemu: serial log {log}')
    if failed:
        print(f'emu: FAIL {failed}', file=sys.stderr)
        sys.exit(1)
    print(f'emu: PASS {scenario.name}')


def cmd_fbdump(args) -> None:
    project = Path(args.project).resolve()
    bdir = build_dir(project)
    cmd_image(args)
    port = free_port()
    q = Qemu(bdir, port, echo=not args.quiet)
    try:
        if args.wait_for:
            q.expect(args.wait_for, args.timeout)
        else:
            time.sleep(args.timeout)
        b, r = read_planes(bdir, port, args.black, args.red)
    finally:
        q.close()
    write_png(Path(args.out), b, r, args.scale)
    print(f'\nemu: wrote {args.out}')


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p):
        p.add_argument('--project', default=str(DEFAULT_PROJECT), help='ESP-IDF project dir')
        p.add_argument('--fs', help='directory to pack as LittleFS (default: <project>/vfs_root/data if present)')
        p.add_argument('--no-fs', action='store_true', help='leave the storage partition blank (erased flash)')
        p.add_argument('--fs-partition', default='storage', help='partition name for the LittleFS image')
        p.add_argument('--black', default='badge_emu_fb_black', help='gdb expression for the black plane pointer')
        p.add_argument('--red', default='badge_emu_fb_red', help='gdb expression for the red plane pointer')
        p.add_argument('--quiet', action='store_true', help='do not echo serial output')
        return p

    b = sub.add_parser('build')
    b.add_argument('--project', default=str(DEFAULT_PROJECT))
    b.add_argument('--extra-defaults', nargs='*', default=[], help='more sdkconfig defaults files')
    b.add_argument('--clean-config', action='store_true', help='regenerate sdkconfig from defaults')
    b.set_defaults(fn=cmd_build)
    common(sub.add_parser('image')).set_defaults(fn=cmd_image)
    r = common(sub.add_parser('run'))
    r.add_argument('--gdb-port', type=int, help='also expose a gdb stub on this port')
    r.set_defaults(fn=cmd_run)
    t = common(sub.add_parser('test', epilog=SCENARIO_HELP, formatter_class=argparse.RawDescriptionHelpFormatter))
    t.add_argument('scenario')
    t.set_defaults(fn=cmd_test)
    f = common(sub.add_parser('fbdump'))
    f.add_argument('out')
    f.add_argument('--wait-for', help='regex to wait for before dumping')
    f.add_argument('--timeout', type=float, default=30)
    f.add_argument('--scale', type=int, default=1)
    f.set_defaults(fn=cmd_fbdump)

    args = ap.parse_args()
    args.fn(args)


if __name__ == '__main__':
    main()
