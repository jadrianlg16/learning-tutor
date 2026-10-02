"""Study tools: a question bank, flashcards and comparison tables — no model anywhere.

CONTRACTS.md, *Study tools*. Three things share one item bank and one scheduler:

* **Questions** (item kind ``mc``) imported from markdown. They start ``TEACHING_ONLY`` like
  every item and write evidence only after a blind check by a solver that is not their
  author (:func:`blind_check`). Practice grades them against the stored key, server-side,
  and records an ordinary ``answer`` event judged ``rubric`` (a frozen key).
* **Flashcards** (item kind ``card``: ``stem`` = front, ``answer`` = back). A flip is the
  learner rating their own recall, so it is recorded as ``card_review`` /
  ``self_report``: it schedules the card through FSRS and is never evidence.
* **Tables** (``study_tables``). Reference material. Fill-in practice on them happens in
  the client and is not recorded; turning a table into flashcards is.

Practice schedules every question it serves, checked or not: a schedule is not evidence,
and without one an unchecked question would never come back. What a question *counts
for* is still decided by its status, exactly as for every other item.

Since migration 4 (CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*)
new questions are interleaved by the goal's blueprint, options are shuffled per serve,
and items in pool ``mock`` stay sealed — out of practice, cards and exports — until a
mock exam (``exam.py``) has used them.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fsrs import Card, Rating

from . import blueprint as blueprint_mod
from . import events as events_mod
from . import fsrs_sched, study_md
from . import graph as graph_mod
from . import items as items_mod
from .ids import prefixed
from .store import LearnerError, Store, parse_ts, utcnow

QUESTION_KIND = "mc"
CARD_KIND = "card"
RATINGS: dict[str, Rating] = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}
PRACTICE_PROMPT_VERSION = "practice/v1"
#: an imported question is graded by matching its frozen key: the ``rubric`` method
BANK_GRADER_VERSION = "bank-key-v1"
CARD_GRADER_VERSION = "self-report-v1"
WHAT = ("questions", "cards", "tables")
_LETTERS = "ABCDEFGHIJ"


# --------------------------------------------------------------------------- helpers
def source_key(goal_id: str, kind: str, *parts: str) -> str:
    """Content hash an importer dedupes on: same goal, same kind, same text → same key."""

    norm = "\x1f".join(re.sub(r"\s+", " ", p or "").strip().lower() for p in parts)
    return hashlib.sha256(f"{goal_id}\x1e{kind}\x1e{norm}".encode()).hexdigest()


def _goal_nodes(store: Store, goal_id: str) -> list[str]:
    if not store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
        raise LearnerError(f"unknown goal {goal_id!r}")
    return [
        r["node_id"]
        for r in store.query(
            "SELECT ng.node_id FROM node_goals ng JOIN nodes n ON n.node_id = ng.node_id "
            "WHERE ng.goal_id = ? AND n.retired = 0",
            (goal_id,),
        )
    ]


def _node_titles(store: Store, node_ids: list[str]) -> dict[str, str]:
    if not node_ids:
        return {}
    marks = ", ".join("?" for _ in node_ids)
    return {
        r["node_id"]: r["title"]
        for r in store.query(
            f"SELECT node_id, title FROM nodes WHERE node_id IN ({marks})", node_ids
        )
    }


#: an item that has been answered at least once (practice, probe or a mock)
_ANSWERED = (
    "EXISTS (SELECT 1 FROM events ae JOIN item_versions av "
    "ON av.item_version_id = ae.item_version_id "
    "WHERE av.item_id = i.item_id AND ae.kind IN ('answer', 'probe_answer'))"
)
_SEALED_SQL = {
    # what practice, cards and exports may show: the practice pool, plus mock items a mock
    # has already used
    "exclude": f" AND (i.pool = 'practice' OR {_ANSWERED})",
    # everything: blind checks and reviews must reach sealed items too
    "include": "",
    # sealed mock items only: never answered anywhere
    "only": f" AND i.pool = 'mock' AND NOT {_ANSWERED}",
}


def _items_of_kind(
    store: Store, goal_id: str, kind: str, *, sealed: str = "exclude"
) -> list[Any]:
    """Current versions of every live, non-holdout item of ``kind`` on the goal's nodes.

    ``sealed`` decides the mock pool: ``exclude`` (the default: what a learner may see
    outside a mock), ``include`` or ``only`` — see ``_SEALED_SQL``.
    """

    node_ids = _goal_nodes(store, goal_id)
    if not node_ids:
        return []
    marks = ", ".join("?" for _ in node_ids)
    return store.query(
        "SELECT i.item_id, i.node_id, i.status, i.author, i.created_at, i.holdout, i.pool, "
        "v.item_version_id, v.stem, v.options, v.answer, v.explanation, v.source, v.kind "
        "FROM items i JOIN item_versions v ON v.item_version_id = i.current_version_id "
        f"WHERE i.node_id IN ({marks}) AND i.retired = 0 AND i.holdout = 0 AND v.kind = ?"
        f"{_SEALED_SQL[sealed]} "
        "ORDER BY i.created_at ASC, i.item_id ASC",
        [*node_ids, kind],
    )


def _keyed_options(options: list[str]) -> list[dict[str, str]]:
    return [{"key": _LETTERS[i], "text": text} for i, text in enumerate(options)]


def _option_for(options: list[str], response: str | None) -> dict[str, str] | None:
    """A response given as a key ("B") or as the option's own text."""

    if response is None:
        return None
    wanted = str(response).strip()
    for i, text in enumerate(options):
        if wanted.upper() == _LETTERS[i] or wanted == text:
            return {"key": _LETTERS[i], "text": text}
    return None


