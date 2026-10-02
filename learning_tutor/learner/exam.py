"""Exam prep on top of the study tools: progress against the blueprint, and sealed mocks.

CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. No model anywhere.

**Progress** is counts, not a mastery estimate: per concept and area, how much of the bank
has been seen, first-try results with a 95% Wilson range, and what is due — the evidence
the learner's own plan tracks ("% de aciertos por área"). Node *state* still comes from the
evidence rules alone. Migrated copies of events (a graph merge or split) are not counted:
they are the same answers again.

**A mock** draws checked sealed items (pool ``mock``, never answered) weighted by the
blueprint, shuffles questions and options with a seed stored in its ``session_start``
event, gives no feedback while open, and grades everything on submit through the ordinary
recording path (``answer`` / ``rubric`` / ``bank-key-v1``, ``payload.mock``). An item left
blank is recorded as ``idk``. Its state is a fold over events, like everything else.
"""

from __future__ import annotations

import json
import math
import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fsrs import Card

from . import blueprint as blueprint_mod
from . import events as events_mod
from . import evidence as evidence_mod
from . import fsrs_sched, study, views
from . import items as items_mod
from .ids import prefixed
from .store import LearnerError, StateConflict, Store, parse_ts, utcnow

MOCK_PROMPT_VERSION = "mock/v1"
MOCK_MAX_DEFAULT = 60
ACTIVITY_DAYS = 28
FORECAST_MAX_DAYS = 60
FORECAST_NO_DEADLINE_DAYS = 14
#: a typical readiness rule ("80% per area, none under 70%") reads per-area accuracy;
#: below this many first tries in an area a weighted headline would be mostly noise
HEADLINE_MIN_FIRST_TRIES = 5
_NOT_MIGRATED = "json_extract(e.payload, '$.migrated') IS NULL"


# --------------------------------------------------------------------------- helpers
def _local_date(ts: str) -> str:
    return parse_ts(ts).astimezone().date().isoformat()


def wilson(correct: int, n: int, z: float = 1.96) -> tuple[int | None, int | None]:
    """95% Wilson score interval for ``correct/n``, as whole percents."""

    if n <= 0:
        return None, None
    p = correct / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0, round(100 * (centre - half))), min(100, round(100 * (centre + half)))


def _empty_tally() -> dict[str, Any]:
    return {
        "bank": 0, "checked": 0, "sealed": 0, "seen": 0,
        "first_attempts": 0, "first_correct": 0, "low": None, "high": None,
        "attempts": 0, "correct": 0, "due_now": 0,
    }


def _add(total: dict[str, Any], part: dict[str, Any]) -> None:
    for key in ("bank", "checked", "sealed", "seen", "first_attempts", "first_correct",
                "attempts", "correct", "due_now"):
        total[key] += part[key]


def _finish(tally: dict[str, Any]) -> dict[str, Any]:
    tally["low"], tally["high"] = wilson(tally["first_correct"], tally["first_attempts"])
    return tally


def _answer_history(store: Store, item_ids: list[str]) -> dict[str, list[Any]]:
    """Every real (not migrated) answer or probe answer per item, oldest first."""

    if not item_ids:
        return {}
    marks = ", ".join("?" for _ in item_ids)
    rows = store.query(
        "SELECT v.item_id, e.ts, e.correct, e.idk FROM events e "
        "JOIN item_versions v ON v.item_version_id = e.item_version_id "
        f"WHERE v.item_id IN ({marks}) AND e.kind IN ('answer', 'probe_answer') "
        f"AND {_NOT_MIGRATED} ORDER BY e.ts ASC, e.event_id ASC",
        item_ids,
    )
    out: dict[str, list[Any]] = {}
    for row in rows:
        out.setdefault(row["item_id"], []).append(row)
    return out


def _dues(store: Store, item_ids: list[str]) -> dict[str, datetime]:
    if not item_ids:
        return {}
    marks = ", ".join("?" for _ in item_ids)
    return {
        row["item_id"]: Card.from_dict(json.loads(row["card"])).due
        for row in store.query(
            f"SELECT item_id, card FROM fsrs_state WHERE item_id IN ({marks})", item_ids
        )
    }


