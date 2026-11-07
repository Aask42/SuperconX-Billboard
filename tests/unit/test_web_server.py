"""Host-only tests for emulator/web/server.py: POST Origin policy, static/firmware path
boundaries, ELF digest check in backtrace decode, EMU_FB frame assembly, manifest shape.
No QEMU, ESP-IDF, hardware or network beyond a loopback test server."""
import http.client
import json
import os
import shutil
import subprocess
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import importlib.util
import sys

import pytest

WEB = Path(__file__).resolve().parents[2] / "emulator" / "web"
sys.path.insert(0, str(WEB.parent))  # server.py itself does `import emu`
_spec = importlib.util.spec_from_file_location("badge_web_server", WEB / "server.py")
try:
    server = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(server)
except ImportError as e:  # pragma: no cover
    pytest.skip(f"emulator/web/server.py not importable: {e}", allow_module_level=True)
SCRATCH_ROOT = Path(__file__).resolve().parent


@pytest.fixture
def scratch():
    """Scratch dir next to the tests (never the system temp dir); removed afterwards."""
    d = SCRATCH_ROOT / f".web_scratch_{os.getpid()}_{id(object())}"
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture
def dev(scratch, monkeypatch):
    hub = server.Hub()
    d = server.Dev(scratch / "proj", hub, None)
    hw = scratch / "hw"
    hw.mkdir()
    monkeypatch.setattr(d, "hw_dir", lambda: hw)
    d.logged = []
    monkeypatch.setattr(d, "log", lambda text, stream="build": d.logged.append((stream, text)))
    return d


@pytest.fixture
def http_srv(dev):
    hub = dev.hub
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(dev, hub))
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def req(srv, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=5)
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    c.request(method, path, body=data, headers=h)
    r = c.getresponse()
    out = (r.status, r.read(), dict(r.getheaders()))
    c.close()
    return out


# ---------------------------------------------------------------- POST Origin
def test_post_without_origin_is_allowed(http_srv):
    st, body, _ = req(http_srv, "POST", "/api/watch", {"on": False})
    assert st == 200 and json.loads(body) == {"ok": True}


def test_post_same_origin_is_allowed(http_srv):
    origin = f"http://127.0.0.1:{http_srv.server_port}"
    st, _, _ = req(http_srv, "POST", "/api/watch", {"on": True}, {"Origin": origin})
    assert st == 200


@pytest.mark.parametrize("origin", [
    "http://evil.example", "null", "https://127.0.0.1:{port}",
    "http://127.0.0.1:1", "http://localhost:{port}", "http://127.0.0.1:{port}.evil.example",
])
def test_post_cross_origin_is_rejected_without_side_effects(http_srv, dev, origin):
    origin = origin.format(port=http_srv.server_port)
    st, _, _ = req(http_srv, "POST", "/api/watch", {"on": True}, {"Origin": origin})
    assert st == 403
    assert dev.watching is False


def test_post_rejected_origin_blocks_every_action(http_srv, dev):
    for path in ("/api/build", "/api/emu/start", "/api/emu/send", "/api/emu/button", "/api/decode"):
        st, _, _ = req(http_srv, "POST", path, {}, {"Origin": "http://evil.example"})
        assert st == 403, path


def test_post_validation_and_unknown_route(http_srv):
    assert req(http_srv, "POST", "/api/build", {"target": "bogus"})[0] == 400
    assert req(http_srv, "POST", "/api/emu/button", {"name": "power"})[0] == 400
    assert req(http_srv, "POST", "/api/decode", {"target": "x"})[0] == 400
    assert req(http_srv, "POST", "/api/nope", {})[0] == 404


# ---------------------------------------------------------------- static / fw paths
def test_static_routes_serve_only_the_whitelist(http_srv):
    for route, name in server.STATIC.items():
        assert (WEB / name).exists() or route == "/vendor/esptool.js"
    st, body, hd = req(http_srv, "GET", "/")
    assert st == 200 and hd["Content-Type"] == "text/html" and hd["Cache-Control"] == "no-store"
    assert body == (WEB / "index.html").read_bytes()
    assert req(http_srv, "GET", "/app.js")[2]["Content-Type"] == "text/javascript"
    assert req(http_srv, "GET", "/style.css")[2]["Content-Type"] == "text/css"