def local_today(now: datetime | None = None) -> str:
    """The learner's local date (the process zone; the compose file passes ``TZ``)."""

    return (now or datetime.now(UTC)).astimezone().date().isoformat()


def shown_order(stem: str, options: list[str], salt: str | None = None) -> list[int]:
    """The order a question's options are shown in: ``order[k]`` is the stored index shown
    as the k-th letter. Seeded by the question's text and the local day (or ``salt``), so a
    question keeps one order within a day and the server can recompute it."""

    material = "\x1f".join([stem, *options, salt or local_today()])
    digest = hashlib.sha256(material.encode()).digest()
    order = list(range(len(options)))
    random.Random(int.from_bytes(digest[:8], "big")).shuffle(order)
    return order


def valid_order(order: Any, count: int) -> list[int] | None:
    """``order`` as sent back by a client: None, or a permutation of the option indexes."""

    if order is None or order == "" or order == []:
        return None
    if isinstance(order, str):
        order = [p for p in re.split(r"[\s,]+", order.strip()) if p]
    try:
        values = [int(v) for v in order]
    except (TypeError, ValueError) as exc:
        raise LearnerError(f"order must be a list of option indexes, got {order!r}") from exc
    if sorted(values) != list(range(count)):
        raise LearnerError(f"order must be a permutation of 0..{count - 1}, got {values}")
    return values


def shown_options(options: list[str], order: list[int]) -> list[dict[str, str]]:
    return [{"key": _LETTERS[pos], "text": options[idx]} for pos, idx in enumerate(order)]


def shown_choice(
    options: list[str], order: list[int], response: str | None
) -> dict[str, str] | None:
    """A response given as a letter of the shown order ("B") or as the option's own text."""

    if response is None:
        return None
    wanted = str(response).strip()
    for option in shown_options(options, order):
        if wanted.upper() == option["key"] or wanted == option["text"]:
            return option
    return None


def interleave(
    fresh: list[Any], seen: dict[str, int], weights: dict[str, float], k: int
) -> list[Any]:
    """Up to ``k`` of ``fresh`` (bank order within a concept), each from the concept furthest
    below its weighted share of what has been seen so far — a largest-deficit round robin,
    so areas interleave from the first pick and heavy concepts come up more often."""

    queues: dict[str, list[Any]] = {}
    for row in fresh:
        queues.setdefault(row["node_id"], []).append(row)
    first_seen = {node: i for i, node in enumerate(queues)}
    counts = dict(seen)
    total_weight = sum(weights.get(node, 0.0) for node in set(weights) | set(queues)) or 1.0
    total_seen = sum(counts.values())
    picks: list[Any] = []
    while len(picks) < k:
        live = [node for node, q in queues.items() if q]
        if not live:
            break
        scores = {}
        for candidate in live:
            share = weights.get(candidate, 0.0) / total_weight
            deficit = share * (total_seen + 1) - counts.get(candidate, 0)
            scores[candidate] = (deficit, share, -first_seen[candidate])
        node = max(live, key=scores.__getitem__)
        picks.append(queues[node].pop(0))
        counts[node] = counts.get(node, 0) + 1
        total_seen += 1
    return picks


def concept_weights(
    node_ids: list[str] | set[str], index: dict[str, dict[str, Any]]
) -> dict[str, float]:
    """Blueprint item counts; a concept the blueprint does not name gets the smallest one
    (and every concept gets 1 when the goal has no blueprint)."""

    floor = min((meta["exam_items"] for meta in index.values()), default=1)
    return {
        node: float(index[node]["exam_items"] if node in index else floor) for node in node_ids
    }


def _latest_validation(store: Store, item_id: str, version_id: str) -> Any:
    return store.one(
        "SELECT * FROM item_validations WHERE item_id = ? AND item_version_id = ? "
        "ORDER BY ts DESC, validation_id DESC LIMIT 1",
        (item_id, version_id),
    )


def _rejected(store: Store, row: Any) -> bool:
    """A question whose latest blind check on this version failed: a key may be wrong."""

    if row["status"] != "TEACHING_ONLY":
        return False
    last = _latest_validation(store, row["item_id"], row["item_version_id"])
    return bool(last and last["result"] == "fail")


def _local_day_start_utc(now: datetime | None = None) -> str:
    """Start of the learner's *local* today, as a UTC timestamp string.

    "New today" is a learner's day, not UTC's: the process's local zone decides (the
    compose file passes ``TZ``), so nothing machine-specific is written here.
    """

    local = (now or datetime.now(UTC)).astimezone()
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _first_seen_today(store: Store, item_ids: list[str], kinds: tuple[str, ...]) -> int:
    """How many of ``item_ids`` had their first-ever event of ``kinds`` today."""

    if not item_ids:
        return 0
    since = _local_day_start_utc()
    marks = ", ".join("?" for _ in item_ids)
    kind_marks = ", ".join("?" for _ in kinds)
    row = store.one(
        "SELECT COUNT(*) AS n FROM ("
        "  SELECT v.item_id, MIN(e.ts) AS first_ts FROM events e "
        "  JOIN item_versions v ON v.item_version_id = e.item_version_id "
        f"  WHERE v.item_id IN ({marks}) AND e.kind IN ({kind_marks}) GROUP BY v.item_id"
        ") WHERE first_ts >= ?",
        [*item_ids, *kinds, since],
    )
    return int(row["n"] or 0)


def _seen(store: Store, item_id: str, kinds: tuple[str, ...]) -> list[Any]:
    kind_marks = ", ".join("?" for _ in kinds)
    return store.query(
        "SELECT e.ts FROM events e JOIN item_versions v ON v.item_version_id = e.item_version_id "
        f"WHERE v.item_id = ? AND e.kind IN ({kind_marks}) ORDER BY e.ts DESC",
        [item_id, *kinds],
    )