# --------------------------------------------------------------------------- progress
def progress(store: Store, goal_id: str) -> dict[str, Any]:
    goal = store.one("SELECT * FROM goals WHERE goal_id = ?", (goal_id,))
    if not goal:
        raise LearnerError(f"unknown goal {goal_id!r}")
    now = datetime.now(UTC)
    today = study.local_today(now)
    deadline = goal["deadline"] or None
    days_left = None
    if deadline:
        days_left = (date.fromisoformat(deadline) - date.fromisoformat(today)).days

    index = blueprint_mod.index(store, goal_id)
    questions = study._items_of_kind(store, goal_id, study.QUESTION_KIND, sealed="include")
    history = _answer_history(store, [r["item_id"] for r in questions])
    dues = _dues(store, [r["item_id"] for r in questions])

    per_node: dict[str, dict[str, Any]] = {}
    for row in questions:
        if study._rejected(store, row):
            continue  # held back from everything until a person judges it
        tally = per_node.setdefault(row["node_id"], _empty_tally())
        answers = history.get(row["item_id"], [])
        if row["pool"] == "mock" and not answers:
            tally["sealed"] += 1
            continue
        tally["bank"] += 1
        tally["checked"] += int(row["status"] in items_mod.EVIDENCE_STATUSES)
        due = dues.get(row["item_id"])
        tally["due_now"] += int(due is not None and due <= now)
        if not answers:
            continue
        first = answers[0]
        tally["seen"] += 1
        tally["first_attempts"] += 1
        tally["first_correct"] += int(bool(first["correct"]) and not first["idk"])
        tally["attempts"] += len(answers)
        tally["correct"] += sum(int(bool(a["correct"]) and not a["idk"]) for a in answers)

    goal_nodes = study._goal_nodes(store, goal_id)
    titles = study._node_titles(store, goal_nodes)
    states = {node: evidence_mod.derive(store, node).state for node in goal_nodes}

    def node_progress(node: str) -> dict[str, Any]:
        meta = index.get(node)
        tally = _finish(dict(per_node.get(node) or _empty_tally()))
        return {
            "ref": meta["ref"] if meta else None,
            "node_id": node,
            "title": meta["title"] if meta else titles.get(node, node),
            "exam_items": meta["exam_items"] if meta else None,
            "share": None,
            "state": states.get(node, "unknown"),
            **tally,
        }

    total_items = sum(meta["exam_items"] for meta in index.values())
    areas: list[dict[str, Any]] = []
    by_code: dict[str, dict[str, Any]] = {}
    for node, meta in sorted(index.items(), key=lambda kv: kv[1]["position"]):
        area = by_code.get(meta["area"])
        if area is None:
            area = {"code": meta["area"], "title": meta["area_title"], "exam_items": 0,
                    "share": 0.0, **_empty_tally(), "subareas": []}
            by_code[meta["area"]] = area
            areas.append(area)
        sub = node_progress(node)
        sub["share"] = meta["exam_items"] / total_items
        area["exam_items"] += meta["exam_items"]
        _add(area, sub)
        area["subareas"].append(sub)
    for area in areas:
        area["share"] = area["exam_items"] / total_items
        _finish(area)

    unassigned = [
        node_progress(node)
        for node in goal_nodes
        if node not in index and node in per_node
    ]

    totals = _empty_tally()
    for tally in per_node.values():
        _add(totals, tally)
    _finish(totals)

    return {
        "goal_id": goal_id,
        "today": today,
        "deadline": deadline,
        "days_left": days_left,
        "has_blueprint": bool(index),
        "totals": totals,
        "disciplinar": _headline(areas),
        "areas": areas,
        "unassigned": unassigned,
        "activity": _activity(store, goal_id, today),
        "forecast": _forecast(store, goal_id, now, today, deadline, days_left),
        "mocks": [m for m in mock_list(store, goal_id)["mocks"]],
    }


def _headline(areas: list[dict[str, Any]]) -> dict[str, Any]:
    if not areas:
        return {"weighted_accuracy": None, "coverage": 0.0,
                "note": "no blueprint: set one to weigh areas like the exam does"}
    coverage = sum(
        sub["share"] for area in areas for sub in area["subareas"] if sub["first_attempts"]
    )
    thin = [a for a in areas if a["first_attempts"] < HEADLINE_MIN_FIRST_TRIES]
    if thin:
        return {
            "weighted_accuracy": None,
            "coverage": round(coverage, 4),
            "note": f"needs {HEADLINE_MIN_FIRST_TRIES} first tries in every area; "
            + ", ".join(f"área {a['code']} has {a['first_attempts']}" for a in thin),
        }
    weighted = sum(a["share"] * a["first_correct"] / a["first_attempts"] for a in areas)
    return {
        "weighted_accuracy": round(100 * weighted),
        "coverage": round(coverage, 4),
        "note": "first tries only, weighted like the exam; not an ICNE score",
    }


