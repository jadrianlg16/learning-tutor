"""Rule-based node state derivation.

Concept state is *derived from item evidence by transparent rules* — not scheduled, not
fitted. FSRS runs on items; BKT is a candidate behind ``LT_EVIDENCE_MODEL=bkt`` and is
deliberately not implemented (see IDEA.md, *Adopted into the design*: compare it against
these rules on hidden-item prediction before adopting it).

What counts as evidence for a node
----------------------------------
* ``answer`` / ``probe_answer`` events whose item is ``PRACTICE_EVIDENCE`` or
  ``MASTERY_ELIGIBLE``. Items that are still ``TEACHING_ONLY`` teach; they never write
  evidence.
* ``teach_back`` events, scored 0-3 against a versioned rubric; ``record teach-back``
  stores the raw score in the payload and writes ``correct = 1`` when the score is 2 or
  more, so a teach-back is weighed exactly like any other attempt.
* Holdout answers count as evidence too, but the false-mastery metric always recomputes the
  state *as of the moment before* the holdout was served, so measurement stays honest.

Counters
--------
* ``independent_passes`` — correct, not "I don't know", assistance level 0-1.
* ``assisted_passes``    — correct with assistance 2-4.
* ``unearned_passes``    — correct at assistance 5-6. Recorded, never counted toward
  mastery (hard rule 1 in CONTRACTS.md).
* ``self_graded_passes`` — correct, but judged by the tutoring model itself
  (``evaluation_method = host_llm``). Recorded, scheduled, never counted: only
  ``blind_solver``, ``rubric`` and ``human`` evaluations can move a node to ``known``
  (CONTRACTS.md, *Adopted from the Tutor MCP audit*). Events written before migration 2
  carry no method at all; they keep their Stage 0 meaning, because a migration must not
  retroactively invalidate a history it knows nothing about.
* ``fails``              — incorrect, or "I don't know".
* ``last_delayed``       — outcome of the most recent event with context ``delayed``.
* ``transfer_passes``    — independent passes with context ``transfer``.

The rules, in order
-------------------
1. An ``active`` misconception on the node  -> ``misconception``.
2. No evidence at all                       -> ``unknown`` (uncertainty ``high``).
3. No pass of any kind                      -> ``unknown``.
4. ``known`` requires all of:
   - at least 2 independent passes (a pass judged ``host_llm`` is not one of them),
   - the last delayed retrieval did not fail,
   - at least one delayed pass **or** one transfer pass (fluency in-session is not
     retention),
   - no failure among the 3 most recent evidence events.
5. Anything else                            -> ``fragile``.

Uncertainty is a label, never a decimal in the view. With
``weight = 2*independent + assisted + fails + transfer + (1 if any delayed evidence)`` and
``conflict = fails > 0 and independent_passes > 0``:

* no evidence -> ``high``
* ``weight >= 6``, no conflict, last delayed retrieval passed -> ``low``
* ``weight >= 3`` -> ``medium``
* otherwise -> ``high``
"""

from __future__ import annotations

from typing import Any

from . import events as events_mod
from . import misconceptions as misc_mod
from .models import NodeState
from .store import LearnerError, Store

EVIDENCE_KINDS = ("answer", "probe_answer", "teach_back")
TEACH_BACK_PASS_SCORE = 2
UNEARNED_ASSISTANCE = 5
INDEPENDENT_ASSISTANCE = 1


def _evidence_rows(store: Store, node_id: str, before: str | None = None) -> list[Any]:
    sql = (
        "SELECT e.*, i.status AS item_status FROM events e "
        "LEFT JOIN item_versions v ON v.item_version_id = e.item_version_id "
        "LEFT JOIN items i ON i.item_id = v.item_id "
        "WHERE e.node_id = ? AND e.kind IN ('answer','probe_answer','teach_back')"
    )
    params: list[Any] = [node_id]
    if before:
        sql += " AND e.ts < ?"
        params.append(before)
    sql += " ORDER BY e.ts ASC, e.event_id ASC"
    rows = []
    for row in store.query(sql, params):
        if row["kind"] == "teach_back":
            rows.append(row)
        elif row["item_status"] in ("PRACTICE_EVIDENCE", "MASTERY_ELIGIBLE"):
            rows.append(row)
    return rows


