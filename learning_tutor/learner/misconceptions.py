"""Misconceptions: a hypothesis until confirmed.

State machine (CONTRACTS.md): ``suspected -> active -> weakened -> resolved -> recurred``.

A high-confidence wrong answer *suspects* a misconception. It only becomes ``active`` — the
only state that reaches ``learner.md`` and the only one that colours a node — after the
full confirmation sequence, in order, each step ``held``:

1. ``reasoning``       — the learner explains their reasoning and it matches the claim.
2. ``prediction``      — a reworded prediction that the claim implies, and they make it.
3. ``counterexample``  — a discriminating case; the claim survives it.

Any step recorded as ``dropped`` ends the hypothesis immediately: the row moves to
``resolved`` with ``resolution_reason = "dropped at <step>"``. (CONTRACTS.md's state list
has no ``dropped`` state, so a dropped hypothesis is stored as ``resolved`` with the reason
attached rather than inventing a sixth state.)

After ``active``: an independent pass on the node weakens it (``weakened``), ``resolve``
closes it, and a later failure on a node with a resolved misconception marks it
``recurred`` — which counts as active again for state derivation.
"""

from __future__ import annotations

import json
from typing import Any

from . import events as events_mod
from .ids import prefixed
from .store import LearnerError, Store, utcnow

STEPS = ("reasoning", "prediction", "counterexample")
STATES = ("suspected", "active", "weakened", "resolved", "recurred")
#: states that make a node show as `misconception`
ACTIVE_STATES = ("active", "recurred")
#: confidence at or above this on a wrong answer is what "high-confidence wrong" means
HIGH_CONFIDENCE = 4


def _row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["steps"] = json.loads(data["steps"]) if data["steps"] else []
    return data


def to_dict(row: Any) -> dict[str, Any]:
    """A ``misconceptions`` row as JSON, with ``steps`` parsed."""

    return _row_to_dict(row)


