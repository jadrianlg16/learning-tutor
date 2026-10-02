"""Item bank and its lifecycle: TEACHING_ONLY -> PRACTICE_EVIDENCE -> MASTERY_ELIGIBLE.

Rules enforced here (from IDEA.md *Adopted now*):

* Every new item starts ``TEACHING_ONLY``. Only ``PRACTICE_EVIDENCE`` and above write
  evidence about a node.
* The same model is never sole author, solver and judge: ``validate`` refuses a validator
  identity equal to the item's author. A passing validation by someone else promotes the
  item to ``PRACTICE_EVIDENCE``; a failing one leaves it ``TEACHING_ONLY`` and counts
  toward the item rejection rate.
* ``promote`` to ``MASTERY_ELIGIBLE`` requires the item to have been used at least
  ``LT_PROMOTE_MIN_USES`` times (default 3) with sane statistics — at least one correct and
  at least one non-trivial response, and no open ambiguity dispute.
* Holdout membership is assigned at promotion time, deterministically, by hashing the item
  id: ``hash_fraction(item_id) < LT_HOLDOUT_FRACTION``. So roughly that fraction of
  ``MASTERY_ELIGIBLE`` items are holdouts, and re-running the assignment never flips one.
* Any edit to a stem or its options creates a new ``item_version``; evidence points at the
  version that was actually served.
"""

from __future__ import annotations

import json
from typing import Any

from .events import TRUSTED_EVALUATION_METHODS, VALID_EVALUATION_METHODS
from .ids import hash_fraction, prefixed
from .ids import item_id as make_item_id
from .models import Item, ItemVersion
from .store import LearnerError, Store, utcnow

STATUSES = ("TEACHING_ONLY", "PRACTICE_EVIDENCE", "MASTERY_ELIGIBLE")
EVIDENCE_STATUSES = ("PRACTICE_EVIDENCE", "MASTERY_ELIGIBLE")
#: `practice` = every item until migration 4; `mock` = sealed for mock exams
POOLS = ("practice", "mock")


def _version_model(row: Any) -> ItemVersion:
    data = dict(row)
    data["options"] = json.loads(data["options"])
    data["components"] = json.loads(data["components"])
    data["distractor_misconceptions"] = json.loads(data["distractor_misconceptions"])
    return ItemVersion.model_validate(data)


def get(store: Store, item: str) -> Item:
    row = store.one("SELECT * FROM items WHERE item_id = ?", (item,))
    if not row:
        raise LearnerError(f"unknown item {item!r}")
    version = None
    if row["current_version_id"]:
        vrow = store.one(
            "SELECT * FROM item_versions WHERE item_version_id = ?", (row["current_version_id"],)
        )
        version = _version_model(vrow) if vrow else None
    return Item(
        item_id=row["item_id"],
        node_id=row["node_id"],
        status=row["status"],
        current_version_id=row["current_version_id"],
        author=row["author"],
        created_at=row["created_at"],
        holdout=bool(row["holdout"]),
        retired=bool(row["retired"]),
        version=version,
    )