def _context_for(store: Store, item_id: str, kinds: tuple[str, ...]) -> str:
    """``delayed`` when the item was last seen at least ``LT_DELAYED_MIN_HOURS`` ago.

    A retrieval after a real gap is what the ``known`` rule's "delayed pass" means; the
    threshold is a judgement (20 h by default: "yesterday or earlier"), not a measurement.
    """

    rows = _seen(store, item_id, kinds)
    if not rows:
        return "in-session"
    gap = datetime.now(UTC) - parse_ts(rows[0]["ts"])
    return "delayed" if gap >= timedelta(hours=store.settings.delayed_min_hours) else "in-session"


def _card_due(store: Store, item_id: str) -> datetime | None:
    row = store.one("SELECT card FROM fsrs_state WHERE item_id = ?", (item_id,))
    if not row:
        return None
    return Card.from_dict(json.loads(row["card"])).due


# --------------------------------------------------------------------------- nodes
def _resolve_nodes(
    store: Store,
    goal_id: str,
    wanted: list[tuple[str | None, str | None]],
    titles: dict[str, str],
    *,
    default_node: str | None,
    create: bool,
    dry_run: bool,
) -> tuple[dict[tuple[str | None, str | None], str | None], list[dict[str, str]], list[str]]:
    """Map ``(tag, section)`` pairs to node ids.

    A tag names a concept in the source's own numbering (``1.2``); its title comes from the
    source's heading (``## 1.2 …``). A missing tagged concept is created — provenance
    ``course``, chained by ``course_sequence`` inside its parent tag, the order the source
    teaches it in — when ``create`` is set. An untagged block goes to ``default_node``, or
    to an existing node whose title is its section heading; otherwise it is left out and
    reported: a heading like "Glossary" is not a concept and must not become one.
    """

    mapping: dict[tuple[str | None, str | None], str | None] = {}
    created: list[dict[str, str]] = []
    problems: list[str] = []
    default_id = graph_mod.resolve(store, default_node) if default_node else None

    tags = sorted(
        {tag for tag, _ in wanted if tag},
        key=lambda t: [int(p) for p in t.split(".")],
    )
    ops: list[dict[str, Any]] = []
    planned: dict[str, str] = {}
    last_in_parent: dict[str, str] = {}
    for tag in tags:
        title = titles.get(tag) or tag
        existing = graph_mod.resolve(store, title, required=False)
        parent = tag.rsplit(".", 1)[0]
        if existing:
            planned[tag] = existing
        elif create:
            op: dict[str, Any] = {"op": "add", "node": {"title": title, "aliases": [tag]}}
            previous = last_in_parent.get(parent)
            if previous:
                op["edges"] = [
                    {"from": previous, "to": title, "type": "course_sequence",
                     "provenance": "course"}
                ]
            ops.append(op)
            planned[tag] = title
            created.append({"tag": tag, "title": title, "node_id": None})
        last_in_parent[parent] = title
    if ops and not dry_run:
        result = graph_mod.revise(store, goal_id, ops)
        for op_result, entry in zip(result["ops"], created, strict=True):
            entry["node_id"] = op_result["node"]
            planned[entry["tag"]] = op_result["node"]

    unplaced = 0
    for tag, section in wanted:
        node = None
        if tag and tag in planned:
            node = planned[tag]
        elif default_id:
            node = default_id
        elif section:
            node = graph_mod.resolve(store, section, required=False)
        if node is None:
            unplaced += 1
        mapping[(tag, section)] = node
    if unplaced:
        problems.append(
            f"{unplaced} block(s) have no concept to file under: tag the headings "
            "(`## 1.2 Title`) or pass a node"
        )
    return mapping, created, problems


