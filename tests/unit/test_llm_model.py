"""Validate the on-device LLM assets are the format tinystories_adapter expects:
a llama2.c checkpoint whose vocab matches the tokenizer (stories260K + tok512)."""
import struct
from pathlib import Path

import pytest

from support.badge_spec import read_llama2_config, LLM_VOCAB_SIZE

REPO = Path(__file__).resolve().parents[2]
MODELS = REPO / "native_firmware" / "vfs_root" / "data" / "models"


def _require(path: Path) -> Path:
    if not path.exists():
        pytest.skip(f"{path} not staged")
    return path


def test_checkpoint_header_is_sane():
    cfg = read_llama2_config(_require(MODELS / "stories260K.bin"))
    assert cfg["vocab_size"] == LLM_VOCAB_SIZE
    assert 0 < cfg["dim"] <= 4096
    assert 0 < cfg["n_layers"] <= 64
    assert 0 < cfg["n_heads"] <= 64
    assert cfg["n_kv_heads"] <= cfg["n_heads"]
    assert 0 < cfg["seq_len"] <= 8192


def test_checkpoint_size_matches_header():
    """A shared-classifier llama2.c export's weight bytes must fit the file; a
    truncated/wrong file would crash build_transformer on device."""
    path = _require(MODELS / "stories260K.bin")
    cfg = read_llama2_config(path)
    # Rough lower bound: token embedding table alone = vocab*dim*4 bytes + header.
    min_bytes = 28 + cfg["vocab_size"] * cfg["dim"] * 4
    assert path.stat().st_size >= min_bytes


def test_tokenizer_matches_model_vocab():
    tok = _require(MODELS / "tok512.bin")
    data = tok.read_bytes()
    (max_token_len,) = struct.unpack("<i", data[:4])
    assert 0 < max_token_len < 1024
    # Walk the vocab entries (float32 score, int32 len, <len> bytes) and count them.
    off, count = 4, 0
    while off + 8 <= len(data) and count < LLM_VOCAB_SIZE:
        off += 4  # score
        (slen,) = struct.unpack("<i", data[off:off + 4]); off += 4
        off += slen
        count += 1
    assert count == LLM_VOCAB_SIZE, f"tokenizer holds {count} tokens, expected {LLM_VOCAB_SIZE}"
