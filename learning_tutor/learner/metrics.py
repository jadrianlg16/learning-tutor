"""The three Stage 0 numbers. All computed on holdouts or on validation records.

* **7-day holdout success rate** — of the holdout checks answered in the last 7 days
  (``LT_HOLDOUT_DELAY_DAYS``), how many passed. A pass here means correct with assistance
  below 5, like everywhere else.
* **False-mastery rate** — nodes that were ``known`` *at the moment a holdout was served*
  and then failed it, over all nodes that have had a holdout check. The state is recomputed
  from the events strictly before the holdout answer, so promoting a node after the failure
  cannot flatter the number.
* **Item rejection rate** — failing validations over all validations.

Each number is reported with its denominator; a rate over an empty denominator is ``null``,
never zero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from . import evidence as evidence_mod
from . import graph as graph_mod
from . import items as items_mod
from .models import Metrics
from .store import Store, utcnow


def _holdout_answers(store: Store, node_ids: set[str] | None) -> list[Any]:
    rows = store.query(
        "SELECT e.*, i.item_id FROM events e "
        "JOIN item_versions v ON v.item_version_id = e.item_version_id "
        "JOIN items i ON i.item_id = v.item_id "
        "WHERE i.holdout = 1 AND e.kind IN ('answer','probe_answer') ORDER BY e.ts"
    )
    if node_ids is None:
        return rows
    return [r for r in rows if r["node_id"] in node_ids]


def _passed(row: Any) -> bool:
    return bool(row["correct"]) and not row["idk"] and row["assistance_level"] < 5


def holdout_success(
    store: Store, node_ids: set[str] | None, days: int | None = None
) -> dict[str, Any]:
    days = days or store.settings.holdout_delay_days
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat().replace(
        "+00:00", "Z"
    )
    rows = [r for r in _holdout_answers(store, node_ids) if r["ts"] >= cutoff]
    passes = sum(1 for r in rows if _passed(r))
    return {
        "window_days": days,
        "checks": len(rows),
        "passes": passes,
        "rate_percent": round(100 * passes / len(rows)) if rows else None,
    }


def false_mastery(store: Store, node_ids: set[str] | None) -> dict[str, Any]:
    rows = _holdout_answers(store, node_ids)
    checked_nodes: set[str] = set()
    false_nodes: set[str] = set()
    for row in rows:
        node = row["node_id"]
        if not node:
            continue
        checked_nodes.add(node)
        if _passed(row):
            continue
        state_then = evidence_mod.derive(store, node, before=row["ts"])
        if state_then.state == "known":
            false_nodes.add(node)
    return {
        "nodes_with_holdout_checks": len(checked_nodes),
        "nodes_known_then_failed": len(false_nodes),
        "nodes": sorted(false_nodes),
        "rate_percent": round(100 * len(false_nodes) / len(checked_nodes))
        if checked_nodes
        else None,
    }


def compute(store: Store, goal_id: str | None = None) -> Metrics:
    node_ids = None
    if goal_id:
        node_ids = {n.node_id for n in graph_mod.get_nodes(store, goal_id)}
    return Metrics(
        goal_id=goal_id or "*",
        generated_at=utcnow(),
        holdout_success_7d=holdout_success(store, node_ids),
        false_mastery=false_mastery(store, node_ids),
        item_rejection=items_mod.rejection_rate(store),
    )
