#!/usr/bin/env python3
"""Badge dev UI backend: compile, run QEMU, stream serial + screen to the browser.

Start with emulator/web/start.sh (it loads ESP-IDF), then open http://127.0.0.1:8042 in Chrome.
Device flashing and the device serial monitor happen in the browser over Web Serial;
this server only builds the binaries and serves them. Stdlib only.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import emu  # noqa: E402

STATIC = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css',
          '/vendor/esptool.js': 'vendor/esptool-js/bundle.js'}
MIME = {'.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.bin': 'application/octet-stream'}
REFRESH = re.compile(r'EMU_EPD_REFRESH \d+')
WATCH_SKIP = {'managed_components', '.git', 'third_party', '__pycache__'}
WATCH_EXT = {'.c', '.cc', '.cpp', '.h', '.hpp', '.txt', '.csv', '.yml', '.projbuild', '.defaults', '.png', '.bin',
             '.json'}


def ns(**kw):
    return argparse.Namespace(**{'no_fs': False, 'fs': None, 'fs_partition': 'storage', **kw})


class Hub:
    """Fan-out of server-sent events to every open browser tab."""

    def __init__(self):
        self.clients: list[queue.Queue] = []
        self.lock = threading.Lock()

    def add(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=2000)
        with self.lock:
            self.clients.append(q)
        return q

    def remove(self, q: queue.Queue) -> None:
        with self.lock:
            self.clients.remove(q)

    def emit(self, event: str, data) -> None:
        msg = f'event: {event}\ndata: {json.dumps(data)}\n\n'.encode()
        with self.lock:
            for q in self.clients:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    pass


class Dev:
    def __init__(self, project: Path, hub: Hub, fs: str | None):
        self.project = project
        self.hub = hub
        self.fs = fs
        self.build_lock = threading.Lock()
        self.qemu: emu.Qemu | None = None
        self.gdb_port = 0
        self.line_buf = ''
        self.shown = 0
        self.snap_lock = threading.Lock()
        self.snap_again = False
        self.frames = 0
        self.last_frame: dict | None = None
        self.watching = False
        self.status = {'build': 'idle', 'emu': 'stopped', 'watch': False,
                       'hw_ready': (self.hw_dir() / 'flasher_args.json').exists(),
                       'project': project.name}

    # ---- helpers
    def set_status(self, **kw) -> None:
        self.status.update(kw)
        self.hub.emit('status', self.status)

    def log(self, text: str, stream: str = 'build') -> None:
        self.hub.emit('log', {'stream': stream, 'text': text})

    def hw_dir(self) -> Path:
        return emu.HERE / 'out' / f'{self.project.name}-hw'

    def emu_dir(self) -> Path:
        return emu.build_dir(self.project)

    def run_logged(self, cmd: list[str]) -> int:
        self.log('$ ' + ' '.join(cmd) + '\n')
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        assert p.stdout
        for line in p.stdout:
            self.log(line)
        return p.wait()

    # ---- build
    def build(self, target: str, include_fs: bool = False) -> bool:
        if not self.build_lock.acquire(blocking=False):
            self.log('build already running\n')
            return False
        try:
            self.set_status(build=f'building {target}')
            t0 = time.time()
            if target == 'emu':
                bdir = self.emu_dir()
                defaults = [self.project / 'sdkconfig.defaults', emu.OVERLAY]
            else:
                bdir = self.hw_dir()
                defaults = [self.project / 'sdkconfig.defaults']
            bdir.mkdir(parents=True, exist_ok=True)
            cmd = ['idf.py', '-C', str(self.project), '-B', str(bdir), '-D', f'SDKCONFIG={bdir / "sdkconfig"}',
                   '-D', 'SDKCONFIG_DEFAULTS=' + ';'.join(str(d) for d in defaults if d.exists()), 'build']
            ok = self.run_logged(cmd) == 0
            if ok and target == 'emu':
                try:
                    emu.cmd_image(ns(project=str(self.project), fs=self.fs))
                except SystemExit:
                    ok = False
            if ok and target == 'hw' and include_fs:
                ok = self.build_hw_fs()
            if target == 'hw':
                self.status['hw_ready'] = ok
            self.log(f'== {target} build {"OK" if ok else "FAILED"} in {time.time() - t0:.1f}s ==\n')
            self.set_status(build='ok' if ok else 'failed')
            self.hub.emit('built', {'target': target, 'ok': ok})
            return ok
        finally:
            self.build_lock.release()

    def build_hw_fs(self) -> bool:
        fs_dir = Path(self.fs) if self.fs else self.project / 'vfs_root' / 'data'
        if not fs_dir.is_dir():
            self.log(f'no {fs_dir}; skipping filesystem image\n')
            return True
        part = next((p for p in emu.partitions(self.project, self.hw_dir()) if p['name'] == 'storage'), None)
        if not part:
            self.log('no storage partition in partitions.csv\n')
            return False
        emu.make_littlefs(fs_dir, part['size'], self.hw_dir() / 'hw_fs.bin')
        (self.hw_dir() / 'hw_fs.json').write_text(json.dumps({'offset': part['offset']}))
        self.log(f'LittleFS image of {fs_dir} -> 0x{part["offset"]:x}\n')
        return True

    def manifest(self, include_fs: bool) -> dict:
        bdir = self.hw_dir()
        args = json.loads((bdir / 'flasher_args.json').read_text())
        files = [{'address': int(off, 0), 'path': path, 'size': (bdir / path).stat().st_size}
                 for off, path in args['flash_files'].items()]
        if include_fs:
            if not (bdir / 'hw_fs.bin').exists():
                raise FileNotFoundError('no filesystem image; build hardware with "include filesystem"')
            off = json.loads((bdir / 'hw_fs.json').read_text())['offset']
            files.append({'address': off, 'path': 'hw_fs.bin', 'size': (bdir / 'hw_fs.bin').stat().st_size})
        files.sort(key=lambda f: f['address'])
        return {'chip': args.get('extra_esptool_args', {}).get('chip', 'esp32s3'),
                'settings': args['flash_settings'], 'files': files,
                'built': time.ctime((bdir / 'flasher_args.json').stat().st_mtime)}

    # ---- emulator
    def emu_start(self) -> None:
        self.emu_stop()
        bdir = self.emu_dir()
        if not (bdir / 'emu_flash.bin').exists():
            self.log('no emulator image yet; build first\n', 'emu')
            return
        self.gdb_port = emu.free_port()
        self.line_buf = ''
        self.shown = 0
        self.log('\n===== QEMU boot =====\n', 'emu')
        self.qemu = emu.Qemu(bdir, self.gdb_port, echo=False, on_output=self.on_serial, keep=False)
        self.set_status(emu='running')
        threading.Thread(target=self.watch_exit, args=(self.qemu,), daemon=True).start()

    def watch_exit(self, q: emu.Qemu) -> None:
        q.proc.wait()
        if self.qemu is q:
            self.set_status(emu=f'exited ({q.proc.returncode})')

    def emu_stop(self) -> None:
        if self.qemu:
            q, self.qemu = self.qemu, None
            q.close()
            self.set_status(emu='stopped')

    def emu_send(self, text: str) -> None:
        if self.qemu:
            self.qemu.send(text)

    def on_serial(self, text: str) -> None:
        """Show serial text as it arrives, but parse complete lines for EMU_FB / EMU_EPD_REFRESH."""
        self.line_buf += text
        out = []
        while '\n' in self.line_buf:
            line, self.line_buf = self.line_buf.split('\n', 1)
            shown, self.shown = self.shown, 0
            if line.startswith('EMU_FB '):
                self.frame_from_line(line, 'serial')
                out.append('[EMU_FB frame]\n')
                continue
            out.append(line[shown:] + '\n')
            if REFRESH.search(line):
                threading.Thread(target=self.snapshot, daemon=True).start()
        pending = self.line_buf
        if pending and not 'EMU_FB '.startswith(pending[:7]):
            out.append(pending[self.shown:])  # prompts without a newline
            self.shown = len(pending)
        if out:
            self.log(''.join(out), 'emu')

    def frame_from_line(self, line: str, source: str) -> None:
        parts = line.split(' ', 4)
        if len(parts) == 5:
            self.publish_frame({'w': int(parts[1]), 'h': int(parts[2]), 'enc': parts[3], 'data': parts[4].strip(),
                                'source': source})

    def snapshot(self) -> None:
        self.snap_again = True
        if not self.snap_lock.acquire(blocking=False):
            return  # the in-flight dump loops once more and picks up the newest planes
        try:
            while self.snap_again and self.qemu:
                self.snap_again = False
                self._snapshot()
        finally:
            self.snap_lock.release()

    def _snapshot(self) -> None:
        try:
            b, r = emu.read_planes(self.emu_dir(), self.gdb_port, 'badge_emu_fb_black', 'badge_emu_fb_red')
        except SystemExit:
            self.log('[screen] no badge_emu_fb_black/red in firmware yet (emulator contract T55)\n', 'emu')
            return
        data = base64.b64encode(zlib.compress(b + r, 6)).decode()
        self.publish_frame({'w': emu.EPD_W, 'h': emu.EPD_H, 'enc': 'z', 'data': data, 'source': 'gdb'})

    def publish_frame(self, frame: dict) -> None:
        self.frames += 1
        frame['n'] = self.frames
        frame['t'] = time.strftime('%H:%M:%S')
        self.last_frame = frame
        self.hub.emit('frame', frame)

    # ---- watch
    def snapshot_tree(self) -> dict:
        out = {}
        for root, dirs, files in os.walk(self.project):
            dirs[:] = [d for d in dirs if d not in WATCH_SKIP and not d.startswith('build')]
            for f in files:
                p = Path(root) / f
                if p.suffix in WATCH_EXT or f == 'CMakeLists.txt' or f.startswith('Kconfig'):
                    try:
                        out[str(p)] = p.stat().st_mtime
                    except FileNotFoundError:
                        pass
        return out

    def watch_loop(self) -> None:
        last = None
        while True:
            time.sleep(1)
            if not self.watching:
                last = None
                continue
            cur = self.snapshot_tree()
            if last is None or cur == last:
                last = cur
                continue
            changed = sorted(k for k in set(cur) | set(last) if cur.get(k) != last.get(k))
            time.sleep(0.4)  # let editors finish multi-file saves
            rel = [os.path.relpath(c, self.project) for c in changed[:5]]
            self.log(f'== change: {", ".join(rel)} -> rebuild ==\n')
            self.hub.emit('changed', {'files': rel})
            if self.build('emu'):
                self.emu_start()
            last = self.snapshot_tree()


def make_handler(dev: Dev, hub: Hub):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def send_json(self, obj, code=200):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def send_file(self, path: Path):
            if not path.is_file():
                return self.send_error(404)
            body = path.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', MIME.get(path.suffix, 'application/octet-stream'))
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if u.path in STATIC:
                return self.send_file(HERE / STATIC[u.path])
            if u.path == '/api/state':
                return self.send_json({'status': dev.status, 'project': str(dev.project)})
            if u.path == '/api/manifest':
                try:
                    return self.send_json(dev.manifest(qs.get('fs', ['0'])[0] == '1'))
                except FileNotFoundError as e:
                    return self.send_json({'error': str(e) if 'filesystem' in str(e) else 'no hardware build yet'},
                                          404)
            if u.path.startswith('/api/fw/'):
                target = (dev.hw_dir() / u.path[len('/api/fw/'):]).resolve()
                if dev.hw_dir().resolve() not in target.parents:
                    return self.send_error(403)
                return self.send_file(target)
            if u.path == '/api/events':
                return self.events()
            self.send_error(404)

        def events(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            q = hub.add()
            try:
                self.wfile.write(f'event: status\ndata: {json.dumps(dev.status)}\n\n'.encode())
                if dev.last_frame:
                    self.wfile.write(f'event: frame\ndata: {json.dumps(dev.last_frame)}\n\n'.encode())
                self.wfile.flush()
                while True:
                    try:
                        msg = q.get(timeout=15)
                    except queue.Empty:
                        msg = b': ping\n\n'
                    self.wfile.write(msg)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                hub.remove(q)

        def do_POST(self):
            u = urlparse(self.path)
            if self.headers.get('Origin') not in (None, f'http://{self.headers.get("Host")}'):
                return self.send_error(403)  # only the UI itself may drive builds
            n = int(self.headers.get('Content-Length') or 0)
            body = json.loads(self.rfile.read(n) or b'{}')

            def bg(fn, *a):
                threading.Thread(target=fn, args=a, daemon=True).start()

            if u.path == '/api/build':
                target = body.get('target', 'emu')
                if target not in ('emu', 'hw'):
                    return self.send_json({'error': 'target must be emu or hw'}, 400)
                if target == 'emu':
                    bg(lambda: dev.build('emu') and dev.emu_start())
                else:
                    bg(dev.build, 'hw', bool(body.get('fs')))
            elif u.path == '/api/emu/start':
                dev.emu_start()
            elif u.path == '/api/emu/stop':
                dev.emu_stop()
            elif u.path == '/api/emu/send':
                dev.emu_send(body.get('text', ''))
            elif u.path == '/api/emu/button':
                if body.get('name') not in emu.BUTTONS:
                    return self.send_json({'error': f'button must be one of {emu.BUTTONS}'}, 400)
                dev.emu_send(f'@btn {body["name"]}\r')
            elif u.path == '/api/emu/snapshot':
                bg(dev.snapshot)
            elif u.path == '/api/watch':
                dev.watching = bool(body.get('on'))
                dev.set_status(watch=dev.watching)
            else:
                return self.send_error(404)
            self.send_json({'ok': True})

    return H


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--project', default=str(emu.DEFAULT_PROJECT))
    ap.add_argument('--fs', help='LittleFS source dir (default <project>/vfs_root/data)')
    ap.add_argument('--port', type=int, default=8042)
    ap.add_argument('--host', default='127.0.0.1')
    args = ap.parse_args()
    emu.idf_path()
    hub = Hub()
    dev = Dev(Path(args.project).resolve(), hub, args.fs)
    threading.Thread(target=dev.watch_loop, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(dev, hub))
    srv.daemon_threads = True
    print(f'badge dev UI: http://{args.host}:{args.port}  (project {dev.project})', flush=True)
    try:
        srv.serve_forever()
    finally:
        dev.emu_stop()


if __name__ == '__main__':
    main()
