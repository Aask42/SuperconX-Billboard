"""Host-compiled hardening tests for the TinyStories adapter (issue #8).

These compile the SHIPPED adapter core
(``native_firmware/main/tinystories_adapter.cpp``) into a shared library with a
thin host seam (``-DTINYSTORIES_HOST_BUILD`` + the C-ABI shim in
``tests/support/tinystories_host.cpp``) and drive it through ctypes — mirroring
``test_c_core.py``. They exercise the real llama2.c parsing / generation / path
logic against tiny synthetic fixtures in the exact on-disk format (see
``tests/support/tinystories_fixtures.py``), so a regression in the firmware's
bounds checks shows up as a failing assertion (or a sanitizer trap) here.

The library is built with ``-fsanitize=address,undefined`` where the platform
lets ctypes load it; otherwise it falls back to UBSan trap-mode, then a plain
build. On macOS the OS blocks dlopen of a full-ASan dylib into the stock Python,
so the effective coverage there is UBSan trap-mode (which still aborts on signed
overflow / bad shifts in the parsing math). Linux CI gets ASan when the runtime
is loadable.

Status codes mirror ``tinystories::Status`` in the adapter header.
"""
import ctypes
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from support import tinystories_fixtures as fx

REPO = Path(__file__).resolve().parents[2]
ADAPTER_CPP = REPO / "native_firmware" / "main" / "tinystories_adapter.cpp"
ADAPTER_INC = REPO / "native_firmware" / "main" / "include"
SHIM_CPP = REPO / "tests" / "support" / "tinystories_host.cpp"

# tinystories::Status
OK = 0
INVALID_ARGUMENT = 1
IO_ERROR = 2
INVALID_FORMAT = 3
OUT_OF_MEMORY = 4
PROMPT_TOO_LONG = 5
NOT_INITIALIZED = 6
CANCELLED = 7

# Build variants, most-instrumented first. Each is (name, extra compiler flags).
_VARIANTS = [
    ("asan+ubsan", ["-fsanitize=address,undefined", "-fno-sanitize-recover=all",
                    "-fno-omit-frame-pointer", "-g"]),
    ("ubsan-trap", ["-fsanitize=undefined", "-fsanitize-trap=undefined", "-g"]),
    ("plain", []),
]


def _find_cxx():
    return shutil.which("c++") or shutil.which("clang++") or shutil.which("g++")


@pytest.fixture(scope="module")
def lib(tmp_path_factory):
    cxx = _find_cxx()
    if cxx is None:
        pytest.skip("no C++ compiler (c++/clang++/g++) on PATH")
    if not ADAPTER_CPP.exists():
        pytest.skip("adapter source not found")
    build_dir = tmp_path_factory.mktemp("tinystories")
    ext = ".dylib" if os.uname().sysname == "Darwin" else ".so"

    dll = None
    chosen = None
    errors = []
    for name, flags in _VARIANTS:
        out = build_dir / ("libtinystories_%s%s" % (name.replace("+", "_"), ext))
        cmd = [cxx, "-shared", "-fPIC", "-O1", "-std=c++17",
               "-DTINYSTORIES_HOST_BUILD", "-I", str(ADAPTER_INC),
               str(SHIM_CPP), str(ADAPTER_CPP), "-o", str(out), *flags]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            errors.append("%s: compile failed: %s" % (name, proc.stderr[-400:]))
            continue
        try:
            dll = ctypes.CDLL(str(out))
            chosen = name
            break
        except OSError as exc:  # sanitizer runtime not loadable into this Python
            errors.append("%s: load failed: %s" % (name, str(exc)[:200]))
    if dll is None:
        pytest.skip("could not build/load host lib:\n" + "\n".join(errors))

    # Record which variant actually ran so the run log shows the coverage level.
    print("\n[tinystories] host build variant: %s" % chosen)

    dll.ts_adapter_new.restype = ctypes.c_void_p
    dll.ts_adapter_delete.argtypes = [ctypes.c_void_p]
    dll.ts_adapter_shutdown.argtypes = [ctypes.c_void_p]
    dll.ts_adapter_initialized.restype = ctypes.c_int
    dll.ts_adapter_initialized.argtypes = [ctypes.c_void_p]
    dll.ts_adapter_initialize.restype = ctypes.c_int
    dll.ts_adapter_initialize.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_char_p]
    dll.ts_adapter_generate.restype = ctypes.c_int
    dll.ts_adapter_generate.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint, ctypes.c_float,
        ctypes.c_float, ctypes.c_ulonglong, ctypes.c_char_p, ctypes.c_size_t,
        ctypes.c_int, ctypes.POINTER(ctypes.c_int),
        ctypes.POINTER(ctypes.c_size_t), ctypes.POINTER(ctypes.c_int)]
    dll.ts_max_generation_tokens.restype = ctypes.c_uint
    dll.ts_max_prompt_bytes.restype = ctypes.c_size_t
    dll._ts_variant = chosen
    return dll