# --------------------------------------------------------------------------- import
def import_markdown(
    store: Store,
    goal_id: str,
    markdown: str,
    *,
    key_markdown: str | None = None,
    what: list[str] | tuple[str, ...] | None = None,
    source: str | None = None,
    author: str = "import",
    node: str | None = None,
    create_nodes: bool = True,
    dry_run: bool = False,
    pool: str = "practice",
) -> dict[str, Any]:
    """Questions, flashcards and tables out of one markdown file (plus an optional key).

    ``pool`` = ``mock`` seals the questions for mock exams; cards and tables from such a
    file would show them early, so a sealed import is questions only.
    """

    _goal_nodes(store, goal_id)
    if pool not in items_mod.POOLS:
        raise LearnerError(f"pool must be one of {', '.join(items_mod.POOLS)}")
    if pool == "mock":
        if what and any(w != "questions" for w in what):
            raise LearnerError(
                "a sealed (mock) import is questions only: cards or tables made from it "
                "would show the questions before the mock"
            )
        what = ["questions"]
    wanted_kinds = [w for w in (what or WHAT)]
    unknown = [w for w in wanted_kinds if w not in WHAT]
    if unknown:
        raise LearnerError(f"unknown import kind(s) {unknown}: one of {', '.join(WHAT)}")
    if not (markdown or "").strip():
        raise LearnerError("nothing to import: the markdown is empty")

    titles = study_md.parse_node_titles(markdown)
    questions, q_problems = (
        study_md.parse_questions(markdown) if "questions" in wanted_kinds else ([], [])
    )
    cards = study_md.parse_cards(markdown) if "cards" in wanted_kinds else []
    tables = study_md.parse_tables(markdown) if "tables" in wanted_kinds else []

    placements = [(q.tag, q.section) for q in questions] + [(c.tag, c.section) for c in cards]
    mapping, created, place_problems = _resolve_nodes(
        store, goal_id, placements, titles,
        default_node=node, create=create_nodes, dry_run=dry_run,
    )
    label = source or "markdown"
    report: dict[str, Any] = {
        "source": label,
        "pool": pool,
        "dry_run": dry_run,
        "questions": None,
        "cards": None,
        "tables": None,
        "nodes_created": created,
    }

    if "questions" in wanted_kinds:
        key = study_md.parse_key(key_markdown if key_markdown is not None else markdown)
        problems = list(q_problems) + list(place_problems)
        imported = skipped = 0
        for q in questions:
            row = study_md.match_key(q, key)
            if row is None:
                problems.append(f"question {q.number}: no answer-key row")
                continue
            letters = [letter for letter, _ in q.options]
            if row.letter not in letters:
                problems.append(f"question {q.number}: key {row.letter} is not one of its options")
                continue
            node_id = mapping.get((q.tag, q.section))
            if node_id is None:
                continue
            texts = [text for _, text in q.options]
            skey = source_key(goal_id, QUESTION_KIND, q.stem, *texts)
            if store.one("SELECT 1 FROM items WHERE source_key = ?", (skey,)):
                skipped += 1
                continue
            imported += 1
            if dry_run:
                continue
            items_mod.add(
                store,
                node_id,
                {
                    "stem": q.stem,
                    "options": texts,
                    "answer": texts[letters.index(row.letter)],
                    "kind": QUESTION_KIND,
                    "explanation": row.explanation,
                    "source": f"{label}#{q.number}",
                    "components": [q.tag] if q.tag else [],
                    "distractor_misconceptions": {},
                },
                author=author,
                source_key=skey,
                pool=pool,
            )
        report["questions"] = {
            "parsed": len(questions),
            "imported": imported,
            "skipped_existing": skipped,
            "problems": problems,
        }

    if "cards" in wanted_kinds:
        imported = skipped = 0
        for c in cards:
            node_id = mapping.get((c.tag, c.section))
            if node_id is None:
                continue
            added = _add_card(
                store, goal_id, node_id, c.front, c.back,
                author=author, source=label, dry_run=dry_run,
            )
            imported += int(added)
            skipped += int(not added)
        report["cards"] = {"parsed": len(cards), "imported": imported, "skipped_existing": skipped}

    if "tables" in wanted_kinds:
        imported = skipped = 0
        for t in tables:
            node_id = None
            if t.tag:
                title = titles.get(t.tag) or t.tag
                node_id = graph_mod.resolve(store, title, required=False)
            added = _save_table(
                store, goal_id, t.title, t.columns, t.rows,
                node_id=node_id, source=f"{label}#{t.section or ''}".rstrip("#"),
                author=author, dry_run=dry_run,
            )
            imported += int(added is not None and added["new"])
            skipped += int(added is not None and not added["new"])
        report["tables"] = {
            "parsed": len(tables), "imported": imported, "skipped_existing": skipped
        }
    return report


# --------------------------------------------------------------------------- the bank
def bank_counts(store: Store, goal_id: str) -> dict[str, Any]:
    rows = _items_of_kind(store, goal_id, QUESTION_KIND)
    now = datetime.now(UTC)
    checked = unchecked = rejected = due = new = 0
    fresh_ids: list[str] = []
    for row in rows:
        if row["status"] in items_mod.EVIDENCE_STATUSES:
            checked += 1
        elif _rejected(store, row):
            rejected += 1
            continue
        else:
            unchecked += 1
        due_at = _card_due(store, row["item_id"])
        if due_at is None and not _seen(store, row["item_id"], ("answer", "probe_answer")):
            new += 1
        elif due_at is not None and due_at <= now:
            due += 1
        fresh_ids.append(row["item_id"])
    limit = store.settings.practice_new_per_day
    sealed = [
        r for r in _items_of_kind(store, goal_id, QUESTION_KIND, sealed="only")
        if not _rejected(store, r)
    ]
    return {
        "total": len(rows),
        "sealed": len(sealed),
        "checked": checked,
        "unchecked": unchecked,
        "rejected": rejected,
        "due_now": due,
        "new_available": new,
        "new_today": _first_seen_today(store, fresh_ids, ("answer", "probe_answer")),
        "new_limit": limit,
    }


