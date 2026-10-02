"""Embeddings: Ollama when it is there, a deterministic offline hash when it is not.

``LT_EMBED_MODEL`` picks the backend:

* ``hash`` — hashed bag-of-words into ``LT_EMBED_DIM`` (default 256) float32 dimensions.
  Deterministic, offline, no dependencies. Tests and Docker builds use it so nothing in the
  corpus module ever needs a network to run.
* anything else (default ``nomic-embed-text``) — POST ``{OLLAMA_URL}/api/embeddings``.

Vectors are stored as little-endian float32 blobs, L2-normalised at write time so cosine
similarity is a plain dot product at read time.
"""

from __future__ import annotations

import array
import hashlib
import math
import os
import re
import sys
from typing import Any

DEFAULT_MODEL = "nomic-embed-text"
HASH_MODEL = "hash"
DEFAULT_HASH_DIM = 256
DEFAULT_OLLAMA_URL = "http://localhost:11434"

_WORD = re.compile(r"[a-z0-9]+")


class EmbeddingError(Exception):
    """The configured embedding backend could not be reached or returned nothing."""


def _env(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def model_name() -> str:
    return _env("LT_EMBED_MODEL", DEFAULT_MODEL)


def hash_dim() -> int:
    try:
        return max(16, int(_env("LT_EMBED_DIM", str(DEFAULT_HASH_DIM))))
    except ValueError:
        return DEFAULT_HASH_DIM


def ollama_url() -> str:
    return _env("OLLAMA_URL", DEFAULT_OLLAMA_URL).rstrip("/")


def is_offline_backend(model: str | None = None) -> bool:
    return (model or model_name()) == HASH_MODEL


# --- the offline backend -------------------------------------------------


def _tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def hash_embed(text: str, dim: int | None = None) -> list[float]:
    """Signed hashed bag-of-words. Same text, same machine or another: same vector.

    Unigrams plus adjacent bigrams, so word order carries a little signal. Counts are
    sub-linear (1 + log count) to keep a repeated word from dominating, then L2-normalised.
    """

    dim = dim or hash_dim()
    tokens = _tokens(text)
    grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]
    counts: dict[str, int] = {}
    for gram in grams:
        counts[gram] = counts.get(gram, 0) + 1

    vec = [0.0] * dim
    for gram, count in counts.items():
        digest = hashlib.blake2b(gram.encode("utf-8"), digest_size=8).digest()
        raw = int.from_bytes(digest, "big")
        index = raw % dim
        sign = 1.0 if (raw >> 63) & 1 else -1.0
        vec[index] += sign * (1.0 + math.log(count))
    return normalise(vec)


# --- the Ollama backend --------------------------------------------------


def ollama_embed(texts: list[str], *, model: str, timeout: float = 30.0) -> list[list[float]]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise EmbeddingError(
            "httpx is required to reach Ollama; set LT_EMBED_MODEL=hash for the offline backend"
        ) from exc

    url = f"{ollama_url()}/api/embeddings"
    out: list[list[float]] = []
    try:
        with httpx.Client(timeout=timeout) as client:
            for text in texts:
                response = client.post(url, json={"model": model, "prompt": text})
                response.raise_for_status()
                payload = response.json()
                vector = payload.get("embedding") or []
                if not vector:
                    raise EmbeddingError(f"Ollama returned no embedding for model {model!r}")
                out.append(normalise([float(value) for value in vector]))
    except EmbeddingError:
        raise
    except Exception as exc:  # httpx.HTTPError, JSON errors, connection refused
        raise EmbeddingError(f"embedding request to {url} failed: {exc}") from exc
    return out


# --- public API ----------------------------------------------------------


def embed_texts(texts: list[str], *, model: str | None = None) -> list[list[float]]:
    model = model or model_name()
    if is_offline_backend(model):
        dim = hash_dim()
        return [hash_embed(text, dim) for text in texts]
    return ollama_embed(texts, model=model)


def embed_text(text: str, *, model: str | None = None) -> list[float]:
    return embed_texts([text], model=model)[0]


def normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in vec))
    if norm <= 0:
        return vec
    return [value / norm for value in vec]


def to_blob(vec: list[float]) -> bytes:
    """float32 little-endian, one value per dimension."""

    buf = array.array("f", vec)
    if array.array("f").itemsize != 4:  # pragma: no cover - defensive
        raise EmbeddingError("platform float is not 4 bytes")
    if sys.byteorder == "big":  # pragma: no cover - x86/arm are little-endian
        buf.byteswap()
    return buf.tobytes()


def from_blob(blob: bytes, dim: int | None = None) -> list[float]:
    buf = array.array("f")
    buf.frombytes(blob)
    if sys.byteorder == "big":  # pragma: no cover
        buf.byteswap()
    values = list(buf)
    if dim is not None and len(values) != dim:
        values = values[:dim] + [0.0] * max(0, dim - len(values))
    return values


def cosine(a: list[float], b: list[float]) -> float:
    """Vectors are stored normalised, so this is a dot product with a safety net."""

    if not a or not b:
        return 0.0
    size = min(len(a), len(b))
    dot = sum(a[i] * b[i] for i in range(size))
    na = math.sqrt(sum(value * value for value in a[:size]))
    nb = math.sqrt(sum(value * value for value in b[:size]))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def backend_info() -> dict[str, Any]:
    model = model_name()
    offline = is_offline_backend(model)
    return {
        "model": model,
        "backend": "hash" if offline else "ollama",
        "dim": hash_dim() if offline else None,
        "url": None if offline else ollama_url(),
        "needs_network": not offline,
    }