def _outcome(row: Any) -> str:
    """'pass' | 'fail' | 'unearned' | 'self_graded' for one evidence row.

    ``unearned`` and ``self_graded`` are both "recorded, never counted": the first because
    the learner was handed the answer, the second because the tutoring model graded its
    own learner. Neither is a failure — they simply are not passes.
    """

    if row["idk"] or not row["correct"]:
        return "fail"
    if row["assistance_level"] >= UNEARNED_ASSISTANCE:
        return "unearned"
    if not events_mod.is_trusted(_evaluation_method(row)):
        return "self_graded"
    return "pass"


def _evaluation_method(row: Any) -> str | None:
    """The row's method, tolerating a row read from a pre-migration database."""

    try:
        return row["evaluation_method"]
    except (IndexError, KeyError):  # pragma: no cover - schema always migrated in practice
        return None


def derive(store: Store, node_id: str, *, before: str | None = None) -> NodeState:
    if store.settings.evidence_model == "bkt":
        return _bkt(store, node_id)
    if store.settings.evidence_model != "rules":
        raise LearnerError(
            f"unknown LT_EVIDENCE_MODEL {store.settings.evidence_model!r} (rules|bkt)"
        )

    row = store.one("SELECT title FROM nodes WHERE node_id = ?", (node_id,))
    if not row:
        raise LearnerError(f"unknown node {node_id!r}")

    rows = _evidence_rows(store, node_id, before=before)
    independent = assisted = unearned = self_graded = fails = transfer = 0
    last_delayed: str | None = None
    recent_outcomes: list[str] = []

    for r in rows:
        outcome = _outcome(r)
        recent_outcomes.append(outcome)
        if outcome == "pass":
            if r["assistance_level"] <= INDEPENDENT_ASSISTANCE:
                independent += 1
                if r["context"] == "transfer":
                    transfer += 1
            else:
                assisted += 1
        elif outcome == "unearned":
            unearned += 1
        elif outcome == "self_graded":
            self_graded += 1
        else:
            fails += 1
        if r["context"] == "delayed":
            # a self-graded or handed-over delayed answer is not a passed retrieval, but
            # it is not a failed one either: it leaves the last delayed outcome unchanged.
            if outcome == "pass":
                last_delayed = "pass"
            elif outcome == "fail":
                last_delayed = "fail"

    active = misc_mod.active_for_node(store, node_id, before=before)
    reasons: list[str] = []
    if active:
        state = "misconception"
        reasons.append(f"active misconception: {active['claim']}")
    elif not rows:
        state = "unknown"
        reasons.append("no evidence recorded")
    elif independent == 0 and assisted == 0:
        state = "unknown"
        reasons.append(
            "no counted passes recorded"
            + (f" ({self_graded} self-graded)" if self_graded else "")
        )
    else:
        last_three = recent_outcomes[-3:]
        checks = {
            "two independent passes": independent >= 2,
            "last delayed retrieval did not fail": last_delayed != "fail",
            "a delayed or transfer pass": last_delayed == "pass" or transfer >= 1,
            "no failure in the last three attempts": "fail" not in last_three,
        }
        if all(checks.values()):
            state = "known"
            reasons.append("independent passes plus delayed or transfer evidence")
        else:
            state = "fragile"
            reasons.extend(f"missing: {name}" for name, ok in checks.items() if not ok)

    weight = 2 * independent + assisted + fails + transfer + (1 if last_delayed else 0)
    conflict = fails > 0 and independent > 0
    if not rows:
        uncertainty = "high"
    elif weight >= 6 and not conflict and last_delayed == "pass":
        uncertainty = "low"
    elif weight >= 3:
        uncertainty = "medium"
    else:
        uncertainty = "high"

    return NodeState(
        node_id=node_id,
        title=row["title"],
        state=state,
        independent_passes=independent,
        assisted_passes=assisted,
        unearned_passes=unearned,
        self_graded_passes=self_graded,
        fails=fails,
        last_delayed=last_delayed,
        transfer_passes=transfer,
        uncertainty=uncertainty,
        active_misconception=active["claim"] if active else None,
        reasons=reasons,
    )


def derive_all(
    store: Store, node_ids: list[str], *, before: str | None = None
) -> dict[str, NodeState]:
    return {nid: derive(store, nid, before=before) for nid in node_ids}


def _bkt(store: Store, node_id: str) -> NodeState:
    raise NotImplementedError(
        "BKT evidence model is a candidate, not an implementation. IDEA.md requires "
        "comparing it against the rule model on hidden-item prediction before adopting "
        "it; until then run with LT_EVIDENCE_MODEL=rules."
    )