def add(
    store: Store,
    node_id: str,
    spec: dict[str, Any],
    *,
    author: str = "model",
    item: str | None = None,
    source_key: str | None = None,
    pool: str = "practice",
) -> Item:
    """Create an item (or a new version of ``item``) from a spec dict.

    Spec: ``{stem, options[], answer, distractor_misconceptions{}, kind, components[]}``,
    plus the optional ``explanation`` and ``source`` an importer carries (migration 3).
    ``source_key`` is an importer's content hash: the unique index on it is what makes
    importing the same file twice add nothing. ``pool`` = ``mock`` seals a new item for
    mock exams (migration 4); a new version keeps its item's pool.
    """

    stem = spec.get("stem")
    answer = spec.get("answer")
    if not stem or answer is None:
        raise LearnerError("item needs a stem and an answer")
    options = spec.get("options") or []
    kind = spec.get("kind", "mc")
    if pool not in POOLS:
        raise LearnerError(f"pool must be one of {', '.join(POOLS)}")
    if kind == "mc":
        if len(options) < 2:
            raise LearnerError("a multiple-choice item needs at least two options")
        if answer not in options:
            raise LearnerError("the answer must be one of the options")

    now = utcnow()
    if item:
        existing = get(store, item)
        if existing.node_id != node_id:
            raise LearnerError("cannot move an item to another node with a new version")
        version_no = int(
            store.one(
                "SELECT COALESCE(MAX(version), 0) AS v FROM item_versions WHERE item_id = ?",
                (item,),
            )["v"]
        ) + 1
        iid = item
    else:
        iid = make_item_id()
        version_no = 1
        store.insert(
            "items",
            {
                "item_id": iid,
                "node_id": node_id,
                "status": "TEACHING_ONLY",
                "current_version_id": None,
                "author": author,
                "created_at": now,
                "holdout": 0,
                "retired": 0,
                "source_key": source_key,
                "pool": pool,
            },
        )

    ivid = prefixed("iv")
    store.insert(
        "item_versions",
        {
            "item_version_id": ivid,
            "item_id": iid,
            "version": version_no,
            "stem": stem,
            "options": json.dumps(options),
            "answer": answer,
            "distractor_misconceptions": json.dumps(spec.get("distractor_misconceptions") or {}),
            "kind": kind,
            "components": json.dumps(spec.get("components") or []),
            "surface_form": spec.get("surface_form"),
            "author": author,
            "created_at": now,
            "explanation": spec.get("explanation"),
            "source": spec.get("source"),
        },
    )
    updates: dict[str, Any] = {"current_version_id": ivid}
    if item and version_no > 1:
        # a reworded stem is a new question: it must be validated again
        updates["status"] = "TEACHING_ONLY"
    store.update("items", {"item_id": iid}, updates)
    return get(store, iid)


def validate(
    store: Store,
    item: str,
    *,
    by: str,
    result: str,
    notes: str | None = None,
    evaluation_method: str | None = None,
) -> Item:
    """Record an independent check of an item.

    ``evaluation_method`` is optional and unstated by default, which keeps the Stage 0
    behaviour exactly: the guarantee is the identity check below. When a method *is*
    stated, ``host_llm`` records the validation but does not promote the item — a model
    approving its own question has not validated anything (CONTRACTS.md, *Adopted from the
    Tutor MCP audit*).
    """

    if result not in ("pass", "fail"):
        raise LearnerError("validation result must be pass or fail")
    if evaluation_method is not None and evaluation_method not in VALID_EVALUATION_METHODS:
        raise LearnerError(
            f"unknown evaluation method {evaluation_method!r}: one of "
            + "|".join(sorted(VALID_EVALUATION_METHODS))
        )
    record = get(store, item)
    if not record.current_version_id:
        raise LearnerError(f"item {item} has no version")
    if record.version and record.version.kind == "card":
        raise LearnerError(
            f"item {item} is a flashcard: a card is self-rated recall with no key to solve, "
            "so it cannot be validated and never writes evidence"
        )
    if by.strip().lower() == record.author.strip().lower():
        raise LearnerError(
            "the validator must differ from the author: the same model is never sole "
            f"author, solver and judge (author={record.author!r})"
        )
    store.insert(
        "item_validations",
        {
            "validation_id": prefixed("val"),
            "item_id": item,
            "item_version_id": record.current_version_id,
            "validator": by,
            "result": result,
            "notes": notes,
            "evaluation_method": evaluation_method,
            "ts": utcnow(),
        },
    )
    trusted = evaluation_method is None or evaluation_method in TRUSTED_EVALUATION_METHODS
    if result == "pass" and trusted and record.status == "TEACHING_ONLY":
        store.update("items", {"item_id": item}, {"status": "PRACTICE_EVIDENCE"})
    return get(store, item)


