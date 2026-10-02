"""Idempotent replay for mutating calls.

Borrowed shape, not code, from the open-source Tutor MCP server's tool contract: every
mutation takes an optional ``idempotency_key``.

* no key                       -> run the call, store nothing
* key, never seen              -> run the call, store ``(key, sha256(body), response)``
* key seen, **same** body      -> replay the stored response verbatim, run nothing
* key seen, **different** body -> :class:`IdempotencyConflict` (HTTP 409)

The body hash is over a canonical JSON rendering (sorted keys, no insignificant
whitespace), so argument order never changes the digest. Only the successful response is
stored: a call that raised is not recorded, so a retry after a failure runs again.

Why this exists: the Telegram reminder job and agent hosts retry. Without a replay table a retried
``record answer`` writes a second event into an append-only table, and no later fold can
tell the duplicate from a genuine second attempt.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any

from .store import IdempotencyConflict, Store, utcnow


def body_digest(body: Any) -> str:
    """sha256 of a canonical JSON rendering of the request body."""

    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def lookup(store: Store, key: str) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM idempotency WHERE key = ?", (key,))
    return dict(row) if row else None


def run(
    store: Store,
    *,
    key: str | None,
    operation: str,
    body: dict[str, Any],
    call: Callable[[], Any],
) -> Any:
    """Run ``call`` under ``key``, replaying a previous response when one matches."""

    if not key:
        return call()

    digest = body_digest(body)
    row = lookup(store, key)
    if row:
        if row["body_sha256"] != digest:
            raise IdempotencyConflict(
                f"idempotency key {key!r} was already used for a different "
                f"{row['operation']} body"
            )
        return json.loads(row["response"])

    result = call()
    store.insert(
        "idempotency",
        {
            "key": key,
            "operation": operation,
            "body_sha256": digest,
            "response": json.dumps(result, default=str),
            "created_at": utcnow(),
        },
    )
    return result


def was_replayed(store: Store, key: str | None, before: dict[str, Any] | None) -> bool:
    """True when ``run`` served a stored response rather than executing the call.

    ``before`` is the row (or ``None``) that :func:`lookup` returned *before* the call.
    Transports use this to set a replay marker without changing the response body.
    """

    return bool(key) and before is not None