class _Adapter:
    """RAII-ish ctypes wrapper so each test gets a fresh adapter + data root."""

    def __init__(self, lib, data_root):
        self._lib = lib
        self._handle = lib.ts_adapter_new()
        assert self._handle, "ts_adapter_new returned null"
        os.environ["TINYSTORIES_HOST_DATA_ROOT"] = str(data_root)

    def initialize(self, model="/data/model.bin", tok="/data/tok.bin"):
        m = model.encode() if isinstance(model, str) else model
        t = tok.encode() if isinstance(tok, str) else tok
        return self._lib.ts_adapter_initialize(self._handle, m, t)

    def initialized(self):
        return bool(self._lib.ts_adapter_initialized(self._handle))

    def generate(self, prompt, max_tokens=16, temperature=0.0, top_p=0.9,
                 seed=123, cancel_after=-1, cap=8192):
        p = prompt.encode() if isinstance(prompt, str) else prompt
        buf = ctypes.create_string_buffer(cap)
        calls = ctypes.c_int(0)
        length = ctypes.c_size_t(0)
        trunc = ctypes.c_int(0)
        status = self._lib.ts_adapter_generate(
            self._handle, p, max_tokens, temperature, top_p, seed, buf, cap,
            cancel_after, ctypes.byref(calls), ctypes.byref(length),
            ctypes.byref(trunc))
        return {
            "status": status,
            "calls": calls.value,
            "text": buf.raw[: length.value],
            "truncated": bool(trunc.value),
        }

    def close(self):
        if self._handle:
            self._lib.ts_adapter_delete(self._handle)
            self._handle = None


def _write_model(root, config=None, **kw):
    config = config or fx.ModelConfig()
    (root / "model.bin").write_bytes(fx.build_checkpoint(config, **kw))
    return config


def _write_tokenizer(root, abs_vocab=260, **kw):
    vocab = fx.default_vocab(abs_vocab)
    kw.setdefault("scores", fx.scores_favoring(vocab))
    (root / "tok.bin").write_bytes(fx.build_tokenizer(vocab, **kw))
    return vocab


@pytest.fixture
def adapter(lib, tmp_path):
    a = _Adapter(lib, tmp_path)
    yield a, tmp_path
    a.close()


@pytest.fixture
def ready(adapter):
    """An initialized adapter over a well-formed tiny model + tokenizer."""
    a, root = adapter
    _write_model(root)
    _write_tokenizer(root)
    assert a.initialize() == OK
    assert a.initialized()
    return a, root


# --------------------------------------------------------------------------- #
# Path rules (valid_data_path)
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("bad", [
    "",                       # empty
    "data/model.bin",         # relative, no leading slash
    "/etc/passwd",            # absolute, outside /data
    "/data",                  # prefix only, no trailing component
    "/data/",                 # trailing empty component
    "/data/..",               # parent escape
    "/data/../etc/passwd",    # parent escape mid-path
    "/data/./model.bin",      # single-dot component
    "/data/sub/../model.bin",   # parent escape in a later component
])
def test_path_rules_reject_bad_model_path(adapter, bad):
    a, root = adapter
    _write_model(root)
    _write_tokenizer(root)
    # A bad path must be rejected up front as kInvalidArgument, before any I/O.
    assert a.initialize(model=bad) == INVALID_ARGUMENT
    assert not a.initialized()