def uses(store: Store, item: str) -> list[Any]:
    return store.query(
        "SELECT e.* FROM events e JOIN item_versions v ON v.item_version_id = e.item_version_id "
        "WHERE v.item_id = ? AND e.kind IN ('answer','probe_answer') ORDER BY e.ts",
        (item,),
    )


def promote(store: Store, item: str, *, min_uses: int | None = None) -> Item:
    record = get(store, item)
    settings = store.settings
    threshold = settings.promote_min_uses if min_uses is None else min_uses
    if record.status == "MASTERY_ELIGIBLE":
        return record
    if record.status != "PRACTICE_EVIDENCE":
        raise LearnerError(
            f"item {item} is {record.status}: it must pass validation "
            "(PRACTICE_EVIDENCE) before promotion"
        )
    rows = uses(store, item)
    if len(rows) < threshold:
        raise LearnerError(
            f"item {item} has {len(rows)} recorded uses, needs at least {threshold}"
        )
    correct = sum(1 for r in rows if r["correct"] == 1)
    idk = sum(1 for r in rows if r["idk"] == 1)
    if correct == 0:
        raise LearnerError(
            f"item {item} has never been answered correctly: check the key before promoting"
        )
    if idk == len(rows):
        raise LearnerError(f"item {item} has only 'I don't know' responses")
    open_dispute = store.one(
        "SELECT dispute_id FROM disputes WHERE item_id = ? AND status = 'open' "
        "AND type = 'ambiguous question'",
        (item,),
    )
    if open_dispute:
        raise LearnerError(
            f"item {item} has an open ambiguity dispute ({open_dispute['dispute_id']})"
        )
    store.update("items", {"item_id": item}, {"status": "MASTERY_ELIGIBLE"})
    assign_holdout(store, item)
    return get(store, item)


def assign_holdout(store: Store, item: str) -> bool:
    """Deterministically decide whether a MASTERY_ELIGIBLE item is a hidden holdout."""

    fraction = store.settings.holdout_fraction
    is_holdout = hash_fraction(item) < fraction
    store.update("items", {"item_id": item}, {"holdout": int(is_holdout)})
    if is_holdout:
        store.execute(
            "INSERT OR IGNORE INTO holdouts(item_id, assigned_at) VALUES (?, ?)",
            (item, utcnow()),
        )
    else:
        store.execute("DELETE FROM holdouts WHERE item_id = ?", (item,))
    return is_holdout


def for_node(
    store: Store,
    node_id: str,
    *,
    statuses: tuple[str, ...] = EVIDENCE_STATUSES,
    include_holdouts: bool = False,
) -> list[Item]:
    marks = ", ".join("?" for _ in statuses)
    sql = (
        f"SELECT item_id FROM items WHERE node_id = ? AND retired = 0 AND status IN ({marks})"
    )
    params: list[Any] = [node_id, *statuses]
    if not include_holdouts:
        sql += " AND holdout = 0"
    sql += " ORDER BY created_at ASC, item_id ASC"
    return [get(store, row["item_id"]) for row in store.query(sql, params)]


def version_of(store: Store, item_version_id: str) -> ItemVersion | None:
    row = store.one("SELECT * FROM item_versions WHERE item_version_id = ?", (item_version_id,))
    return _version_model(row) if row else None


def item_of_version(store: Store, item_version_id: str) -> str | None:
    row = store.one(
        "SELECT item_id FROM item_versions WHERE item_version_id = ?", (item_version_id,)
    )
    return row["item_id"] if row else None


def rejection_rate(store: Store) -> dict[str, Any]:
    rows = store.query("SELECT result, COUNT(*) AS n FROM item_validations GROUP BY result")
    counts = {row["result"]: row["n"] for row in rows}
    total = sum(counts.values())
    return {
        "validations": total,
        "rejected": counts.get("fail", 0),
        "rate_percent": round(100 * counts.get("fail", 0) / total) if total else None,
    }