@pytest.mark.parametrize("path", ["/server.py", "/start.sh", "/../server.py", "/%2e%2e/server.py", "/static/app.js"])
def test_non_whitelisted_or_traversal_static_paths_404(http_srv, path):
    assert req(http_srv, "GET", path)[0] == 404


def test_missing_whitelisted_file_is_404(http_srv, monkeypatch, scratch):
    monkeypatch.setattr(server, "STATIC", {"/": "does-not-exist.html"})
    assert req(http_srv, "GET", "/")[0] == 404


def test_fw_serves_files_inside_hw_dir_only(http_srv, dev, scratch):
    (dev.hw_dir() / "app.bin").write_bytes(b"\x01\x02")
    (dev.hw_dir() / "sub").mkdir()
    (dev.hw_dir() / "sub" / "x.bin").write_bytes(b"x")
    (scratch / "secret.txt").write_text("secret")
    (scratch / "hw-evil").mkdir()
    (scratch / "hw-evil" / "k.bin").write_bytes(b"evil")
    st, body, hd = req(http_srv, "GET", "/api/fw/app.bin")
    assert (st, body) == (200, b"\x01\x02") and hd["Content-Type"] == "application/octet-stream"
    assert req(http_srv, "GET", "/api/fw/sub/x.bin")[0] == 200
    for p in ("/api/fw/../secret.txt", "/api/fw/%2e%2e/secret.txt", "/api/fw/../hw-evil/k.bin",
              "/api/fw/%2e%2e%2fsecret.txt", "/api/fw/.."):
        st = req(http_srv, "GET", p)[0]
        assert st in (403, 404), p
    # http.client does not normalise paths, so ".." reaches the handler; it must never serve outside
    assert req(http_srv, "GET", "/api/fw/../secret.txt")[1] != b"secret"


def test_fw_blocks_symlink_escape(http_srv, dev, scratch):
    (scratch / "secret.txt").write_text("secret")
    try:
        (dev.hw_dir() / "link.bin").symlink_to(scratch / "secret.txt")
    except OSError:
        pytest.skip("symlinks unavailable")
    st, body, _ = req(http_srv, "GET", "/api/fw/link.bin")
    assert st == 403 and body != b"secret"


def test_fw_missing_file_404_and_directory_not_served(http_srv, dev):
    (dev.hw_dir() / "sub").mkdir()
    assert req(http_srv, "GET", "/api/fw/nope.bin")[0] == 404
    assert req(http_srv, "GET", "/api/fw/sub")[0] == 404
    assert req(http_srv, "GET", "/api/fw/")[0] in (403, 404)


# ---------------------------------------------------------------- manifest
def _write_hw(dev, with_fs=False, chip=True):
    hw = dev.hw_dir()
    (hw / "bootloader").mkdir(exist_ok=True)
    (hw / "bootloader" / "bootloader.bin").write_bytes(b"b" * 10)
    (hw / "partition_table").mkdir(exist_ok=True)
    (hw / "partition_table" / "partition-table.bin").write_bytes(b"p" * 20)
    (hw / "app.bin").write_bytes(b"a" * 30)
    args = {"flash_settings": {"flash_mode": "dio", "flash_size": "8MB", "flash_freq": "80m"},
            "flash_files": {"0x10000": "app.bin", "0x0": "bootloader/bootloader.bin",
                            "0x8000": "partition_table/partition-table.bin"}}
    if chip:
        args["extra_esptool_args"] = {"chip": "esp32s3"}
    (hw / "flasher_args.json").write_text(json.dumps(args))
    if with_fs:
        (hw / "hw_fs.bin").write_bytes(b"f" * 40)
        (hw / "hw_fs.json").write_text(json.dumps({"offset": 0x310000}))