def test_path_rules_reject_bad_tokenizer_path(adapter):
    a, root = adapter
    _write_model(root)
    _write_tokenizer(root)
    assert a.initialize(tok="/data/../secret") == INVALID_ARGUMENT


def test_valid_path_but_missing_file_is_io_error(adapter):
    # A syntactically valid /data path that does not exist must reach fopen and
    # report kIoError (proving valid_data_path accepted it, vs. kInvalidArgument).
    a, root = adapter
    _write_tokenizer(root)
    assert a.initialize(model="/data/does_not_exist.bin") == IO_ERROR


def test_overlong_path_is_handled_without_crash(adapter):
    # valid_data_path has no explicit length cap (see PR notes); an over-long but
    # otherwise valid path must still be handled gracefully (no crash / no OOB).
    a, root = adapter
    _write_tokenizer(root)
    long_name = "a" * 6000
    status = a.initialize(model="/data/" + long_name)
    assert status in (IO_ERROR, INVALID_ARGUMENT, INVALID_FORMAT)
    assert not a.initialized()


# --------------------------------------------------------------------------- #
# Checkpoint parsing
# --------------------------------------------------------------------------- #

def test_wellformed_model_initializes(ready):
    a, _ = ready
    assert a.initialized()


@pytest.mark.parametrize("field,value", [
    ("dim", 0),            # non-positive
    ("dim", 4097),         # over kMaxDimension
    ("hidden_dim", 0),
    ("n_layers", 0),
    ("n_layers", 129),     # over kMaxLayers
    ("n_heads", 3),        # dim(4) % n_heads(3) != 0
    ("n_kv_heads", 3),     # n_kv_heads > n_heads
    ("seq_len", 0),
    ("seq_len", 4097),     # over kMaxSequenceLength
])
def test_invalid_config_dims_rejected(adapter, field, value):
    a, root = adapter
    cfg = fx.ModelConfig()
    setattr(cfg, field, value)
    _write_model(root, cfg)
    _write_tokenizer(root)
    assert a.initialize() == INVALID_FORMAT
    assert not a.initialized()


def test_odd_head_size_rejected(adapter):
    # head_size = dim / n_heads must be even. dim=6,n_heads=2 => head_size 3.
    a, root = adapter
    cfg = fx.ModelConfig(dim=6, n_heads=2, n_kv_heads=2)
    _write_model(root, cfg)
    _write_tokenizer(root)
    assert a.initialize() == INVALID_FORMAT


def test_vocab_below_minimum_rejected(adapter):
    a, root = adapter
    cfg = fx.ModelConfig(vocab_size=258)   # minimum enforced is 259
    _write_model(root, cfg)
    _write_tokenizer(root)  # rejected at config validation, before the tokenizer
    assert a.initialize() == INVALID_FORMAT


def test_file_shorter_than_expected_rejected(adapter):
    a, root = adapter
    cfg = fx.ModelConfig()
    short = cfg.weight_float_count() - 10
    _write_model(root, cfg, weight_count_override=short)
    _write_tokenizer(root)
    assert a.initialize() == INVALID_FORMAT


def test_file_longer_than_expected_rejected(adapter):
    a, root = adapter
    cfg = fx.ModelConfig()
    longer = cfg.weight_float_count() + 10
    _write_model(root, cfg, weight_count_override=longer)
    _write_tokenizer(root)
    assert a.initialize() == INVALID_FORMAT


def test_truncated_header_rejected(adapter):
    a, root = adapter
    _write_tokenizer(root)
    (root / "model.bin").write_bytes(b"\x01\x02\x03")  # < sizeof(Config)
    assert a.initialize() == INVALID_FORMAT


