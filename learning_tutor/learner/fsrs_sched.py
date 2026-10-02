"""FSRS scheduling, applied to **items only** (never to concepts).

py-fsrs 6.x. One ``fsrs.Card`` per item, stored as JSON in ``fsrs_state``.

Rating mapping (``rating_for``) — the one place where a graded answer becomes a scheduler
signal::

    idk or incorrect                          -> Again (1)
    assistance >= 5 (partial/worked solution)  -> Again (1)   # a pass at 5 is never a pass
    assistance 2-4 (a hint was needed)         -> Hard (2)
    assistance 1, confidence 1-2               -> Hard (2)
    assistance 1, confidence 3+ or unstated    -> Good (3)
    assistance 0, confidence 4-5               -> Easy (4)
    assistance 0, otherwise                    -> Good (3)

Rationale: hard rule 1 in CONTRACTS.md says a pass at assistance >= 5 never counts toward
mastery, so it must not lengthen an interval either — it is scheduled as a lapse. Hints
below that bar still shorten the interval rather than resetting it. Confidence only ever
moves a correct answer between Good and Easy; a confident wrong answer is still Again (its
signal is spent on the misconception machinery, not on the schedule).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from fsrs import Card, Rating, Scheduler

from .store import Store, parse_ts, utcnow

UNEARNED_ASSISTANCE = 5


def scheduler(store: Store) -> Scheduler:
    return Scheduler(desired_retention=store.settings.desired_retention, enable_fuzzing=False)


def rating_for(
    *,
    correct: bool,
    assistance_level: int,
    confidence: int | None = None,
    idk: bool = False,
) -> Rating:
    if idk or not correct:
        return Rating.Again
    if assistance_level >= UNEARNED_ASSISTANCE:
        return Rating.Again
    if assistance_level >= 2:
        return Rating.Hard
    if assistance_level == 1:
        return Rating.Hard if (confidence is not None and confidence <= 2) else Rating.Good
    if confidence is not None and confidence >= 4:
        return Rating.Easy
    return Rating.Good


def _load(store: Store, item_id: str) -> Card:
    row = store.one("SELECT card FROM fsrs_state WHERE item_id = ?", (item_id,))
    if not row:
        return Card()
    return Card.from_dict(json.loads(row["card"]))


def _save(store: Store, item_id: str, card: Card) -> None:
    payload = json.dumps(card.to_dict())
    now = utcnow()
    if store.one("SELECT 1 FROM fsrs_state WHERE item_id = ?", (item_id,)):
        store.update("fsrs_state", {"item_id": item_id}, {"card": payload, "updated_at": now})
    else:
        store.insert("fsrs_state", {"item_id": item_id, "card": payload, "updated_at": now})


def review(
    store: Store,
    item_id: str,
    *,
    correct: bool,
    assistance_level: int,
    confidence: int | None = None,
    idk: bool = False,
    when: datetime | None = None,
) -> dict[str, Any]:
    """Apply one review to an item's card and persist it."""

    rating = rating_for(
        correct=correct, assistance_level=assistance_level, confidence=confidence, idk=idk
    )
    card = _load(store, item_id)
    reviewed_at = when or datetime.now(UTC)
    card, _log = scheduler(store).review_card(card, rating, reviewed_at)
    _save(store, item_id, card)
    return {
        "item_id": item_id,
        "rating": rating.name,
        "due": card.due.isoformat(),
        "state": card.state.name,
    }


def review_with_rating(
    store: Store, item_id: str, rating: Rating, *, when: datetime | None = None
) -> dict[str, Any]:
    """Apply a rating the learner chose themselves — a flashcard flip (study tools).

    ``rating_for`` turns a *graded* answer into a rating; a self-rated card has no grade,
    only the learner's own Again/Hard/Good/Easy, so it goes to the scheduler as given. It
    schedules the card and is never evidence (CONTRACTS.md, *Study tools*).
    """

    card = _load(store, item_id)
    reviewed_at = when or datetime.now(UTC)
    card, _log = scheduler(store).review_card(card, rating, reviewed_at)
    _save(store, item_id, card)
    return {
        "item_id": item_id,
        "rating": rating.name,
        "due": card.due.isoformat(),
        "state": card.state.name,
    }


def due_at(store: Store, item_id: str) -> datetime | None:
    row = store.one("SELECT card FROM fsrs_state WHERE item_id = ?", (item_id,))
    if not row:
        return None
    card = Card.from_dict(json.loads(row["card"]))
    return card.due


def due_items(
    store: Store,
    *,
    node_ids: list[str] | None = None,
    now: datetime | None = None,
    include_holdouts: bool = False,
    statuses: tuple[str, ...] = ("PRACTICE_EVIDENCE", "MASTERY_ELIGIBLE"),
) -> list[dict[str, Any]]:
    """Items whose FSRS card is due, most overdue first. Holdouts excluded by default."""

    now = now or datetime.now(UTC)
    marks = ", ".join("?" for _ in statuses)
    sql = (
        f"SELECT i.item_id, i.node_id, f.card FROM items i JOIN fsrs_state f "
        f"ON f.item_id = i.item_id WHERE i.retired = 0 AND i.status IN ({marks})"
    )
    params: list[Any] = list(statuses)
    if not include_holdouts:
        sql += " AND i.holdout = 0"
    if node_ids:
        sql += " AND i.node_id IN ({})".format(", ".join("?" for _ in node_ids))
        params.extend(node_ids)
    out = []
    for row in store.query(sql, params):
        card = Card.from_dict(json.loads(row["card"]))
        if card.due <= now:
            out.append(
                {
                    "item_id": row["item_id"],
                    "node_id": row["node_id"],
                    "due": card.due.isoformat(),
                    "overdue_seconds": int((now - card.due).total_seconds()),
                }
            )
    out.sort(key=lambda r: (-r["overdue_seconds"], r["item_id"]))
    return out


def due_today_count(store: Store, node_ids: list[str] | None = None) -> int:
    end_of_day = datetime.now(UTC).replace(hour=23, minute=59, second=59)
    return len(due_items(store, node_ids=node_ids, now=end_of_day))


def schedule_summary(store: Store, item_id: str) -> dict[str, Any] | None:
    row = store.one("SELECT card, updated_at FROM fsrs_state WHERE item_id = ?", (item_id,))
    if not row:
        return None
    card = Card.from_dict(json.loads(row["card"]))
    return {
        "item_id": item_id,
        "due": card.due.isoformat(),
        "state": card.state.name,
        "updated_at": row["updated_at"],
    }


def days_since(ts: str, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    return (now - parse_ts(ts)) / timedelta(days=1)