def test_manifest_shape_sorted_offsets_and_sizes(dev):
    _write_hw(dev)
    m = dev.manifest(False)
    assert m["chip"] == "esp32s3"
    assert m["settings"]["flash_size"] == "8MB"
    assert [(f["address"], f["path"], f["size"]) for f in m["files"]] == [
        (0x0, "bootloader/bootloader.bin", 10), (0x8000, "partition_table/partition-table.bin", 20),
        (0x10000, "app.bin", 30)]
    assert isinstance(m["built"], str) and m["built"]


def test_manifest_chip_defaults_to_esp32s3(dev):
    _write_hw(dev, chip=False)
    assert dev.manifest(False)["chip"] == "esp32s3"


def test_manifest_with_fs_appends_storage_at_partition_offset(dev):
    _write_hw(dev, with_fs=True)
    files = dev.manifest(True)["files"]
    assert files[-1] == {"address": 0x310000, "path": "hw_fs.bin", "size": 40}
    assert [f["address"] for f in files] == sorted(f["address"] for f in files)
    assert all(f["path"] != "hw_fs.bin" for f in dev.manifest(False)["files"])


def test_manifest_errors_over_http(http_srv, dev):
    st, body, _ = req(http_srv, "GET", "/api/manifest")
    assert st == 404 and json.loads(body) == {"error": "no hardware build yet"}
    _write_hw(dev)
    st, body, _ = req(http_srv, "GET", "/api/manifest?fs=1")
    assert st == 404 and "filesystem" in json.loads(body)["error"]
    st, body, _ = req(http_srv, "GET", "/api/manifest")
    assert st == 200 and json.loads(body)["files"][0]["address"] == 0


def test_manifest_listed_files_are_servable(http_srv, dev):
    _write_hw(dev)
    for f in json.loads(req(http_srv, "GET", "/api/manifest")[1])["files"]:
        st, body, _ = req(http_srv, "GET", "/api/fw/" + f["path"])
        assert st == 200 and len(body) == f["size"]


# ---------------------------------------------------------------- decode / ELF digest
import hashlib  # noqa: E402

ELF_BYTES = b"\x7fELF-fake-elf-image"
ELF_SHA = hashlib.sha256(ELF_BYTES).hexdigest()
BT = "Backtrace: 0x40081234:0x3ffb0000 0x400d5678:0x3ffb0010"


@pytest.fixture
def decode_env(dev, scratch, monkeypatch):
    elf = scratch / "app.elf"
    elf.write_bytes(ELF_BYTES)
    monkeypatch.setattr(dev, "elf", lambda target: elf)
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="0x40081234: foo at a.c:1\n\n0x400d5678: bar at b.c:2\n",
                                           stderr="")

    monkeypatch.setattr(server.subprocess, "run", fake_run)
    return calls


def test_decode_without_code_addresses_does_nothing(dev, decode_env):
    assert dev.decode("hw", "nothing here 0x3ffb0000") == {"lines": []}
    assert decode_env == []


def test_decode_without_elf_warns(dev, monkeypatch):
    monkeypatch.setattr(dev, "elf", lambda t: None)
    r = dev.decode("emu", BT)
    assert r["lines"] == [] and "no emu ELF" in r["warning"]


def test_decode_dedups_addresses_and_returns_lines(dev, decode_env):
    r = dev.decode("hw", BT + " 0x40081234")
    assert r["lines"] == ["0x40081234: foo at a.c:1", "0x400d5678: bar at b.c:2"]
    assert "warning" not in r
    cmd = decode_env[0]
    assert cmd[0] == "xtensa-esp32s3-elf-addr2line" and cmd[-2:] == ["0x40081234", "0x400d5678"]


@pytest.mark.parametrize("sha", [ELF_SHA, ELF_SHA[:8], ELF_SHA.upper(), ELF_SHA[:6].upper()])
def test_decode_matching_digest_has_no_warning(dev, decode_env, sha):
    assert "warning" not in dev.decode("hw", BT, sha)


@pytest.mark.parametrize("sha", ["0" * 64, "deadbeef", "ffffff"])
def test_decode_mismatched_digest_warns_but_still_decodes(dev, decode_env, sha):
    r = dev.decode("hw", BT, sha)
    assert "!=" in r["warning"] and sha in r["warning"] and len(r["lines"]) == 2


