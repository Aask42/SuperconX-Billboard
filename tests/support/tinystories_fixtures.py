"""Synthetic llama2.c-format fixtures for the TinyStories adapter host tests.

These generate *tiny* models and tokenizers in the exact binary layout the
shipped adapter (``native_firmware/main/tinystories_adapter.cpp``) parses, so the
tests never need to ship large or third-party model data. The formats mirror
Andrej Karpathy's llama2.c:

Checkpoint (model) file::

    Config header: 7x int32 little-endian
        dim, hidden_dim, n_layers, n_heads, n_kv_heads, vocab_size, seq_len
    Weights: float32 little-endian, in this order
        token_embedding   vocab*dim
        rms_att_weight     layers*dim
        wq                 layers*dim*dim
        wk                 layers*dim*kv_dim
        wv                 layers*dim*kv_dim
        wo                 layers*dim*dim
        rms_ffn_weight     layers*dim
        w1                 layers*dim*hidden
        w2                 layers*hidden*dim
        w3                 layers*dim*hidden
        rms_final_weight   dim
        legacy RoPE        seq_len*head_size   (freq_cis real+imag, unused)
        wcls               vocab*dim           (only when NOT shared)

    A positive ``vocab_size`` means shared classifier weights (wcls aliases the
    token embedding table); a negative ``vocab_size`` means a separate wcls block
    is appended and the true vocab size is its absolute value.

Tokenizer file::

    int32 max_token_length
    repeated vocab_size times:
        float32 score
        int32   length
        length bytes of the token string (no trailing NUL)
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ModelConfig:
    dim: int = 4
    hidden_dim: int = 8
    n_layers: int = 1
    n_heads: int = 2
    n_kv_heads: int = 2
    vocab_size: int = 260      # positive => shared weights
    seq_len: int = 32

    @property
    def abs_vocab(self) -> int:
        return abs(self.vocab_size)

    @property
    def shared_weights(self) -> bool:
        return self.vocab_size > 0

    @property
    def head_size(self) -> int:
        return self.dim // self.n_heads

    @property
    def kv_dim(self) -> int:
        return (self.dim * self.n_kv_heads) // self.n_heads

    def weight_float_count(self) -> int:
        v = self.abs_vocab
        d = self.dim
        h = self.hidden_dim
        n = self.n_layers
        kv = self.kv_dim
        total = v * d
        total += n * d
        total += n * d * d
        total += n * d * kv
        total += n * d * kv
        total += n * d * d
        total += n * d
        total += n * d * h
        total += n * h * d
        total += n * d * h
        total += d
        total += self.seq_len * self.head_size
        if not self.shared_weights:
            total += v * d
        return total


class _Lcg:
    """Tiny deterministic PRNG so fixtures are byte-reproducible run to run."""

    def __init__(self, seed: int = 0x1234_5678) -> None:
        self._state = seed & 0xFFFF_FFFF

    def next_float(self) -> float:
        # xorshift-ish; map to a small signed range so the transformer is stable.
        s = self._state
        s ^= (s << 13) & 0xFFFF_FFFF
        s ^= s >> 17
        s ^= (s << 5) & 0xFFFF_FFFF
        self._state = s & 0xFFFF_FFFF
        return (self._state / 0xFFFF_FFFF) * 0.2 - 0.1


def build_checkpoint(config: ModelConfig, *, weight_seed: int = 0x1234_5678,
                     weight_count_override: Optional[int] = None) -> bytes:
    """Serialize a full, well-formed checkpoint. ``weight_count_override`` lets a
    test emit the wrong number of weight floats (short/long file)."""
    header = struct.pack(
        "<7i",
        config.dim,
        config.hidden_dim,
        config.n_layers,
        config.n_heads,
        config.n_kv_heads,
        config.vocab_size,
        config.seq_len,
    )
    count = (weight_count_override if weight_count_override is not None
             else config.weight_float_count())
    rng = _Lcg(weight_seed)
    floats = [rng.next_float() for _ in range(count)]
    return header + struct.pack("<%df" % count, *floats)


def default_vocab(abs_vocab: int = 260) -> List[bytes]:
    """A vocab in the llama2.c convention used by the adapter's byte fallback:

    - id 0 ``<unk>``, id 1 ``<s>`` (BOS, injected directly), id 2 ``</s>`` (EOS)
    - ids 3..258 are the 256 raw-byte tokens ``<0x00>``..``<0xff>``
      (the adapter's byte fallback emits ``byte + 3``)
    - id 259 is a literal single space, which ``encode`` looks up as the dummy
      prefix for any non-empty prompt.
    Extra ids beyond 259 (if abs_vocab is larger) get short unique fillers.
    """
    vocab: List[bytes] = [b"<unk>", b"<s>", b"</s>"]
    for b in range(256):
        vocab.append(("<0x%02X>" % b).encode("ascii"))
    vocab.append(b" ")
    while len(vocab) < abs_vocab:
        vocab.append(("tok%d" % len(vocab)).encode("ascii"))
    if len(vocab) != abs_vocab:
        raise ValueError("vocab longer than abs_vocab=%d" % abs_vocab)
    return vocab


def build_tokenizer(vocab: List[bytes], *, max_token_length: Optional[int] = None,
                    scores: Optional[List[float]] = None,
                    length_override=None, truncate_last: int = 0) -> bytes:
    """Serialize a tokenizer file.

    - ``max_token_length`` defaults to the longest token (>=1).
    - ``length_override(i, actual_len) -> int`` can lie about a token's byte
      length to exercise the truncated / out-of-range string paths.
    - ``truncate_last`` drops this many bytes off the very end of the file to
      simulate a truncated strings table.
    """
    longest = max((len(v) for v in vocab), default=1)
    mtl = max_token_length if max_token_length is not None else max(longest, 1)
    out = bytearray(struct.pack("<i", mtl))
    for i, token in enumerate(vocab):
        score = 0.0 if scores is None else scores[i]
        out += struct.pack("<f", score)
        length = len(token)
        if length_override is not None:
            length = length_override(i, len(token))
        out += struct.pack("<i", length)
        out += token
    if truncate_last:
        del out[len(out) - truncate_last:]
    return bytes(out)


def scores_favoring(vocab: List[bytes]) -> List[float]:
    """Give each byte-token a small, distinct score so BPE merges are
    deterministic without biasing toward the BOS/EOS specials."""
    return [float(i) * 1e-3 for i in range(len(vocab))]
