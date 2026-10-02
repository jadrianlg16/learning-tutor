"""Exam blueprint: the official item count per concept of a goal.

CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. For EGEL Plus ISOFT that
is 143 Disciplinar items over 4 areas and 14 subáreas (the guide's p. 11). It is data: a goal
without one gets equal shares everywhere it is used, and nothing here knows about any exam.

A row names its concept by ``ref`` — the source's own tag, ``3.2`` — resolved through the
graph's aliases whenever it is read. A merge or split moves aliases to the surviving node,
so the row follows its concept without ever being rewritten.
"""

from __future__ import annotations

from typing import Any

from . import events as events_mod
from . import graph as graph_mod
from .store import LearnerError, Store, utcnow


def rows(store: Store, goal_id: str) -> list[Any]:
    return store.query(
        "SELECT * FROM blueprints WHERE goal_id = ? ORDER BY position ASC, ref ASC", (goal_id,)
    )


def _live_goal_node(store: Store, goal_id: str, ref: str) -> str | None:
    node_id = graph_mod.resolve(store, ref, required=False)
    if not node_id:
        return None
    live = store.one(
        "SELECT 1 FROM nodes n JOIN node_goals ng ON ng.node_id = n.node_id "
        "WHERE n.node_id = ? AND ng.goal_id = ? AND n.retired = 0",
        (node_id, goal_id),
    )
    return node_id if live else None


def index(store: Store, goal_id: str) -> dict[str, dict[str, Any]]:
    """``node_id → {ref, area, area_title, title, exam_items, position}`` for rows that
    resolve to a live concept of the goal. Empty when the goal has no blueprint."""

    out: dict[str, dict[str, Any]] = {}
    for row in rows(store, goal_id):
        node_id = _live_goal_node(store, goal_id, row["ref"])
        if node_id is None or node_id in out:
            continue
        out[node_id] = {
            "ref": row["ref"],
            "area": row["area"],
            "area_title": row["area_title"],
            "title": row["title"],
            "exam_items": int(row["exam_items"]),
            "position": int(row["position"]),
        }
    return out


def get(store: Store, goal_id: str) -> dict[str, Any]:
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    found = rows(store, goal_id)
    if not found:
        raise LearnerError(f"no blueprint for goal {goal_id!r}")
    total = sum(int(r["exam_items"]) for r in found)
    areas: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    for row in found:
        area = by_code.get(row["area"])
        if area is None:
            area = {"code": row["area"], "title": row["area_title"], "exam_items": 0,
                    "share": 0.0, "subareas": []}
            by_code[row["area"]] = area
            areas.append(area)
        node_id = _live_goal_node(store, goal_id, row["ref"])
        title_row = (
            store.one("SELECT title FROM nodes WHERE node_id = ?", (node_id,)) if node_id else None
        )
        items = int(row["exam_items"])
        area["exam_items"] += items
        area["subareas"].append(
            {
                "ref": row["ref"],
                "title": row["title"],
                "node_id": node_id,
                "node_title": title_row["title"] if title_row else None,
                "exam_items": items,
                "share": items / total,
            }
        )
    for area in areas:
        area["share"] = area["exam_items"] / total
    return {
        "goal_id": goal_id,
        "exam": found[0]["exam"],
        "source": found[0]["source"],
        "total_items": total,
        "updated_at": max(r["updated_at"] for r in found),
        "areas": areas,
    }


def _clean(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """The spec flattened to rows, or a LearnerError naming every problem at once."""

    areas = spec.get("areas") if isinstance(spec, dict) else None
    if not isinstance(areas, list) or not areas:
        raise LearnerError("a blueprint needs a non-empty `areas` list")
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for a_i, area in enumerate(areas, 1):
        code = str((area or {}).get("code") or "").strip()
        title = str((area or {}).get("title") or "").strip()
        subs = (area or {}).get("subareas")
        if not code or not title:
            problems.append(f"area #{a_i} needs a code and a title")
        if not isinstance(subs, list) or not subs:
            problems.append(f"area {code or a_i} needs a non-empty `subareas` list")
            continue
        for sub in subs:
            ref = str((sub or {}).get("ref") or "").strip()
            sub_title = str((sub or {}).get("title") or "").strip()
            items = (sub or {}).get("items")
            if not ref or not sub_title:
                problems.append(f"a subarea of area {code} needs a ref and a title")
                continue
            if ref in seen:
                problems.append(f"ref {ref} appears twice")
            seen.add(ref)
            if not isinstance(items, int) or isinstance(items, bool) or items <= 0:
                problems.append(f"{ref}: items must be a positive whole number")
                continue
            out.append({"ref": ref, "area": code, "area_title": title, "title": sub_title,
                        "exam_items": items})
    if problems:
        raise LearnerError("blueprint rejected: " + "; ".join(problems))
    return out


def set_blueprint(
    store: Store, goal_id: str, spec: dict[str, Any], *, author: str = "cli"
) -> dict[str, Any]:
    """Replace the goal's blueprint. Every ref must resolve to a live concept of the goal,
    or nothing is written."""

    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    clean = _clean(spec)
    missing = [r["ref"] for r in clean if _live_goal_node(store, goal_id, r["ref"]) is None]
    if missing:
        raise LearnerError(
            "blueprint rejected: no live concept of this goal answers to "
            + ", ".join(missing)
            + " (name them by an alias, id or exact title)"
        )
    previous = rows(store, goal_id)
    now = utcnow()
    exam = (spec.get("exam") or "").strip() or None
    source = (spec.get("source") or "").strip() or None
    store.execute("DELETE FROM blueprints WHERE goal_id = ?", (goal_id,))
    for position, row in enumerate(clean, 1):
        store.insert(
            "blueprints",
            {"goal_id": goal_id, **row, "position": position, "exam": exam, "source": source,
             "updated_at": now},
        )
    events_mod.append(
        store,
        kind="note",
        goal_id=goal_id,
        payload={
            "blueprint_set": {
                "exam": exam,
                "source": source,
                "author": author,
                "rows": len(clean),
                "total_items": sum(r["exam_items"] for r in clean),
                "previous_rows": len(previous),
                "previous_total_items": sum(int(r["exam_items"]) for r in previous),
            }
        },
    )
    return get(store, goal_id)