@pytest.mark.parametrize("sha", [None, "", "abc", "xyz123", "g" * 8, "a" * 65, 12345, ["abcdef"]])
def test_decode_ignores_malformed_digest(dev, decode_env, sha):
    r = dev.decode("hw", BT, sha)
    assert "warning" not in r and len(r["lines"]) == 2


def test_decode_addr2line_failure_is_reported(dev, decode_env, monkeypatch):
    def boom(cmd, **kw):
        raise FileNotFoundError("no addr2line")
    monkeypatch.setattr(server.subprocess, "run", boom)
    r = dev.decode("hw", BT)
    assert r["lines"] == [] and "addr2line failed" in r["warning"]


def test_decode_endpoint_truncates_text_and_passes_sha(http_srv, dev, decode_env):
    st, body, _ = req(http_srv, "POST", "/api/decode", {"target": "hw", "text": BT, "sha": "0" * 64})
    assert st == 200 and "!=" in json.loads(body)["warning"]
    # addresses past the 4096-char cap are ignored
    st, body, _ = req(http_srv, "POST", "/api/decode", {"text": "x" * 4096 + " 0x40081234"})
    assert json.loads(body)["lines"] == []


def test_serial_crash_uses_elf_sha_from_boot_log(dev, decode_env):
    dev.on_serial(f"ELF file SHA256: {'0' * 16}\n")
    assert dev.emu_elf_sha == "0" * 16
    dev.on_serial(BT + "\n")
    text = "".join(t for _, t in dev.logged)
    assert "foo at a.c:1" in text and "[decode]" in text and "!=" in text


# ---------------------------------------------------------------- EMU_FB assembly
import base64  # noqa: E402
import zlib  # noqa: E402


def b64(b):
    return base64.b64encode(b).decode()


def planes(w, h, black=0xA5, red=0x5A):
    n = w // 8 * h
    return bytes([black]) * n + bytes([red]) * n


def fb_line(w, h, enc="raw", payload=None):
    payload = planes(w, h) if payload is None else payload
    if enc == "z":
        payload = zlib.compress(payload, 6)
    return f"EMU_FB {w} {h} {enc} {b64(payload)}"


def frames(dev, monkeypatch):
    out = []
    monkeypatch.setattr(dev.hub, "emit", lambda ev, data: out.append((ev, data)))
    return out


@pytest.mark.parametrize("enc", ["raw", "z"])
@pytest.mark.parametrize("w,h", [(8, 1), (296, 128), (960, 672)])
def test_valid_frames_are_published(dev, monkeypatch, enc, w, h):
    out = frames(dev, monkeypatch)
    line = fb_line(w, h, enc)
    dev.on_serial(line + "\n")
    assert len(out) == 1 and out[0][0] == "frame"
    f = out[0][1]
    assert (f["w"], f["h"], f["enc"], f["source"], f["n"]) == (w, h, enc, "serial", 1)
    assert f["data"] == line.split(" ", 4)[4]
    assert dev.last_frame is f
    assert dev.logged == [("emu", "[EMU_FB frame]\n")]


@pytest.mark.parametrize("enc", ["raw", "z"])
def test_full_960x672_frame_chunked_and_crlf(dev, monkeypatch, enc):
    out = frames(dev, monkeypatch)
    text = fb_line(960, 672, enc) + "\r\n"
    for i in range(0, len(text), 997):  # odd chunk size splits the prefix and the payload
        dev.on_serial(text[i:i + 997])
    assert len(out) == 1
    assert out[0][1]["data"] == text.split(" ", 4)[4].strip()
    assert dev.logged == [("emu", "[EMU_FB frame]\n")]  # no partial frame text leaks to the log


def test_prefix_split_across_chunks(dev, monkeypatch):
    out = frames(dev, monkeypatch)
    line = fb_line(8, 1)
    for chunk in (line[:3], line[3:9], line[9:] + "\n"):
        dev.on_serial(chunk)
    assert len(out) == 1 and dev.logged == [("emu", "[EMU_FB frame]\n")]