def test_overflow_dims_rejected_without_allocation(adapter):
    # Regression guard for the reported "unchecked multiplication" in the
    # KV-cache / state sizing. These dims are internally consistent and sit at
    # the valid_config maxima, so layers*seq_len*kv_dim and vocab*dim are huge
    # (many GB). The exact-size check must reject the (tiny) file BEFORE any
    # allocation is attempted, and nothing must overflow / crash under the
    # sanitizer. See PR notes on why this is not reachable as a heap overflow.
    a, root = adapter
    cfg = fx.ModelConfig(dim=4096, hidden_dim=16384, n_layers=128,
                         n_heads=2, n_kv_heads=2, vocab_size=100000,
                         seq_len=4096)
    # Deliberately emit only a stub body; the real expected size is gigabytes.
    _write_model(root, cfg, weight_count_override=4)
    _write_tokenizer(root)
    assert a.initialize() == INVALID_FORMAT
    assert not a.initialized()


# --------------------------------------------------------------------------- #
# Tokenizer parsing
# --------------------------------------------------------------------------- #

def test_tokenizer_vocab_count_mismatch_rejected(adapter):
    # Model declares vocab 260 but the tokenizer file only holds 259 entries, so
    # the parse ends with offset != file size => kInvalidFormat.
    a, root = adapter
    _write_model(root)  # vocab 260
    vocab = fx.default_vocab(260)[:-1]  # 259 entries
    (root / "tok.bin").write_bytes(
        fx.build_tokenizer(vocab, scores=fx.scores_favoring(vocab)))
    assert a.initialize() == INVALID_FORMAT


def test_tokenizer_too_many_entries_rejected(adapter):
    a, root = adapter
    _write_model(root)  # vocab 260
    vocab = fx.default_vocab(262)  # 262 entries, 2 too many
    (root / "tok.bin").write_bytes(
        fx.build_tokenizer(vocab, scores=fx.scores_favoring(vocab)))
    assert a.initialize() == INVALID_FORMAT


def test_tokenizer_truncated_strings_table_rejected(adapter):
    a, root = adapter
    _write_model(root)
    vocab = fx.default_vocab(260)
    (root / "tok.bin").write_bytes(
        fx.build_tokenizer(vocab, scores=fx.scores_favoring(vocab),
                           truncate_last=5))  # last token's bytes cut short
    assert a.initialize() == INVALID_FORMAT


def test_tokenizer_declared_length_overruns_file_rejected(adapter):
    a, root = adapter
    _write_model(root)
    vocab = fx.default_vocab(260)

    def lie(i, actual):
        # Claim the last token is enormous so length > remaining bytes.
        return 9999 if i == len(vocab) - 1 else actual

    (root / "tok.bin").write_bytes(
        fx.build_tokenizer(vocab, scores=fx.scores_favoring(vocab),
                           length_override=lie))
    assert a.initialize() == INVALID_FORMAT


def test_tokenizer_bad_max_token_length_rejected(adapter):
    a, root = adapter
    _write_model(root)
    vocab = fx.default_vocab(260)
    (root / "tok.bin").write_bytes(
        fx.build_tokenizer(vocab, scores=fx.scores_favoring(vocab),
                           max_token_length=0))  # must be > 0
    assert a.initialize() == INVALID_FORMAT


# --------------------------------------------------------------------------- #
# Generation
# --------------------------------------------------------------------------- #

def test_generate_before_init_is_not_initialized(adapter):
    a, root = adapter
    res = a.generate("hello")
    assert res["status"] == NOT_INITIALIZED


def test_empty_prompt_generates(ready):
    # Empty prompt => BOS-only encode, then free generation. Must succeed.
    a, _ = ready
    res = a.generate("", max_tokens=16, temperature=0.0, seed=7)
    assert res["status"] == OK
    assert res["calls"] >= 1


