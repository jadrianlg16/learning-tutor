"""Talks to the learner model: the ``learner`` CLI (shelled out to, JSON in/out) or the
``learner-svc`` HTTP service when ``LT_LEARNER_URL`` is set — plus one read-only lookup
neither surface exposes yet.

Why the read-only lookup exists: ``learner next`` (and ``GET /v1/next``) deliberately never
return an item's answer key (CONTRACTS.md hard rule 1 — never reveal before an attempt), so
grading a Telegram tap and writing 1-3 lines of feedback afterwards needs the stem's answer
and distractor misconceptions from somewhere. There is no ``learner item show`` (or an HTTP
equivalent — checked against ``learning_tutor/learner_svc/routes/authoring.py``, which has
no ``GET /v1/items/{id}``) to ask for it. Rather than invent durable state or duplicate the
answer key into ``telegram/state.json`` at push time (which would make state.json a second
copy of learner data that could drift), :func:`read_item_version` opens
``learner/events.db`` **read-only** (``sqlite3`` URI mode, no write access) and reads the one
row it needs — the same read a ``learner item show`` command would do once one exists. This
never writes, so it does not violate "durable state is written only through tools"
(CONTRACTS.md hard rule 5). It also assumes the data directory is on the same filesystem as
this job even when ``LT_LEARNER_URL`` points at a remote ``learner-svc`` — true for the
single-box Stage 1 deployment this repo targets, flagged in docs/modules/telegram.md as a
limitation for a future networked one.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx

from .config import TelegramSettings
from .config import data_dir as _data_dir


class LearnerError(RuntimeError):
    """The ``learner`` CLI (or learner-svc) rejected a call."""


def _run_cli(args: list[str]) -> dict[str, Any]:
    cmd = [sys.executable, "-m", "learning_tutor.cli", *args, "--json"]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "learner CLI failed"
        try:
            message = json.loads(result.stderr.strip().splitlines()[-1])["error"]
        except (json.JSONDecodeError, IndexError, KeyError):
            pass
        raise LearnerError(str(message))
    text = result.stdout.strip()
    return json.loads(text) if text else {}


def _http_get(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    try:
        resp = httpx.get(url, params=params, timeout=10)
    except httpx.HTTPError as exc:
        raise LearnerError(f"learner-svc request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise LearnerError(f"learner-svc request failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


def _http_post(
    url: str, body: dict[str, Any], *, idempotency_key: str | None = None
) -> dict[str, Any]:
    headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
    try:
        resp = httpx.post(url, json=body, headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        raise LearnerError(f"learner-svc request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise LearnerError(f"learner-svc request failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


# --------------------------------------------------------------------------- candidates
def get_candidates(settings: TelegramSettings, *, n: int = 5) -> list[dict[str, Any]]:
    """The ``review``-mode picks, already in learner.md's priority order.

    Uses ``GET /v1/next`` when ``LT_LEARNER_URL`` is set (Stage 1 service), otherwise
    shells out to ``learner next --mode review``.
    """

    if settings.learner_url:
        url = f"{settings.learner_url.rstrip('/')}/v1/next"
        params = {"mode": "review", "n": n, "goal": settings.goal}
        data = _http_get(url, params)
    else:
        args = ["next", "--mode", "review", "--n", str(n)]
        if settings.goal:
            args += ["--goal", settings.goal]
        data = _run_cli(args)
    return list(data.get("picks") or [])


# --------------------------------------------------------------------------- recording
_EVALUATION_METHOD_SUPPORTED: bool | None = None


#: ANSI escape sequences (colour, bold, cursor) that a forced-terminal help renderer emits
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
#: env vars that make Typer/Rich render help as a colour terminal even when piped
#: (``GITHUB_ACTIONS`` among them: CI runs would otherwise get styled help text)
_FORCE_TERMINAL_VARS = (
    "GITHUB_ACTIONS",
    "FORCE_COLOR",
    "PY_COLORS",
    "CLICOLOR_FORCE",
    "TTY_COMPATIBLE",
)


def _plain_help_env() -> dict[str, str]:
    """The environment for reading CLI help as plain text: no forced terminal, no colour,
    and wide enough that a long option name is never wrapped or truncated."""

    env = {k: v for k, v in os.environ.items() if k not in _FORCE_TERMINAL_VARS}
    env.update({"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"})
    return env


def _supports_evaluation_method() -> bool:
    """Feature-detect ``record answer --evaluation-method``.

    Present in this repo's CLI (``learner record answer --help`` lists it), but detected
    here rather than hardcoded so this job degrades gracefully if it is ever missing (an
    older checkout, a pinned release) instead of crashing every tap on an unknown-option
    error. The help is read as plain text: styled output splits ``--evaluation-method`` into
    separately coloured fragments, and a substring check on that text silently fails.
    """

    global _EVALUATION_METHOD_SUPPORTED
    if _EVALUATION_METHOD_SUPPORTED is None:
        result = subprocess.run(
            [sys.executable, "-m", "learning_tutor.cli", "record", "answer", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=_plain_help_env(),
        )
        _EVALUATION_METHOD_SUPPORTED = "--evaluation-method" in _ANSI.sub("", result.stdout)
    return _EVALUATION_METHOD_SUPPORTED


def record_answer(
    settings: TelegramSettings,
    *,
    session_id: str | None,
    item_id: str,
    response: str | None,
    correct: bool,
    idk: bool = False,
    assistance: int = 0,
    context: str = "delayed",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record one resolved tap as ``channel=telegram``, ``evaluation_method=human``.

    A tap is graded by exact comparison against the stored answer key (see
    telegram/formatting.py's ``resolve_answer_letter``), never by an LLM judging itself —
    that is genuinely ``human`` evidence, not the ``host_llm`` default. ``idempotency_key``
    (the push token is a good choice) makes a retried ``poll`` tick safe to call again.
    """

    if settings.learner_url:
        url = f"{settings.learner_url.rstrip('/')}/v1/events"
        body: dict[str, Any] = {
            "kind": "answer",
            "session_id": session_id,
            "item_id": item_id,
            "response": response,
            "correct": correct,
            "idk": idk,
            "assistance_level": assistance,
            "context": context,
            "channel": "telegram",
            "evaluation_method": "human",
        }
        return _http_post(url, body, idempotency_key=idempotency_key)

    args = ["record", "answer", "--item", item_id, "--correct", "1" if correct else "0"]
    if session_id:
        args += ["--session", session_id]
    if response is not None:
        args += ["--response", response]
    if idk:
        args += ["--idk"]
    args += ["--assistance", str(assistance), "--context", context, "--channel", "telegram"]
    if _supports_evaluation_method():
        args += ["--evaluation-method", "human"]
    if idempotency_key:
        args += ["--idempotency-key", idempotency_key]
    return _run_cli(args)


# --------------------------------------------------------------------------- item lookup
def read_item_version(item_version_id: str) -> dict[str, Any] | None:
    """Read-only: stem/options/answer/distractors/surface_form for one item version.

    See the module docstring for why this bypasses the CLI. Opens the SQLite file in
    read-only URI mode so a bug here can never write to the learner's store.
    """

    db_path = Path(_data_dir()) / "learner" / "events.db"
    if not db_path.exists():
        return None
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT stem, options, answer, distractor_misconceptions, surface_form "
            "FROM item_versions WHERE item_version_id = ?",
            (item_version_id,),
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return None
    return {
        "stem": row["stem"],
        "options": json.loads(row["options"]),
        "answer": row["answer"],
        "distractor_misconceptions": json.loads(row["distractor_misconceptions"]),
        "surface_form": row["surface_form"],
    }


__all__ = [
    "LearnerError",
    "get_candidates",
    "record_answer",
    "read_item_version",
]