def get(store: Store, misconception_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM misconceptions WHERE misconception_id = ?", (misconception_id,))
    if not row:
        raise LearnerError(f"unknown misconception {misconception_id!r}")
    return _row_to_dict(row)


def for_node(store: Store, node_id: str) -> list[dict[str, Any]]:
    return [
        _row_to_dict(r)
        for r in store.query(
            "SELECT * FROM misconceptions WHERE node_id = ? ORDER BY created_at", (node_id,)
        )
    ]


def active_for_node(
    store: Store, node_id: str, *, before: str | None = None
) -> dict[str, Any] | None:
    """The active (or recurred) misconception on a node, if any.

    With ``before`` the state is reconstructed from the ``misconception_step`` events, so
    historical state derivation (the false-mastery metric) stays honest.
    """

    if before is None:
        marks = ", ".join("?" for _ in ACTIVE_STATES)
        row = store.one(
            f"SELECT * FROM misconceptions WHERE node_id = ? AND state IN ({marks}) "
            "ORDER BY updated_at DESC LIMIT 1",
            (node_id, *ACTIVE_STATES),
        )
        return _row_to_dict(row) if row else None

    history: dict[str, dict[str, Any]] = {}
    for event in events_mod.query(
        store, node_id=node_id, kinds=["misconception_step"], before=before
    ):
        payload = event.payload or {}
        mid = payload.get("misconception_id")
        if not mid:
            continue
        history[mid] = {"claim": payload.get("claim", ""), "state": payload.get("state", "")}
    for mid, entry in history.items():
        if entry["state"] in ACTIVE_STATES:
            return {"misconception_id": mid, "claim": entry["claim"], "state": entry["state"]}
    return None


def _record(
    store: Store,
    row: dict[str, Any],
    note: str | None = None,
    *,
    session_id: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
) -> None:
    events_mod.append(
        store,
        kind="misconception_step",
        node_id=row["node_id"],
        session_id=session_id,
        prompt_version=prompt_version,
        grader_version=grader_version,
        payload={
            "misconception_id": row["misconception_id"],
            "claim": row["claim"],
            "state": row["state"],
            "steps": row["steps"],
            "note": note,
        },
    )


def suspect(
    store: Store,
    node_id: str,
    claim: str,
    *,
    session_id: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
) -> dict[str, Any]:
    if not claim:
        raise LearnerError("a misconception needs a claim")
    existing = store.one(
        "SELECT * FROM misconceptions WHERE node_id = ? AND claim = ?", (node_id, claim)
    )
    if existing:
        row = _row_to_dict(existing)
        if row["state"] == "resolved":
            _set_state(
                store,
                row["misconception_id"],
                "recurred",
                reason="observed again",
                session_id=session_id,
                prompt_version=prompt_version,
                grader_version=grader_version,
            )
            return get(store, row["misconception_id"])
        return row
    now = utcnow()
    mid = prefixed("m")
    store.insert(
        "misconceptions",
        {
            "misconception_id": mid,
            "node_id": node_id,
            "claim": claim,
            "state": "suspected",
            "steps": json.dumps([]),
            "resolution_reason": None,
            "created_at": now,
            "updated_at": now,
        },
    )
    row = get(store, mid)
    _record(
        store,
        row,
        note="suspected",
        session_id=session_id,
        prompt_version=prompt_version,
        grader_version=grader_version,
    )
    return row


def _set_state(
    store: Store,
    misconception_id: str,
    state: str,
    *,
    reason: str | None = None,
    session_id: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
) -> dict[str, Any]:
    if state not in STATES:
        raise LearnerError(f"unknown misconception state {state!r}")
    store.update(
        "misconceptions",
        {"misconception_id": misconception_id},
        {"state": state, "updated_at": utcnow(), "resolution_reason": reason},
    )
    row = get(store, misconception_id)
    _record(
        store,
        row,
        note=reason,
        session_id=session_id,
        prompt_version=prompt_version,
        grader_version=grader_version,
    )
    return row


def confirm_step(
    store: Store,
    node_id: str,
    claim: str,
    *,
    step: str,
    outcome: str,
    note: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
) -> dict[str, Any]:
    if step not in STEPS:
        raise LearnerError(f"step must be one of {', '.join(STEPS)}")
    if outcome not in ("held", "dropped"):
        raise LearnerError("outcome must be held or dropped")
    row = store.one(
        "SELECT * FROM misconceptions WHERE node_id = ? AND claim = ?", (node_id, claim)
    )
    if not row:
        raise LearnerError(
            f"no suspected misconception {claim!r} on {node_id}: run `misconception suspect` first"
        )
    record = _row_to_dict(row)
    if record["state"] in ACTIVE_STATES:
        raise LearnerError(f"{record['misconception_id']} is already {record['state']}")
    steps = [s for s in record["steps"] if s["outcome"] == "held"]
    expected = STEPS[len(steps)] if len(steps) < len(STEPS) else None
    if expected is None:
        raise LearnerError("confirmation sequence is already complete")
    if step != expected:
        raise LearnerError(
            f"confirmation is ordered: expected {expected!r} next, got {step!r} "
            f"(held so far: {[s['step'] for s in steps] or 'none'})"
        )
    record["steps"].append({"step": step, "outcome": outcome, "ts": utcnow(), "note": note})
    store.update(
        "misconceptions",
        {"misconception_id": record["misconception_id"]},
        {"steps": json.dumps(record["steps"]), "updated_at": utcnow()},
    )
    versions = {"prompt_version": prompt_version, "grader_version": grader_version}
    if outcome == "dropped":
        return _set_state(
            store,
            record["misconception_id"],
            "resolved",
            reason=f"dropped at {step}",
            **versions,
        )
    held = [s for s in record["steps"] if s["outcome"] == "held"]
    if len(held) == len(STEPS):
        return _set_state(
            store, record["misconception_id"], "active", reason="confirmed", **versions
        )
    updated = get(store, record["misconception_id"])
    _record(store, updated, note=f"{step} held", **versions)
    return updated


def weaken(
    store: Store, misconception_id: str, *, reason: str = "independent pass"
) -> dict[str, Any]:
    row = get(store, misconception_id)
    if row["state"] not in ACTIVE_STATES:
        return row
    return _set_state(store, misconception_id, "weakened", reason=reason)


def resolve(
    store: Store,
    node_id: str,
    claim: str,
    *,
    reason: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
) -> dict[str, Any]:
    row = store.one(
        "SELECT * FROM misconceptions WHERE node_id = ? AND claim = ?", (node_id, claim)
    )
    if not row:
        raise LearnerError(f"no misconception {claim!r} on {node_id}")
    return _set_state(
        store,
        row["misconception_id"],
        "resolved",
        reason=reason or "resolved",
        prompt_version=prompt_version,
        grader_version=grader_version,
    )


def on_answer(
    store: Store,
    node_id: str,
    *,
    correct: bool,
    assistance_level: int,
    confidence: int | None,
    chosen_option: str | None,
    distractor_map: dict[str, str] | None,
) -> list[dict[str, Any]]:
    """React to an answer: suspect, weaken or mark a recurrence. Never confirms anything.

    Confirmation is always the three-step sequence, driven explicitly by the tutor.
    """

    touched: list[dict[str, Any]] = []
    if not correct and chosen_option and distractor_map:
        claim = distractor_map.get(chosen_option)
        if claim and confidence is not None and confidence >= HIGH_CONFIDENCE:
            touched.append(suspect(store, node_id, claim))
    if not correct:
        for row in for_node(store, node_id):
            dropped = (row["resolution_reason"] or "").startswith("dropped at")
            if row["state"] == "resolved" and not dropped:
                touched.append(
                    _set_state(store, row["misconception_id"], "recurred", reason="failed again")
                )
    if correct and assistance_level <= 1:
        for row in for_node(store, node_id):
            if row["state"] in ACTIVE_STATES:
                touched.append(weaken(store, row["misconception_id"]))
    return touched
