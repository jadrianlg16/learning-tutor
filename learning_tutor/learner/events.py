"""Append and query events. The events table is the truth; nothing here ever updates it."""

from __future__ import annotations

import json
from typing import Any

from .ids import ulid
from .models import Event
from .store import LearnerError, Store, utcnow

VALID_KINDS = {
    "answer",
    "teach_back",
    "probe_answer",
    "dispute",
    "graph_revision",
    "session_start",
    "session_end",
    "misconception_step",
    "note",
    # a flashcard flip rated by the learner (study tools): schedules, never evidence
    "card_review",
}
VALID_CHANNELS = {"claude-code", "agent", "telegram", "web"}
VALID_CONTEXTS = {"in-session", "delayed", "transfer", "probe"}
VALID_EVALUATION_METHODS = {"host_llm", "blind_solver", "rubric", "human", "self_report"}
#: Methods that may count toward mastery. ``host_llm`` is the model grading its own
#: learner: recorded, shown as self-graded, never counted (CONTRACTS.md, "Adopted from
#: the Tutor MCP audit"). ``self_report`` — the learner rating their own flashcard — is
#: recorded for scheduling and is never trusted either.
TRUSTED_EVALUATION_METHODS = {"blind_solver", "rubric", "human"}

#: A pass recorded at this assistance level or above never counts toward mastery.
UNEARNED_ASSISTANCE = 5
#: A pass at this level or below counts as independent evidence.
INDEPENDENT_ASSISTANCE = 1


def append(
    store: Store,
    *,
    kind: str,
    session_id: str | None = None,
    goal_id: str | None = None,
    node_id: str | None = None,
    item_version_id: str | None = None,
    response: str | None = None,
    correct: int | None = None,
    confidence: int | None = None,
    idk: bool = False,
    assistance_level: int = 0,
    channel: str = "claude-code",
    context: str = "in-session",
    prompt_version: str | None = None,
    grader_version: str | None = None,
    evaluation_method: str | None = None,
    payload: dict[str, Any] | None = None,
    ts: str | None = None,
) -> Event:
    if kind not in VALID_KINDS:
        raise LearnerError(f"unknown event kind {kind!r}")
    if channel not in VALID_CHANNELS:
        raise LearnerError(f"unknown channel {channel!r}")
    if context not in VALID_CONTEXTS:
        raise LearnerError(f"unknown context {context!r}")
    if not 0 <= assistance_level <= 6:
        raise LearnerError("assistance level must be 0-6")
    if confidence is not None and not 1 <= confidence <= 5:
        raise LearnerError("confidence must be 1-5")
    if evaluation_method is not None and evaluation_method not in VALID_EVALUATION_METHODS:
        raise LearnerError(
            f"unknown evaluation method {evaluation_method!r}: one of "
            + "|".join(sorted(VALID_EVALUATION_METHODS))
        )

    event_id = ulid()
    row = {
        "event_id": event_id,
        "ts": ts or utcnow(),
        "session_id": session_id,
        "goal_id": goal_id,
        "node_id": node_id,
        "item_version_id": item_version_id,
        "kind": kind,
        "response": response,
        "correct": None if correct is None else int(correct),
        "confidence": confidence,
        "idk": int(bool(idk)),
        "assistance_level": int(assistance_level),
        "channel": channel,
        "context": context,
        "prompt_version": prompt_version,
        "grader_version": grader_version,
        "evaluation_method": evaluation_method,
        "payload": json.dumps(payload) if payload is not None else None,
    }
    store.insert("events", row)
    return to_model(row)


def to_model(row: Any) -> Event:
    data = dict(row)
    payload = data.get("payload")
    data["payload"] = json.loads(payload) if payload else None
    return Event.model_validate(data)


def query(
    store: Store,
    *,
    node_id: str | None = None,
    session_id: str | None = None,
    goal_id: str | None = None,
    kinds: list[str] | None = None,
    since: str | None = None,
    before: str | None = None,
    limit: int | None = None,
    newest_first: bool = False,
) -> list[Event]:
    sql = "SELECT * FROM events WHERE 1=1"
    params: list[Any] = []
    if node_id:
        sql += " AND node_id = ?"
        params.append(node_id)
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    if goal_id:
        sql += " AND goal_id = ?"
        params.append(goal_id)
    if kinds:
        sql += " AND kind IN ({})".format(", ".join("?" for _ in kinds))
        params.extend(kinds)
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    if before:
        sql += " AND ts < ?"
        params.append(before)
    # ``newest_first`` has to reach the SQL, not a reversal afterwards: with a LIMIT, an
    # ascending query returns the *oldest* n rows, which is the opposite of what a reader
    # asking for "the last 50 events" wants.
    sql += " ORDER BY ts DESC, event_id DESC" if newest_first else " ORDER BY ts ASC, event_id ASC"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return [to_model(row) for row in store.query(sql, params)]


def counts_by_kind(store: Store) -> dict[str, int]:
    rows = store.query("SELECT kind, COUNT(*) AS n FROM events GROUP BY kind")
    return {row["kind"]: row["n"] for row in rows}


def is_trusted(evaluation_method: str | None) -> bool:
    """Whether an evaluation may count toward mastery.

    ``None`` is an event written before migration 2 added the column: there is no record
    of how it was judged, and a migration must not retroactively invalidate a history it
    knows nothing about, so it keeps the Stage 0 meaning (it counts). Everything written
    from Stage 1 on carries an explicit method, because the CLI, HTTP and MCP surfaces all
    default it to ``host_llm``.
    """

    return evaluation_method is None or evaluation_method in TRUSTED_EVALUATION_METHODS


def is_pass(event: Event) -> bool:
    """A pass that counts: correct, not 'I don't know', assistance below the unearned bar."""

    return (
        bool(event.correct)
        and not event.idk
        and event.assistance_level < UNEARNED_ASSISTANCE
        and is_trusted(event.evaluation_method)
    )


def is_independent(event: Event) -> bool:
    return is_pass(event) and event.assistance_level <= INDEPENDENT_ASSISTANCE