def _activity(store: Store, goal_id: str, today: str) -> list[dict[str, Any]]:
    first_day = date.fromisoformat(today) - timedelta(days=ACTIVITY_DAYS - 1)
    days = {
        (first_day + timedelta(days=i)).isoformat(): {"answers": 0, "correct": 0, "cards": 0}
        for i in range(ACTIVITY_DAYS)
    }
    since = datetime.combine(first_day, datetime.min.time()).astimezone() - timedelta(days=1)
    rows = store.query(
        "SELECT e.kind, e.ts, e.correct, e.idk FROM events e "
        "WHERE e.goal_id = ? AND e.kind IN ('answer', 'probe_answer', 'card_review') "
        f"AND {_NOT_MIGRATED} AND e.ts >= ?",
        (goal_id, since.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")),
    )
    for row in rows:
        day = days.get(_local_date(row["ts"]))
        if day is None:
            continue
        if row["kind"] == "card_review":
            day["cards"] += 1
        else:
            day["answers"] += 1
            day["correct"] += int(bool(row["correct"]) and not row["idk"])
    return [{"date": d, **v} for d, v in days.items()]


def _forecast(
    store: Store,
    goal_id: str,
    now: datetime,
    today: str,
    deadline: str | None,
    days_left: int | None,
) -> list[dict[str, Any]]:
    horizon = (
        min(days_left, FORECAST_MAX_DAYS - 1)
        if days_left is not None and days_left >= 0
        else FORECAST_NO_DEADLINE_DAYS - 1
    )
    start = date.fromisoformat(today)
    days = {(start + timedelta(days=i)).isoformat(): 0 for i in range(horizon + 1)}
    visible = study._items_of_kind(store, goal_id, study.QUESTION_KIND) + study._items_of_kind(
        store, goal_id, study.CARD_KIND
    )
    for due in _dues(store, [r["item_id"] for r in visible]).values():
        key = max(due, now).astimezone().date().isoformat()
        if key in days:
            days[key] += 1
    return [{"date": d, "due": n} for d, n in days.items()]


# --------------------------------------------------------------------------- mocks
def _mock_sessions(store: Store, goal_id: str | None = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT s.session_id, s.goal_id, s.channel, s.started_at, s.ended_at, e.payload "
        "FROM events e JOIN sessions s ON s.session_id = e.session_id "
        "WHERE e.kind = 'session_start' AND json_extract(e.payload, '$.mock') IS NOT NULL"
    )
    params: list[Any] = []
    if goal_id:
        sql += " AND s.goal_id = ?"
        params.append(goal_id)
    out = []
    for row in store.query(sql + " ORDER BY s.started_at DESC, s.session_id DESC", params):
        out.append({**dict(row), "plan": json.loads(row["payload"])["mock"]})
    return out


def _mock(store: Store, session_id: str) -> dict[str, Any]:
    session = store.one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    if not session:
        raise LearnerError(f"unknown session {session_id!r}")
    found = [m for m in _mock_sessions(store) if m["session_id"] == session_id]
    if not found:
        raise LearnerError(f"session {session_id} is not a mock exam")
    return found[0]


def _reserved(store: Store, goal_id: str) -> set[str]:
    """Items drawn into a mock that is still open: not available to another one."""

    return {
        item
        for m in _mock_sessions(store, goal_id)
        if not m["ended_at"]
        for item in m["plan"]["items"]
    }


def _sealed(store: Store, goal_id: str) -> tuple[list[Any], int]:
    """(checked sealed items a new mock may use, sealed items still waiting for a check)."""

    reserved = _reserved(store, goal_id)
    ready, unchecked = [], 0
    for row in study._items_of_kind(store, goal_id, study.QUESTION_KIND, sealed="only"):
        if row["item_id"] in reserved or study._rejected(store, row):
            continue
        if row["status"] in items_mod.EVIDENCE_STATUSES:
            ready.append(row)
        else:
            unchecked += 1
    return ready, unchecked


def _ends_at(started_at: str, minutes: int) -> str:
    end = parse_ts(started_at) + timedelta(minutes=minutes)
    return end.isoformat(timespec="seconds").replace("+00:00", "Z")


def _answers(store: Store, session_id: str) -> dict[str, Any]:
    rows = store.query(
        "SELECT v.item_id, e.response, e.correct, e.idk, e.ts FROM events e "
        "JOIN item_versions v ON v.item_version_id = e.item_version_id "
        "WHERE e.session_id = ? AND e.kind = 'answer' "
        f"AND json_extract(e.payload, '$.mock') = ? AND {_NOT_MIGRATED}",
        (session_id, session_id),
    )
    return {row["item_id"]: row for row in rows}


def _summary(store: Store, mock: dict[str, Any]) -> dict[str, Any]:
    plan = mock["plan"]
    out: dict[str, Any] = {
        "session_id": mock["session_id"],
        "started_at": mock["started_at"],
        "submitted_at": mock["ended_at"],
        "n": plan["n"],
        "answered": None,
        "correct": None,
        "minutes": plan["minutes"],
        "minutes_used": None,
        "areas": None,
    }
    if mock["ended_at"]:
        answers = _answers(store, mock["session_id"])
        index = blueprint_mod.index(store, mock["goal_id"])
        out["answered"] = sum(1 for a in answers.values() if not a["idk"])
        out["correct"] = sum(1 for a in answers.values() if a["correct"] and not a["idk"])
        out["minutes_used"] = _minutes_between(mock["started_at"], mock["ended_at"])
        areas: dict[str, dict[str, Any]] = {}
        for item_id in plan["items"]:
            node = _item_node(store, item_id)
            code = index[node]["area"] if node in index else None
            area = areas.setdefault(code or "", {"code": code, "n": 0, "correct": 0})
            area["n"] += 1
            answer = answers.get(item_id)
            area["correct"] += int(bool(answer and answer["correct"] and not answer["idk"]))
        out["areas"] = sorted(areas.values(), key=lambda a: (a["code"] is None, a["code"] or ""))
    return out


def _item_node(store: Store, item_id: str) -> str:
    row = store.one("SELECT node_id FROM items WHERE item_id = ?", (item_id,))
    return row["node_id"] if row else ""


def _minutes_between(start: str, end: str) -> int:
    return max(1, math.ceil((parse_ts(end) - parse_ts(start)).total_seconds() / 60))


def mock_list(store: Store, goal_id: str) -> dict[str, Any]:
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    ready, unchecked = _sealed(store, goal_id)
    index = blueprint_mod.index(store, goal_id)
    by_area: dict[str, dict[str, Any]] = {}
    for meta in sorted(index.values(), key=lambda m: m["position"]):
        by_area.setdefault(
            meta["area"], {"code": meta["area"], "title": meta["area_title"], "available": 0}
        )
    for row in ready:
        meta = index.get(row["node_id"])
        key = meta["area"] if meta else ""
        entry = by_area.setdefault(
            key, {"code": meta["area"] if meta else None,
                  "title": meta["area_title"] if meta else "Not in the blueprint",
                  "available": 0}
        )
        entry["available"] += 1
    sessions = _mock_sessions(store, goal_id)
    open_ = next((m for m in sessions if not m["ended_at"]), None)
    return {
        "goal_id": goal_id,
        "sealed_available": len(ready),
        "sealed_unchecked": unchecked,
        "sealed_by_area": list(by_area.values()),
        "open": _summary(store, open_) if open_ else None,
        "mocks": [_summary(store, m) for m in sessions if m["ended_at"]],
    }


def mock_start(
    store: Store,
    goal_id: str,
    *,
    n: int | None = None,
    minutes: int | None = None,
    channel: str = "web",
) -> dict[str, Any]:
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    if channel not in events_mod.VALID_CHANNELS:
        raise LearnerError(f"unknown channel {channel!r}")
    open_ = [m for m in _mock_sessions(store, goal_id) if not m["ended_at"]]
    if open_:
        raise StateConflict(
            f"mock {open_[0]['session_id']} is still open: submit it before starting another"
        )
    ready, unchecked = _sealed(store, goal_id)
    if not ready:
        hint = f" ({unchecked} still need a blind check)" if unchecked else ""
        raise LearnerError(
            "no checked sealed questions left for a mock" + hint
            + ": import some with pool=mock and blind-check them"
        )
    size = min(len(ready), MOCK_MAX_DEFAULT) if n is None else int(n)
    if size < 1:
        raise LearnerError("a mock needs at least one question")
    size = min(size, len(ready))
    index = blueprint_mod.index(store, goal_id)
    weights = study.concept_weights({r["node_id"] for r in ready}, index)
    picks = study.interleave(ready, {}, weights, size)

    session_id = prefixed("s")
    rng = random.Random(session_id)
    rng.shuffle(picks)
    orders: dict[str, list[int]] = {}
    for row in picks:
        order = list(range(len(json.loads(row["options"]))))
        rng.shuffle(order)
        orders[row["item_id"]] = order
    limit = int(minutes) if minutes else max(1, round(size * store.settings.mock_minutes_per_item))
    if limit < 1:
        raise LearnerError("a mock needs at least one minute")
    started = utcnow()
    store.insert(
        "sessions",
        {"session_id": session_id, "goal_id": goal_id, "channel": channel,
         "started_at": started, "ended_at": None, "summary": None},
    )
    events_mod.append(
        store,
        kind="session_start",
        session_id=session_id,
        goal_id=goal_id,
        channel=channel,
        payload={"mock": {"items": [r["item_id"] for r in picks], "orders": orders,
                          "minutes": limit, "n": len(picks)}},
    )
    views.refresh(store, goal_id)
    return mock_show(store, session_id)


def mock_show(store: Store, session_id: str) -> dict[str, Any]:
    mock = _mock(store, session_id)
    if mock["ended_at"]:
        return _result(store, mock)
    plan = mock["plan"]
    index = blueprint_mod.index(store, mock["goal_id"])
    questions = []
    for item_id in plan["items"]:
        item = items_mod.get(store, item_id)
        order = plan["orders"][item_id]
        meta = index.get(item.node_id)
        questions.append(
            {
                "item_id": item_id,
                "ref": meta["ref"] if meta else None,
                "area": {"code": meta["area"], "title": meta["area_title"]} if meta else None,
                "node_title": study._node_titles(store, [item.node_id]).get(item.node_id),
                "stem": item.version.stem,
                "options": study.shown_options(item.version.options, order),
                "order": order,
            }
        )
    return {
        "session_id": session_id,
        "goal_id": mock["goal_id"],
        "status": "open",
        "started_at": mock["started_at"],
        "minutes": plan["minutes"],
        "ends_at": _ends_at(mock["started_at"], plan["minutes"]),
        "n": plan["n"],
        "questions": questions,
    }


def mock_submit(
    store: Store, session_id: str, answers: list[dict[str, Any]] | None
) -> dict[str, Any]:
    """Grade and record every question of the mock at once, then close it.

    Everything is validated before anything is written, so a bad answer leaves the mock
    open and untouched. The option order is the one the mock drew, whatever a client sends.
    """

    from . import api  # the recording path every other answer takes

    mock = _mock(store, session_id)
    if mock["ended_at"]:
        raise StateConflict(f"mock {session_id} was already submitted at {mock['ended_at']}")
    plan = mock["plan"]
    given: dict[str, dict[str, Any]] = {}
    for answer in answers or []:
        item_id = str((answer or {}).get("item_id") or "")
        if item_id not in plan["orders"]:
            raise LearnerError(f"item {item_id!r} is not a question of mock {session_id}")
        given[item_id] = answer

    graded = []
    for item_id in plan["items"]:
        item = items_mod.get(store, item_id)
        order = plan["orders"][item_id]
        answer = given.get(item_id) or {}
        response = answer.get("response")
        blank = response is None or str(response).strip() == ""
        chosen = None if blank else study.shown_choice(item.version.options, order, response)
        if not blank and chosen is None:
            raise LearnerError(f"response {response!r} to {item_id} is not one of its options")
        keyed = study.shown_choice(item.version.options, order, item.version.answer)
        confidence = answer.get("confidence")
        # checked here, not only by record_answer: each write commits, so a bad value found
        # mid-loop would leave half a mock recorded and the mock still open
        if confidence is not None and (
            isinstance(confidence, bool) or not isinstance(confidence, int)
            or not 1 <= confidence <= 5
        ):
            raise LearnerError(f"confidence for {item_id} must be 1-5, got {confidence!r}")
        graded.append((item_id, order, chosen, keyed, confidence))

    for item_id, order, chosen, keyed, confidence in graded:
        correct = bool(chosen and keyed and chosen["text"] == keyed["text"])
        receipt = api.record_answer(
            store,
            session_id=session_id,
            item_id=item_id,
            response=chosen["text"] if chosen else None,
            correct=correct,
            confidence=confidence,
            idk=chosen is None,
            assistance_level=0,
            context="in-session",
            channel=mock["channel"],
            prompt_version=MOCK_PROMPT_VERSION,
            grader_version=study.BANK_GRADER_VERSION,
            evaluation_method="rubric",
            extra={"mock": session_id, "shown_order": order},
        )
        if not receipt["wrote_evidence"]:
            # unchecked items never reach a mock, but keep the practice rule if one did
            fsrs_sched.review(store, item_id, correct=correct, assistance_level=0,
                              confidence=confidence, idk=chosen is None)
    right = sum(1 for _, _, chosen, keyed, _ in graded
                if chosen and keyed and chosen["text"] == keyed["text"])
    answered = sum(1 for _, _, chosen, _, _ in graded if chosen)
    api.session_end(
        store,
        session_id,
        summary=json.dumps({"mock": {"n": len(graded), "answered": answered, "correct": right}}),
    )
    return mock_show(store, session_id)


def _result(store: Store, mock: dict[str, Any]) -> dict[str, Any]:
    plan = mock["plan"]
    answers = _answers(store, mock["session_id"])
    index = blueprint_mod.index(store, mock["goal_id"])
    items = []
    areas: dict[str, dict[str, Any]] = {}
    for item_id in plan["items"]:
        item = items_mod.get(store, item_id)
        order = plan["orders"][item_id]
        options = item.version.options
        answer = answers.get(item_id)
        yours = study.shown_choice(options, order, answer["response"]) if answer else None
        keyed = study.shown_choice(options, order, item.version.answer)
        correct = bool(yours and keyed and yours["text"] == keyed["text"])
        meta = index.get(item.node_id)
        code = meta["area"] if meta else None
        area = areas.setdefault(
            code or "",
            {"code": code, "title": meta["area_title"] if meta else "Not in the blueprint",
             "n": 0, "correct": 0, "subareas": {}},
        )
        area["n"] += 1
        area["correct"] += int(correct)
        ref = meta["ref"] if meta else None
        sub = area["subareas"].setdefault(
            ref or item.node_id,
            {"ref": ref, "title": meta["title"] if meta else
             study._node_titles(store, [item.node_id]).get(item.node_id), "n": 0, "correct": 0},
        )
        sub["n"] += 1
        sub["correct"] += int(correct)
        items.append(
            {
                "item_id": item_id,
                "ref": ref,
                "area_code": code,
                "node_title": study._node_titles(store, [item.node_id]).get(item.node_id),
                "stem": item.version.stem,
                "options": study.shown_options(options, order),
                "your_answer": yours,
                "correct_answer": keyed,
                "correct": correct,
                "explanation": item.version.explanation,
            }
        )
    ordered_areas = sorted(areas.values(), key=lambda a: (a["code"] is None, a["code"] or ""))
    for area in ordered_areas:
        area["subareas"] = sorted(area["subareas"].values(), key=lambda s: s["ref"] or "")
    ends_at = _ends_at(mock["started_at"], plan["minutes"])
    submitted = mock["ended_at"]
    return {
        "session_id": mock["session_id"],
        "goal_id": mock["goal_id"],
        "status": "submitted",
        "started_at": mock["started_at"],
        "submitted_at": submitted,
        "minutes": plan["minutes"],
        "minutes_used": _minutes_between(mock["started_at"], submitted),
        # a minute of grace for the round trip of an auto-submit at the bell
        "overtime": parse_ts(submitted) > parse_ts(ends_at) + timedelta(minutes=1),
        "n": plan["n"],
        "answered": sum(1 for i in items if i["your_answer"]),
        "correct": sum(1 for i in items if i["correct"]),
        "areas": ordered_areas,
        "items": items,
    }
