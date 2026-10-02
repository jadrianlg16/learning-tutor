"""Hidden holdouts — the only items the delayed and transfer metrics are computed on.

A holdout is a flag on a ``MASTERY_ELIGIBLE`` item, assigned deterministically at promotion
(see ``items.assign_holdout``). Holdouts are **never** returned by ``learner next`` in any
mode; the only door is ``learner holdout-check``.

A holdout is due when its node has evidence (state is not ``unknown``) and it has not been
served for ``LT_HOLDOUT_DELAY_DAYS`` (default 7) — measured from the last check, or from
the promotion date if it has never been served. A holdout whose item version carries a
``surface_form`` is served as a ``transfer`` check; every other holdout is a ``delayed``
check.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from . import evidence as evidence_mod
from . import graph as graph_mod
from . import items as items_mod
from .models import Pick
from .store import Store, parse_ts, utcnow


def _age_days(ts: str | None, now: datetime) -> float | None:
    if not ts:
        return None
    return (now - parse_ts(ts)).total_seconds() / 86400.0


def due(
    store: Store, goal_id: str | None = None, *, now: datetime | None = None
) -> list[dict[str, Any]]:
    now = now or datetime.now(UTC)
    delay = store.settings.holdout_delay_days
    node_filter = None
    if goal_id:
        node_filter = {n.node_id for n in graph_mod.get_nodes(store, goal_id)}

    out: list[dict[str, Any]] = []
    rows = store.query(
        "SELECT i.item_id, i.node_id, h.assigned_at, h.last_checked_at "
        "FROM items i JOIN holdouts h ON h.item_id = i.item_id "
        "WHERE i.retired = 0 AND i.status = 'MASTERY_ELIGIBLE' AND i.holdout = 1 "
        "ORDER BY i.item_id"
    )
    for row in rows:
        if node_filter is not None and row["node_id"] not in node_filter:
            continue
        state = evidence_mod.derive(store, row["node_id"])
        if state.state == "unknown":
            continue
        reference = row["last_checked_at"] or row["assigned_at"]
        age = _age_days(reference, now) or 0.0
        if age < delay:
            continue
        item = items_mod.get(store, row["item_id"])
        out.append(
            {
                "item_id": row["item_id"],
                "node_id": row["node_id"],
                "node_state": state.state,
                "days_since_last_check": int(age),
                "never_checked": row["last_checked_at"] is None,
                "context": "transfer"
                if (item.version and item.version.surface_form)
                else "delayed",
                "item": item,
            }
        )
    out.sort(key=lambda r: (-r["days_since_last_check"], r["item_id"]))
    return out


def due_picks(store: Store, goal_id: str | None = None) -> list[Pick]:
    picks = []
    for row in due(store, goal_id):
        item: Any = row["item"]
        version = item.version
        picks.append(
            Pick(
                mode="holdout",
                reason=(
                    f"holdout not checked for {row['days_since_last_check']} days; "
                    f"node is {row['node_state']}"
                ),
                node_id=row["node_id"],
                item_id=item.item_id,
                item_version_id=item.current_version_id,
                stem=version.stem if version else None,
                options=version.options if version else [],
                context=row["context"],
            )
        )
    return picks


def mark_checked(store: Store, item_id: str) -> None:
    if store.one("SELECT 1 FROM holdouts WHERE item_id = ?", (item_id,)):
        store.update("holdouts", {"item_id": item_id}, {"last_checked_at": utcnow()})


def is_holdout(store: Store, item_id: str) -> bool:
    row = store.one("SELECT holdout FROM items WHERE item_id = ?", (item_id,))
    return bool(row and row["holdout"])
