"""Shared plumbing for the routes: the per-request store, and the error mapping.

There is no logic here beyond opening SQLite and turning a :class:`LearnerError` into the
right status code. Every route is a thin call into ``learner/api.py``.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from fastapi import Depends, Header, Request
from fastapi.responses import JSONResponse

from ..config import Settings
from ..learner.store import (
    IdempotencyConflict,
    LearnerError,
    StateConflict,
    Store,
    open_store,
)

#: The core raises one error type. These are the messages that mean "not here"; everything
#: else is a rejected request. Kept in one place, deliberately: the alternative is a
#: `NotFound` subclass at ~25 raise sites in modules the CLI already depends on.
#:
#: The quote matters. `unknown goal 'g'` is a missing thing (404); `unknown dispute type
#: 'I disagree'`, `unknown channel 'x'` and friends name a *qualifier* before the value and
#: are rejected arguments (400), so the noun has to be followed directly by the quoted id.
_NOT_FOUND = re.compile(
    r"""^(?:
        unknown\ (?:goal|session|item|node|misconception|dispute|table)\ ['"]
        |no\ such\ file
        |no\ goals\ yet
        |no\ blueprint\ for\ goal\ ['"]
        |no\ (?:suspected\ )?misconception\ ['"]
    )""",
    re.VERBOSE,
)


def status_for(exc: LearnerError) -> int:
    if isinstance(exc, IdempotencyConflict | StateConflict):
        return 409
    return 404 if _NOT_FOUND.match(str(exc)) else 400


def error_response(exc: LearnerError) -> JSONResponse:
    return JSONResponse({"error": str(exc)}, status_code=status_for(exc))


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_store(settings: Settings = Depends(get_settings)) -> Iterator[Store]:
    store = open_store(settings)
    try:
        yield store
    finally:
        store.close()


#: The header form of the idempotency key, for callers that would rather not touch the body.
IdempotencyHeader = Header(default=None, alias="Idempotency-Key")


def key_of(body: Any, header: str | None) -> str | None:
    """The body field wins; the ``Idempotency-Key`` header is the fallback."""

    return getattr(body, "idempotency_key", None) or header