def test_frames_interleaved_with_normal_lines_and_counter(dev, monkeypatch):
    out = frames(dev, monkeypatch)
    dev.on_serial(f"hello\n{fb_line(8, 1)}\nworld\n{fb_line(8, 1, 'z')}\n")
    assert [f["n"] for _, f in out] == [1, 2]
    text = "".join(t for _, t in dev.logged)
    assert "hello\n" in text and "world\n" in text and "AA==" not in text


def test_prompt_without_newline_is_shown_once(dev):
    dev.on_serial("badge> ")
    dev.on_serial("x\n")
    text = "".join(t for _, t in dev.logged)
    assert text.count("badge> ") == 1 and text.endswith("x\n")


def _bad_lines():
    full = planes(8, 2)  # two planes of 2 bytes each
    zfull = zlib.compress(full)
    big = planes(960, 672)
    return {
        "missing-fields": "EMU_FB 296 128",
        "reported-case-one-byte-for-8x1": "EMU_FB 8 1 raw AA==",
        "raw-truncated": fb_line(8, 2, payload=full[:-1]),
        "raw-empty": "EMU_FB 8 2 raw ",
        "raw-one-plane-only": fb_line(960, 672, payload=big[:len(big) // 2]),
        "raw-trailing-bytes": fb_line(8, 2, payload=full + b"\0"),
        "z-truncated-stream": f"EMU_FB 8 2 z {b64(zfull[:-3])}",
        "z-short-plane-data": fb_line(8, 2, "z", full[:-1]),
        "z-too-long": fb_line(8, 2, "z", full + b"\0"),
        "z-trailing-garbage": f"EMU_FB 8 2 z {b64(zfull + b'junk')}",
        "z-not-zlib": f"EMU_FB 8 2 z {b64(b'not zlib data')}",
        "bad-base64": "EMU_FB 8 2 raw !!!!",
        "bad-base64-padding": "EMU_FB 8 2 raw AAA",
        "unknown-enc": f"EMU_FB 8 2 gz {b64(full)}",
        "uppercase-enc": f"EMU_FB 8 2 RAW {b64(full)}",
        "non-numeric-dims": "EMU_FB wide tall z AAAA",
        "zero-width": "EMU_FB 0 2 raw ",
        "zero-height": "EMU_FB 8 0 raw ",
        "negative-width": f"EMU_FB -8 2 raw {b64(full)}",
        "width-not-multiple-of-8": fb_line(8, 2).replace("EMU_FB 8 2", "EMU_FB 9 2"),
        "oversized-width": f"EMU_FB 968 1 raw {b64(planes(968, 1))}",
        "oversized-height": f"EMU_FB 8 673 raw {b64(planes(8, 673))}",
        "huge-dims": f"EMU_FB 999999999 999999999 raw {b64(full)}",
        "oversized-raw-payload": fb_line(8, 2, payload=b"\0" * 100000),
        "zlib-bomb": fb_line(8, 2, "z", b"\0" * 50_000_000),
    }


@pytest.mark.parametrize("name", sorted(_bad_lines()))
def test_invalid_frames_are_not_published_and_do_not_raise(dev, monkeypatch, name):
    out = frames(dev, monkeypatch)
    dev.on_serial(_bad_lines()[name] + "\n")
    assert out == [] and dev.last_frame is None and dev.frames == 0
    dev.on_serial(fb_line(8, 1) + "\n")  # the reader keeps working afterwards
    assert [f["n"] for _, f in out] == [1]


def test_zlib_decompression_is_bounded(monkeypatch):
    sizes = []
    real = zlib.decompressobj

    class Spy:
        def __init__(self):
            self.o = real()

        def decompress(self, data, max_length=0):
            sizes.append(max_length)
            return self.o.decompress(data, max_length)

        def __getattr__(self, k):
            return getattr(self.o, k)

    monkeypatch.setattr(server.zlib, "decompressobj", Spy)
    assert not server.valid_frame(960, 672, "z", b64(zlib.compress(b"\0" * 100_000_000)))
    assert sizes == [2 * 120 * 672 + 1]
