"""Stable, sortable identifiers.

- ``ulid()`` — time-ordered id for events (lexicographic order == chronological order).
- ``node_id(title)`` — ``n_<slug>_<4hex>`` where the hex is a hash of the *slug*, so the
  same concept name always produces the same id in any database. Collisions inside one
  graph are resolved by the caller.
"""

from __future__ import annotations

import hashlib
import os
import re
import time
import unicodedata

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def _b32(value: int, length: int) -> str:
    out = []
    for _ in range(length):
        out.append(_CROCKFORD[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


_LAST: dict[str, int] = {"ms": 0, "rand": 0}


def ulid() -> str:
    """A monotonic ULID: 48-bit millisecond timestamp + 80 bits of randomness.

    Monotonic within a process: two ids minted in the same millisecond still sort in the
    order they were minted, so ``ORDER BY ts, event_id`` is the order things happened even
    though ``ts`` only has second resolution.
    """

    ms = int(time.time() * 1000)
    if ms == _LAST["ms"]:
        rand = _LAST["rand"] + 1
    else:
        rand = int.from_bytes(os.urandom(10), "big") >> 1
    _LAST["ms"], _LAST["rand"] = ms, rand
    return _b32(ms, 10) + _b32(rand, 16)


def slugify(text: str, max_len: int = 40) -> str:
    normalised = unicodedata.normalize("NFKD", text)
    ascii_text = normalised.encode("ascii", "ignore").decode("ascii").lower()
    slug = _SLUG_STRIP.sub("_", ascii_text).strip("_")
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("_")
    return slug or "node"


def short_hash(text: str, length: int = 4) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def node_id(title: str) -> str:
    slug = slugify(title)
    return f"n_{slug}_{short_hash(slug)}"


def item_id() -> str:
    return f"i_{ulid().lower()}"


def prefixed(prefix: str) -> str:
    return f"{prefix}_{ulid().lower()}"


def hash_fraction(text: str) -> float:
    """Deterministic value in [0, 1) derived from ``text``. Used for holdout assignment."""

    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return int(digest, 16) / float(1 << 32)


def normalise_alias(text: str) -> str:
    return slugify(text, max_len=200)