def test_unknown_bytes_use_byte_fallback(ready):
    # High / non-UTF8 bytes have no multi-char token; the byte fallback must
    # encode them (byte+3) and generation must succeed without crashing.
    a, _ = ready
    res = a.generate(b"\xff\xfe\x80", max_tokens=8, temperature=0.0, seed=1)
    assert res["status"] == OK


def test_prompt_over_max_bytes_rejected(ready, lib):
    a, _ = ready
    limit = lib.ts_max_prompt_bytes()
    res = a.generate(b"a" * (limit + 1), max_tokens=8)
    assert res["status"] == PROMPT_TOO_LONG


def test_prompt_exceeding_context_rejected(ready):
    # seq_len defaults to 32; a prompt that encodes to >= seq_len tokens (but is
    # under kMaxPromptBytes) must report kPromptTooLong, not overflow the KV cache.
    a, _ = ready
    res = a.generate("x" * 80, max_tokens=8, temperature=0.0)
    assert res["status"] == PROMPT_TOO_LONG


def test_max_tokens_zero_rejected(ready):
    a, _ = ready
    assert a.generate("hi", max_tokens=0)["status"] == INVALID_ARGUMENT


def test_max_tokens_over_cap_rejected(ready, lib):
    a, _ = ready
    cap = lib.ts_max_generation_tokens()
    assert a.generate("hi", max_tokens=cap + 1)["status"] == INVALID_ARGUMENT


def test_invalid_temperature_and_top_p_rejected(ready):
    a, _ = ready
    assert a.generate("hi", temperature=-0.1)["status"] == INVALID_ARGUMENT
    assert a.generate("hi", temperature=2.5)["status"] == INVALID_ARGUMENT
    assert a.generate("hi", top_p=-0.1)["status"] == INVALID_ARGUMENT
    assert a.generate("hi", top_p=1.5)["status"] == INVALID_ARGUMENT


def test_generation_capped_by_context_length(ready):
    # Request far more tokens than the 32-slot context allows; the loop must be
    # bounded by the context window, so callbacks stay below seq_len.
    a, _ = ready
    res = a.generate("hi", max_tokens=500, temperature=0.0)
    assert res["status"] == OK
    assert res["calls"] < 32  # seq_len


def test_deterministic_argmax_stream_is_golden(ready):
    # temperature=0 => argmax: fully deterministic, independent of RNG.
    a, _ = ready
    first = a.generate("hi", max_tokens=16, temperature=0.0)
    second = a.generate("hi", max_tokens=16, temperature=0.0)
    assert first["status"] == OK
    assert first["text"] == second["text"]
    assert first["calls"] == second["calls"]


def test_deterministic_sampled_stream_with_fixed_seed(ready):
    # Same seed + sampling params => identical stream run to run.
    a, _ = ready
    first = a.generate("hi", max_tokens=24, temperature=0.8, top_p=0.9, seed=42)
    second = a.generate("hi", max_tokens=24, temperature=0.8, top_p=0.9, seed=42)
    assert first["status"] == OK
    assert first["text"] == second["text"]


def test_callback_cancel_stops_cleanly(ready):
    # Returning false from the callback after N chunks must stop with kCancelled
    # and exactly N callbacks, leaving no buffer writes past the logits/KV arrays.
    a, _ = ready
    res = a.generate("hi", max_tokens=64, temperature=0.0, cancel_after=3)
    assert res["status"] == CANCELLED
    assert res["calls"] == 3


def test_full_context_generation_under_sanitizer(ready):
    # Drive generation to fill the whole context so the KV cache is written up to
    # the last slot; under ASan/UBSan this guards against OOB writes.
    a, _ = ready
    res = a.generate("", max_tokens=500, temperature=0.9, top_p=0.95, seed=99)
    assert res["status"] == OK


def test_generate_after_shutdown_is_not_initialized(ready):
    a, root = ready
    a._lib.ts_adapter_shutdown(a._handle)
    assert not a.initialized()
    assert a.generate("hi")["status"] == NOT_INITIALIZED