def _question_payload(
    store: Store,
    row: Any,
    titles: dict[str, str],
    reason: str,
    index: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    options = json.loads(row["options"])
    order = shown_order(row["stem"], options)
    meta = (index or {}).get(row["node_id"])
    return {
        "item_id": row["item_id"],
        "item_version_id": row["item_version_id"],
        "node_id": row["node_id"],
        "node_title": titles.get(row["node_id"], row["node_id"]),
        "ref": meta["ref"] if meta else None,
        "area": {"code": meta["area"], "title": meta["area_title"]} if meta else None,
        "stem": row["stem"],
        "options": shown_options(options, order),
        "order": order,
        "allow_idk": True,
        "checked": row["status"] in items_mod.EVIDENCE_STATUSES,
        "reason": reason,
        "context": _context_for(store, row["item_id"], ("answer", "probe_answer")),
    }


def _focus(
    store: Store, goal_id: str, focus: str | None, index: dict[str, dict[str, Any]]
) -> tuple[set[str] | None, dict[str, str]]:
    """``focus`` → (the concepts it covers, or None for all; a label for the UI).

    An area code (``3``, ``area 3``) covers that area's concepts in the blueprint; anything
    else is a concept (ref, alias, id or title) of the goal.
    """

    text = (focus or "").strip()
    if not text or text.lower() == "mixed":
        return None, {"kind": "mixed", "label": "Mixed"}
    code = re.sub(r"^(?:área|area)\s*", "", text, flags=re.IGNORECASE)
    areas = {meta["area"]: meta["area_title"] for meta in index.values()}
    if code in areas:
        nodes = {node for node, meta in index.items() if meta["area"] == code}
        return nodes, {"kind": "area", "label": f"Área {code} · {areas[code]}"}
    node = graph_mod.resolve(store, text, required=False)
    if node and node in _goal_nodes(store, goal_id):
        meta = index.get(node)
        title = _node_titles(store, [node]).get(node, node)
        label = f"{meta['ref']} {meta['title']}" if meta else title
        return {node}, {"kind": "node", "label": label}
    raise LearnerError(
        f"unknown focus {focus!r}: an area code of the blueprint or a concept of the goal"
    )


def practice_next(
    store: Store, goal_id: str, *, n: int = 1, focus: str | None = None
) -> dict[str, Any]:
    """Due questions first (most overdue), then new ones interleaved by the blueprint, up to
    the daily cap. ``focus`` narrows both to an area or a concept.

    Holdouts are never served (CONTRACTS.md: `holdout-check` is their only door), a question
    a blind solver disagreed with is held back until a person looks at it, and sealed mock
    items wait for a mock.
    """

    n = max(1, min(int(n or 1), 50))
    index = blueprint_mod.index(store, goal_id)
    scope, focus_info = _focus(store, goal_id, focus, index)
    rows = [r for r in _items_of_kind(store, goal_id, QUESTION_KIND) if not _rejected(store, r)]
    if scope is not None:
        rows = [r for r in rows if r["node_id"] in scope]
    titles = _node_titles(store, list({r["node_id"] for r in rows}))
    now = datetime.now(UTC)
    due: list[tuple[datetime, Any]] = []
    fresh: list[Any] = []
    seen_by_node: dict[str, int] = {}
    for row in rows:
        at = _card_due(store, row["item_id"])
        seen = at is not None or bool(_seen(store, row["item_id"], ("answer", "probe_answer")))
        if not seen:
            fresh.append(row)
            continue
        seen_by_node[row["node_id"]] = seen_by_node.get(row["node_id"], 0) + 1
        if at is not None and at <= now:
            due.append((at, row))
    due.sort(key=lambda pair: (pair[0], pair[1]["item_id"]))
    counts = bank_counts(store, goal_id)
    counts["focus"] = focus_info
    picks = [_question_payload(store, row, titles, "due", index) for _, row in due[:n]]
    room = max(0, counts["new_limit"] - counts["new_today"])
    weights = concept_weights({r["node_id"] for r in rows}, index)
    for row in interleave(fresh, seen_by_node, weights, min(room, n - len(picks))):
        picks.append(_question_payload(store, row, titles, "new", index))
    note = None
    if not picks:
        if counts["total"] == 0:
            note = "no questions in the bank yet: import some"
        elif not rows:
            note = f"no questions for {focus_info['label']} yet"
        elif fresh and room == 0:
            note = f"today's {counts['new_limit']} new questions are done; nothing is due"
        else:
            note = "nothing is due right now"
    return {"questions": picks, "counts": counts, "done": not picks, "note": note}


def practice_answer(
    store: Store,
    *,
    item_id: str,
    response: str | None,
    order: Any = None,
    confidence: int | None = None,
    idk: bool = False,
    session_id: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """Grade against the stored key, record an ``answer`` (``rubric``), schedule, explain.

    ``response`` is a letter of the order the options were shown in (``order``, as served;
    today's order when it is missing) or the option's own text.
    """

    from . import api  # the recording path every other answer takes

    item = items_mod.get(store, item_id)
    version = item.version
    if version is None or version.kind != QUESTION_KIND:
        is_card = bool(version and version.kind == CARD_KIND)
        raise LearnerError(
            f"item {item_id} is not a practice question"
            + (" (it is a flashcard: use cards review)" if is_card else "")
        )
    if item.holdout:
        raise LearnerError(f"item {item_id} is a hidden holdout: answer it through holdout-check")
    shown = valid_order(order, len(version.options)) or shown_order(
        version.stem, version.options
    )
    chosen = None if idk else shown_choice(version.options, shown, response)
    if not idk and chosen is None:
        raise LearnerError(
            f"response {response!r} is not one of the options "
            + ", ".join(o["key"] for o in shown_options(version.options, shown))
        )
    correct_option = shown_choice(version.options, shown, version.answer)
    correct = bool(chosen and correct_option and chosen["text"] == correct_option["text"])
    context = _context_for(store, item_id, ("answer", "probe_answer"))
    receipt = api.record_answer(
        store,
        session_id=session_id,
        item_id=item_id,
        response=chosen["text"] if chosen else None,
        correct=correct,
        confidence=confidence,
        idk=idk,
        assistance_level=0,
        context=context,
        channel=channel,
        prompt_version=PRACTICE_PROMPT_VERSION,
        grader_version=BANK_GRADER_VERSION,
        evaluation_method="rubric",
        extra={"shown_order": shown},
    )
    schedule = receipt.get("schedule")
    notes = []
    if not receipt["wrote_evidence"]:
        # unchecked: scheduled for practice all the same, but not proof of anything yet
        schedule = fsrs_sched.review(
            store, item_id, correct=correct, assistance_level=0, confidence=confidence, idk=idk
        )
        notes.append(
            "not yet verified by an independent checker — practice only, not proof"
        )
    return {
        "item_id": item_id,
        "correct": correct,
        "idk": bool(idk),
        "your_answer": chosen,
        "correct_answer": correct_option,
        "explanation": version.explanation,
        "checked": item.status in items_mod.EVIDENCE_STATUSES,
        "counts_toward_mastery": receipt["counts_toward_mastery"],
        "context": context,
        "node_state": receipt["node_state"],
        "schedule": {"due": schedule["due"], "rating": schedule["rating"]} if schedule else None,
        "note": "; ".join(notes) or None,
        "event_id": receipt["event_id"],
    }


def bank_pending(store: Store, goal_id: str, *, limit: int = 25) -> dict[str, Any]:
    """Questions with no blind check on their current version: stem and options, no key.
    Sealed mock items are included: a mock may only use checked ones."""

    rows = _items_of_kind(store, goal_id, QUESTION_KIND, sealed="include")
    titles = _node_titles(store, list({r["node_id"] for r in rows}))
    pending = []
    for row in rows:
        if row["status"] != "TEACHING_ONLY":
            continue
        if _latest_validation(store, row["item_id"], row["item_version_id"]):
            continue
        pending.append(
            {
                "item_id": row["item_id"],
                "node_title": titles.get(row["node_id"], row["node_id"]),
                "stem": row["stem"],
                "options": _keyed_options(json.loads(row["options"])),
                "author": row["author"],
            }
        )
    limit = max(1, min(int(limit or 25), 200))
    return {"goal_id": goal_id, "pending": len(pending), "items": pending[:limit]}


def blind_check(
    store: Store,
    item_id: str,
    *,
    answer: str | None,
    by: str,
    ambiguous: bool = False,
    notes: str | None = None,
) -> dict[str, Any]:
    """Record a blind solve: the key is compared here, so the caller never needs to see it.

    Pass when the solver chose the keyed option and did not call the question ambiguous;
    fail otherwise. Recorded as a ``blind_solver`` validation, so the author-is-not-the-
    validator rule and the rejection rate apply exactly as for any other validation.
    """

    item = items_mod.get(store, item_id)
    version = item.version
    if version is None or version.kind != QUESTION_KIND:
        raise LearnerError(f"item {item_id} is not a multiple-choice question")
    chosen = _option_for(version.options, answer)
    keyed = _option_for(version.options, version.answer)
    matched = bool(chosen and keyed and chosen["text"] == keyed["text"])
    result = "pass" if matched and not ambiguous else "fail"
    record = items_mod.validate(
        store,
        item_id,
        by=by,
        result=result,
        notes=json.dumps(
            {
                "solver_answer": chosen["key"] if chosen else answer,
                "ambiguous": bool(ambiguous),
                "notes": notes,
            },
            ensure_ascii=False,
        ),
        evaluation_method="blind_solver",
    )
    return {
        "item_id": item_id,
        "result": result,
        "status": record.status,
        "key_matched": matched,
        "ambiguous": bool(ambiguous),
    }


def bank_review(store: Store, goal_id: str) -> dict[str, Any]:
    """Questions whose latest blind check failed — keys included, for a person to judge."""

    rows = _items_of_kind(store, goal_id, QUESTION_KIND, sealed="include")
    titles = _node_titles(store, list({r["node_id"] for r in rows}))
    out = []
    for row in rows:
        if not _rejected(store, row):
            continue
        last = _latest_validation(store, row["item_id"], row["item_version_id"])
        try:
            meta = json.loads(last["notes"] or "{}")
        except (TypeError, ValueError):
            meta = {"notes": last["notes"]}
        options = json.loads(row["options"])
        solver = _option_for(options, meta.get("solver_answer"))
        out.append(
            {
                "item_id": row["item_id"],
                "node_title": titles.get(row["node_id"], row["node_id"]),
                "stem": row["stem"],
                "options": _keyed_options(options),
                "answer": _option_for(options, row["answer"]),
                "solver_answer": solver,
                "ambiguous": bool(meta.get("ambiguous")),
                "notes": meta.get("notes"),
                "validator": last["validator"],
                "explanation": row["explanation"],
            }
        )
    return {"goal_id": goal_id, "items": out}


# --------------------------------------------------------------------------- cards
def _add_card(
    store: Store,
    goal_id: str,
    node_id: str,
    front: str,
    back: str,
    *,
    author: str,
    source: str | None,
    dry_run: bool = False,
) -> bool:
    front, back = (front or "").strip(), (back or "").strip()
    if not front or not back:
        raise LearnerError("a flashcard needs a front and a back")
    skey = source_key(goal_id, CARD_KIND, front, back)
    if store.one("SELECT 1 FROM items WHERE source_key = ?", (skey,)):
        return False
    if not dry_run:
        items_mod.add(
            store,
            node_id,
            {"stem": front, "answer": back, "kind": CARD_KIND, "source": source},
            author=author,
            source_key=skey,
        )
    return True


def cards_add(
    store: Store, goal_id: str, cards: list[dict[str, Any]], *, author: str, source: str | None
) -> dict[str, Any]:
    """Cards the harness wrote: ``[{front, back, node}]``. ``node`` is a title, alias or id."""

    _goal_nodes(store, goal_id)
    imported = skipped = 0
    for card in cards or []:
        if not card.get("node"):
            raise LearnerError("every card needs a node (the concept it belongs to)")
        node_id = graph_mod.resolve(store, str(card["node"]))
        added = _add_card(
            store, goal_id, node_id, card.get("front", ""), card.get("back", ""),
            author=author, source=card.get("source") or source,
        )
        imported += int(added)
        skipped += int(not added)
    return {"parsed": len(cards or []), "imported": imported, "skipped_existing": skipped}


def card_counts(store: Store, goal_id: str) -> dict[str, Any]:
    rows = _items_of_kind(store, goal_id, CARD_KIND)
    now = datetime.now(UTC)
    due = new = 0
    for row in rows:
        at = _card_due(store, row["item_id"])
        if at is None:
            new += 1
        elif at <= now:
            due += 1
    return {
        "total": len(rows),
        "due_now": due,
        "new_available": new,
        "new_today": _first_seen_today(store, [r["item_id"] for r in rows], ("card_review",)),
        "new_limit": store.settings.cards_new_per_day,
    }


def cards_next(store: Store, goal_id: str, *, n: int = 1) -> dict[str, Any]:
    """Due cards first, then new ones up to the daily cap. Fronts only."""

    n = max(1, min(int(n or 1), 50))
    rows = _items_of_kind(store, goal_id, CARD_KIND)
    titles = _node_titles(store, list({r["node_id"] for r in rows}))
    now = datetime.now(UTC)
    due: list[tuple[datetime, Any]] = []
    fresh: list[Any] = []
    for row in rows:
        at = _card_due(store, row["item_id"])
        if at is None:
            fresh.append(row)
        elif at <= now:
            due.append((at, row))
    due.sort(key=lambda pair: (pair[0], pair[1]["item_id"]))
    counts = card_counts(store, goal_id)

    def shape(row: Any, reason: str) -> dict[str, Any]:
        return {
            "item_id": row["item_id"],
            "node_id": row["node_id"],
            "node_title": titles.get(row["node_id"], row["node_id"]),
            "front": row["stem"],
            "reason": reason,
        }

    picks = [shape(row, "due") for _, row in due[:n]]
    room = max(0, counts["new_limit"] - counts["new_today"])
    picks += [shape(row, "new") for row in fresh[: min(room, n - len(picks))]]
    return {"cards": picks, "counts": counts, "done": not picks}


def _card(store: Store, item_id: str) -> Any:
    item = items_mod.get(store, item_id)
    if item.version is None or item.version.kind != CARD_KIND:
        raise LearnerError(f"item {item_id} is not a flashcard")
    return item


def card_reveal(store: Store, item_id: str) -> dict[str, Any]:
    item = _card(store, item_id)
    return {
        "item_id": item_id,
        "front": item.version.stem,
        "back": item.version.answer,
        "source": item.version.source,
    }


def card_review(
    store: Store,
    item_id: str,
    *,
    rating: str,
    session_id: str | None = None,
    channel: str | None = None,
) -> dict[str, Any]:
    """The learner's own rating: a ``card_review`` event (``self_report``) plus FSRS."""

    key = (rating or "").strip().lower()
    if key not in RATINGS:
        raise LearnerError(f"rating must be one of {', '.join(RATINGS)}")
    item = _card(store, item_id)
    session = None
    if session_id:
        session = store.one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        if not session:
            raise LearnerError(f"unknown session {session_id!r}")
    goal_row = store.one(
        "SELECT goal_id FROM node_goals WHERE node_id = ? LIMIT 1", (item.node_id,)
    )
    goal_id = session["goal_id"] if session else (goal_row["goal_id"] if goal_row else None)
    event = events_mod.append(
        store,
        kind="card_review",
        session_id=session_id,
        goal_id=goal_id,
        node_id=item.node_id,
        item_version_id=item.current_version_id,
        response=key,
        correct=int(key != "again"),
        channel=channel or (session["channel"] if session else "claude-code"),
        context=_context_for(store, item_id, ("card_review",)),
        prompt_version=PRACTICE_PROMPT_VERSION,
        grader_version=CARD_GRADER_VERSION,
        evaluation_method="self_report",
        payload={"item_id": item_id, "rating": key},
    )
    schedule = fsrs_sched.review_with_rating(store, item_id, RATINGS[key])
    return {
        "item_id": item_id,
        "rating": key,
        "event_id": event.event_id,
        "schedule": {"due": schedule["due"], "state": schedule["state"]},
        "counts_toward_mastery": False,
        "note": "self-rated: schedules your reviews, never counts as proof",
    }


def _anki(text: str) -> str:
    """One field of an Anki text import: no tabs, newlines as <br>."""

    return (text or "").replace("\t", " ").replace("\r\n", "\n").replace("\n", "<br>")


def cards_export(
    store: Store, goal_id: str, *, fmt: str = "tsv", include: str = "cards"
) -> dict[str, Any]:
    """Front, back and tags per line — importable into Anki as plain text."""

    if fmt not in ("tsv", "csv"):
        raise LearnerError("format must be tsv or csv")
    if include not in ("cards", "questions", "all"):
        raise LearnerError("include must be cards, questions or all")
    rows: list[tuple[str, str, str]] = []
    kinds = {"cards": [CARD_KIND], "questions": [QUESTION_KIND], "all": [CARD_KIND, QUESTION_KIND]}
    for kind in kinds[include]:
        # a question a blind solver disagreed with may have a wrong key: it is held back
        # from practice, and it must not leave for another app with that key on its back
        found = [r for r in _items_of_kind(store, goal_id, kind) if not _rejected(store, r)]
        titles = _node_titles(store, list({r["node_id"] for r in found}))
        for r in found:
            tag = re.sub(r"\s+", "_", titles.get(r["node_id"], "")).strip("_")
            if kind == CARD_KIND:
                rows.append((r["stem"], r["answer"], tag))
                continue
            options = json.loads(r["options"])
            keyed = _option_for(options, r["answer"])
            front = r["stem"] + "\n\n" + "\n".join(
                f"{o['key']}) {o['text']}" for o in _keyed_options(options)
            )
            back = f"{keyed['key']}) {keyed['text']}" if keyed else r["answer"]
            if r["explanation"]:
                back += "\n\n" + r["explanation"]
            rows.append((front, back, tag))
    buffer = io.StringIO()
    if fmt == "tsv":
        for front, back, tag in rows:
            buffer.write(f"{_anki(front)}\t{_anki(back)}\t{tag}\n")
    else:
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(["front", "back", "tags"])
        writer.writerows(rows)
    return {
        "goal_id": goal_id,
        "format": fmt,
        "include": include,
        "count": len(rows),
        "text": buffer.getvalue(),
    }


# --------------------------------------------------------------------------- tables
def _table_hash(title: str, columns: list[str], rows: list[list[str]]) -> str:
    return hashlib.sha256(
        json.dumps([title.strip().lower(), columns, rows], ensure_ascii=False).encode()
    ).hexdigest()


def _save_table(
    store: Store,
    goal_id: str,
    title: str,
    columns: list[str],
    rows: list[list[str]],
    *,
    node_id: str | None,
    source: str | None,
    author: str,
    dry_run: bool = False,
) -> dict[str, Any] | None:
    columns = [str(c).strip() for c in columns or []]
    if len(columns) < 2:
        raise LearnerError("a table needs at least two columns")
    clean_rows = []
    for row in rows or []:
        cells = [str(c).strip() for c in row]
        if len(cells) != len(columns):
            raise LearnerError(
                f"every row needs {len(columns)} cells; got {len(cells)} in {cells[:1]}"
            )
        if any(cells):
            clean_rows.append(cells)
    if not clean_rows:
        raise LearnerError("a table needs at least one row")
    title = (title or "").strip() or " vs ".join(columns[1:])
    digest = _table_hash(title, columns, clean_rows)
    existing = store.one(
        "SELECT table_id FROM study_tables WHERE goal_id = ? AND content_sha256 = ?",
        (goal_id, digest),
    )
    if existing:
        return {"table_id": existing["table_id"], "new": False}
    if dry_run:
        return {"table_id": None, "new": True}
    table_id = prefixed("t")
    store.insert(
        "study_tables",
        {
            "table_id": table_id,
            "goal_id": goal_id,
            "node_id": node_id,
            "title": title,
            "columns": json.dumps(columns, ensure_ascii=False),
            "rows": json.dumps(clean_rows, ensure_ascii=False),
            "source": source,
            "author": author,
            "content_sha256": digest,
            "created_at": utcnow(),
            "retired": 0,
        },
    )
    return {"table_id": table_id, "new": True}


def table_save(
    store: Store,
    goal_id: str,
    *,
    title: str,
    columns: list[str],
    rows: list[list[str]],
    node: str | None = None,
    source: str | None = None,
    author: str,
) -> dict[str, Any]:
    _goal_nodes(store, goal_id)
    node_id = graph_mod.resolve(store, node) if node else None
    saved = _save_table(
        store, goal_id, title, columns, rows, node_id=node_id, source=source, author=author
    )
    return {**table_get(store, saved["table_id"]), "new": saved["new"]}


def _table_row_payload(row: Any, titles: dict[str, str], *, full: bool) -> dict[str, Any]:
    columns = json.loads(row["columns"])
    rows = json.loads(row["rows"])
    data = {
        "table_id": row["table_id"],
        "goal_id": row["goal_id"],
        "title": row["title"],
        "node_id": row["node_id"],
        "node_title": titles.get(row["node_id"]) if row["node_id"] else None,
        "columns": columns,
        "row_count": len(rows),
        "source": row["source"],
        "author": row["author"],
        "created_at": row["created_at"],
    }
    if full:
        data["rows"] = rows
    return data


def tables_list(store: Store, goal_id: str) -> dict[str, Any]:
    _goal_nodes(store, goal_id)
    rows = store.query(
        "SELECT * FROM study_tables WHERE goal_id = ? AND retired = 0 "
        "ORDER BY created_at ASC, table_id ASC",
        (goal_id,),
    )
    titles = _node_titles(store, [r["node_id"] for r in rows if r["node_id"]])
    return {"tables": [_table_row_payload(r, titles, full=False) for r in rows]}


def table_get(store: Store, table_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM study_tables WHERE table_id = ?", (table_id,))
    if not row:
        raise LearnerError(f"unknown table {table_id!r}")
    titles = _node_titles(store, [row["node_id"]] if row["node_id"] else [])
    return _table_row_payload(row, titles, full=True)


def table_cards(store: Store, table_id: str, *, author: str) -> dict[str, Any]:
    """Flashcards from a table's cells.

    Two columns: one card per row, left → right. Three or more, where the first column
    labels the rows (a comparison): one card per cell, "<column> — <row label>" → cell.
    Cards need a concept, so a table not filed under one cannot be turned into cards.
    """

    table = table_get(store, table_id)
    if not table["node_id"]:
        raise LearnerError(
            f"table {table_id} is not filed under a concept, so its cards would have none"
        )
    columns, rows = table["columns"], table["rows"]
    pairs: list[tuple[str, str]] = []
    if len(columns) == 2:
        pairs = [(row[0], row[1]) for row in rows if row[0] and row[1]]
    else:
        for row in rows:
            for c in range(1, len(columns)):
                if row[0] and row[c]:
                    pairs.append((f"{columns[c]} — {row[0]}", row[c]))
    imported = skipped = 0
    for front, back in pairs:
        added = _add_card(
            store, table["goal_id"], table["node_id"], front, back,
            author=author, source=f"table {table_id}",
        )
        imported += int(added)
        skipped += int(not added)
    return {"parsed": len(pairs), "imported": imported, "skipped_existing": skipped}


def overview(store: Store, goal_id: str) -> dict[str, Any]:
    from . import exam  # exam builds on this module

    mocks = exam.mock_list(store, goal_id)
    return {
        "goal_id": goal_id,
        "bank": bank_counts(store, goal_id),
        "cards": card_counts(store, goal_id),
        "tables": tables_list(store, goal_id)["tables"],
        "blueprint": bool(blueprint_mod.rows(store, goal_id)),
        "mock": {
            "sealed_available": mocks["sealed_available"],
            "open": mocks["open"]["session_id"] if mocks["open"] else None,
        },
    }
