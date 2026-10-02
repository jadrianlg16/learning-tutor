"""Typed disputes — the open learner model.

Six types, from IDEA.md (*Typed disputes*). Every one creates a transparent record settled
by evidence; **none of them ever grants mastery**. In particular "I already know this"
schedules a two-item check and nothing else: the node state still comes from the evidence
rules, and the check has to actually be passed.
"""

from __future__ import annotations

import json
from typing import Any

from . import events as events_mod
from . import items as items_mod
from .ids import prefixed
from .store import LearnerError, Store, utcnow

TYPES = (
    "I already know this",
    "ambiguous question",
    "misclick",
    "not on my exam",
    "this edge is wrong",
    "test me instead",
)
#: how many items the "I already know this" / "test me instead" check schedules
CHECK_ITEMS = 2


def _row(store: Store, dispute_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,))
    if not row:
        raise LearnerError(f"unknown dispute {dispute_id!r}")
    data = dict(row)
    data["check_items"] = json.loads(data["check_items"]) if data["check_items"] else []
    return data


def get(store: Store, dispute_id: str) -> dict[str, Any]:
    """One dispute row, with ``check_items`` parsed. Raises if it does not exist."""

    return _row(store, dispute_id)


def _pick_check_items(store: Store, node_id: str | None) -> list[str]:
    if not node_id:
        return []
    pool = items_mod.for_node(
        store, node_id, statuses=("MASTERY_ELIGIBLE", "PRACTICE_EVIDENCE"), include_holdouts=False
    )
    return [item.item_id for item in pool[:CHECK_ITEMS]]


def open_dispute(
    store: Store,
    *,
    dispute_type: str,
    node_id: str | None = None,
    item_id: str | None = None,
    note: str | None = None,
    session_id: str | None = None,
    goal_id: str | None = None,
) -> dict[str, Any]:
    if dispute_type not in TYPES:
        raise LearnerError(f"unknown dispute type {dispute_type!r}; one of: {'; '.join(TYPES)}")
    check_items: list[str] = []
    if dispute_type in ("I already know this", "test me instead"):
        check_items = _pick_check_items(store, node_id)
    did = prefixed("d")
    store.insert(
        "disputes",
        {
            "dispute_id": did,
            "type": dispute_type,
            "node_id": node_id,
            "item_id": item_id,
            "note": note,
            "status": "open",
            "outcome": None,
            "evidence": None,
            "check_items": json.dumps(check_items),
            "created_at": utcnow(),
            "settled_at": None,
        },
    )
    events_mod.append(
        store,
        kind="dispute",
        session_id=session_id,
        goal_id=goal_id,
        node_id=node_id,
        payload={
            "dispute_id": did,
            "type": dispute_type,
            "status": "open",
            "item_id": item_id,
            "check_items": check_items,
            "note": note,
        },
    )
    result = _row(store, did)
    result["mastery_granted"] = False
    if dispute_type in ("I already know this", "test me instead"):
        result["next_action"] = (
            f"answer {CHECK_ITEMS} check items on this node; a claim is not evidence"
        )
        if len(check_items) < CHECK_ITEMS:
            result["warning"] = (
                "fewer than two validated items exist on this node; author and validate "
                "items before the check can settle the dispute"
            )
    return result


def settle(
    store: Store, dispute_id: str, *, outcome: str, evidence: str | None = None
) -> dict[str, Any]:
    if outcome not in ("upheld", "rejected"):
        raise LearnerError("outcome must be upheld or rejected")
    record = _row(store, dispute_id)
    if record["status"] == "settled":
        raise LearnerError(f"dispute {dispute_id} is already settled ({record['outcome']})")
    store.update(
        "disputes",
        {"dispute_id": dispute_id},
        {
            "status": "settled",
            "outcome": outcome,
            "evidence": evidence,
            "settled_at": utcnow(),
        },
    )
    # Note: an upheld "this edge is wrong" does not touch the graph by itself; the edge is
    # removed by an explicit `graph revise` op, so every graph write stays versioned.
    events_mod.append(
        store,
        kind="dispute",
        node_id=record["node_id"],
        payload={
            "dispute_id": dispute_id,
            "type": record["type"],
            "status": "settled",
            "outcome": outcome,
            "evidence": evidence,
        },
    )
    settled = _row(store, dispute_id)
    settled["mastery_granted"] = False
    settled["note_on_mastery"] = (
        "a settled dispute never sets node state; state is recomputed from evidence"
    )
    return settled


def open_for_node(store: Store, node_id: str) -> list[dict[str, Any]]:
    return [
        _row(store, r["dispute_id"])
        for r in store.query(
            "SELECT dispute_id FROM disputes WHERE node_id = ? AND status = 'open' "
            "ORDER BY created_at",
            (node_id,),
        )
    ]


def open_disputes(store: Store) -> list[dict[str, Any]]:
    return [
        _row(store, r["dispute_id"])
        for r in store.query(
            "SELECT dispute_id FROM disputes WHERE status = 'open' ORDER BY created_at"
        )
    ]


def stats(store: Store) -> dict[str, Any]:
    rows = store.query(
        "SELECT status, outcome, COUNT(*) AS n FROM disputes GROUP BY status, outcome"
    )
    total = sum(r["n"] for r in rows)
    upheld = sum(r["n"] for r in rows if r["outcome"] == "upheld")
    settled = sum(r["n"] for r in rows if r["status"] == "settled")
    return {
        "disputes": total,
        "settled": settled,
        "upheld": upheld,
        "learner_was_right_percent": round(100 * upheld / settled) if settled else None,
    }
