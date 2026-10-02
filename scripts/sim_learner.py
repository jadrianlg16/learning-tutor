"""Scripted-learner harness for the automatable Stage 1 gates.

IDEA.md *Staging*, row 1, names two gates that a machine can check without a real learner:
"probe measurably shorter on session 2" and "false-mastery rate on holdouts acceptable". The
item-rejection rate is the third Stage 0 number. This script drives the learner core through
those numbers with a *truth-table* learner: no model, no gateway, no LLM — every answer is
decided by a rule over (node, context), so the harness measures the learner model, not the
tutor.

What it does, in order (all calls go through ``learning_tutor.learner.api`` — the same
functions the ``learner`` CLI, ``learner-svc`` and the MCP server call):

1. Goal + a synthetic chain graph ``N1 -> N2 -> ... -> Nk`` of strict prerequisites.
2. Per node: ``--items-per-node`` items plus one *designated holdout* item, all authored by
   ``author-model`` and validated by ``solver-model`` (``blind_solver``). ``--rejected-items``
   extra items are authored and *failed* at validation, so the rejection rate has a
   denominator.
3. **Session 1** (``gap_days`` before "now"): probe in ``--mode probe`` until one of the
   ``probe.md`` stop rules fires, then ``--practice-rounds`` rounds of in-session answers on
   every validated item.
4. **Session 2** ("now"): probe again (the number that must shrink), then ``--mode review``
   until nothing is due (these answers carry ``context=delayed``, which is what lets a node
   reach ``known``).
5. After every answer, every ``PRACTICE_EVIDENCE`` item with ``LT_PROMOTE_MIN_USES`` uses is
   promoted. The designated item is promoted with ``LT_HOLDOUT_FRACTION=1.0`` and every
   other with ``0.0``, so exactly one holdout per node exists regardless of the id hash.
6. With ``LT_HOLDOUT_DELAY_DAYS=0`` the holdouts are due at once; ``holdouts.due()`` is
   called with the harness clock and every due holdout is answered with the right context.
7. ``api.metrics`` is read and the JSON report is printed.

The learner's rules
-------------------
* ``perfect`` — every answer correct.
* ``--forgets N2`` — correct on everything served *by* ``next`` (probe, practice, review),
  wrong on the hidden holdout checks of that node. That is the crammer whose knowledge
  looks whole from inside the session and is not: the node is ``known`` at the moment the
  holdout is served and fails it, which is exactly what ``false_mastery`` counts.
* ``--never-knows N4`` — wrong on everything about that node, so the probe has something
  to be shorter *about* and the budget matters.

Rates over an empty denominator are ``null``, as in ``metrics.py``; the harness never turns
them into zeros. ``--setup-only`` stops after the graph, which is the empty-denominator case.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

# runnable as `uv run python scripts/sim_learner.py` from the repo root without an install
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from learning_tutor.config import Settings, get_settings  # noqa: E402
from learning_tutor.learner import api  # noqa: E402
from learning_tutor.learner import graph as graph_mod  # noqa: E402
from learning_tutor.learner import holdouts as holdouts_mod  # noqa: E402
from learning_tutor.learner import items as items_mod  # noqa: E402
from learning_tutor.learner.store import LearnerError, Store, open_store  # noqa: E402

AUTHOR = "author-model"
VALIDATOR = "solver-model"
#: the learner's answers are judged by an independent solver, never by the tutoring model
EVALUATION_METHOD = "blind_solver"
GOAL_ID = "g_sim"
#: probe.md stop rule 3: three consecutive wrong / IDK answers
PROBE_CONSECUTIVE_FAILS = 3
#: a guard on the review loop, on top of the store's own session cap
MAX_REVIEW_PICKS = 200
CORRECT = "right"
WRONG = "wrong"
OPTIONS = [CORRECT, WRONG, "other"]


def iso(when: datetime) -> str:
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclasses.dataclass(frozen=True)
class LearnerRules:
    """The truth table. ``answer`` is the only thing the harness asks of it."""

    forgets: frozenset[str] = frozenset()
    never_knows: frozenset[str] = frozenset()

    def answer(self, node_title: str, *, holdout: bool) -> bool:
        if node_title in self.never_knows:
            return False
        if holdout and node_title in self.forgets:
            return False
        return True

    @property
    def name(self) -> str:
        if not self.forgets and not self.never_knows:
            return "perfect"
        parts = []
        if self.forgets:
            parts.append("forgets " + ",".join(sorted(self.forgets)))
        if self.never_knows:
            parts.append("never knows " + ",".join(sorted(self.never_knows)))
        return "; ".join(parts)


@dataclasses.dataclass
class Config:
    data_dir: str | None = None
    nodes: int = 4
    items_per_node: int = 2
    rejected_items: int = 1
    practice_rounds: int = 2
    gap_days: int = 30
    forgets: tuple[str, ...] = ()
    never_knows: tuple[str, ...] = ()
    setup_only: bool = False


class Clock:
    """The harness clock. Session 1 sits ``gap_days`` before session 2; session 2 is an hour
    before real now; the holdout checks are at real now — so FSRS (which reads the wall
    clock inside ``next``) sees the session-1 cards as due, and ``holdouts.due()`` receives
    this clock explicitly."""

    def __init__(self, gap_days: int) -> None:
        self.now = datetime.now(UTC).replace(microsecond=0)
        self.session2 = self.now - timedelta(hours=1)
        self.session1 = self.session2 - timedelta(days=gap_days)
        self.current = self.session1

    def tick(self, seconds: int = 60) -> str:
        self.current = self.current + timedelta(seconds=seconds)
        return iso(self.current)


class Harness:
    def __init__(self, store: Store, rules: LearnerRules, config: Config) -> None:
        self.store = store
        self.rules = rules
        self.config = config
        self.clock = Clock(config.gap_days)
        self.titles: dict[str, str] = {}  # node_id -> title
        self.designated: set[str] = set()  # item ids meant to become holdouts
        self.counters: dict[str, int] = {
            "answers_recorded": 0,
            "promotions": 0,
            "holdout_checks": 0,
        }
        self.probe_log: dict[str, list[dict[str, Any]]] = {"session_1": [], "session_2": []}

    # ------------------------------------------------------------------ setup
    def setup(self) -> None:
        api.goal_add(self.store, goal_id=GOAL_ID, title="Synthetic chain", depth="apply")
        titles = [f"N{i + 1}" for i in range(self.config.nodes)]
        payload = {
            "nodes": [{"title": t} for t in titles],
            "edges": [
                {"from": a, "to": b, "type": "strict_prerequisite", "provenance": "model"}
                for a, b in zip(titles, titles[1:], strict=False)
            ],
        }
        api.graph_import(self.store, GOAL_ID, payload)
        self.titles = {n.node_id: n.title for n in graph_mod.get_nodes(self.store, GOAL_ID)}
        unknown = (set(self.rules.forgets) | set(self.rules.never_knows)) - set(titles)
        if unknown:
            raise LearnerError(f"rule names nodes that are not in the graph: {sorted(unknown)}")

    def author_items(self) -> None:
        for node_id, title in self.titles.items():
            for k in range(self.config.items_per_node):
                self._author(node_id, f"{title} practice {k + 1}")
            holdout = self._author(node_id, f"{title} hidden check")
            self.designated.add(holdout)
        first = next(iter(self.titles))
        for k in range(self.config.rejected_items):
            spec = self._spec(f"{self.titles[first]} ambiguous {k + 1}")
            record = api.item_add(self.store, first, spec, author=AUTHOR)
            api.item_validate(
                self.store,
                record["item_id"],
                by=VALIDATOR,
                result="fail",
                notes="two options defensible",
                evaluation_method=EVALUATION_METHOD,
            )

    def _spec(self, stem: str) -> dict[str, Any]:
        return {"stem": stem, "options": OPTIONS, "answer": CORRECT, "kind": "mc"}

    def _author(self, node_id: str, stem: str) -> str:
        record = api.item_add(self.store, node_id, self._spec(stem), author=AUTHOR)
        api.item_validate(
            self.store,
            record["item_id"],
            by=VALIDATOR,
            result="pass",
            evaluation_method=EVALUATION_METHOD,
        )
        return record["item_id"]

    # --------------------------------------------------------------- answering
    def _answer(self, item_id: str, *, session_id: str | None, context: str) -> bool:
        item = items_mod.get(self.store, item_id)
        correct = self.rules.answer(self.titles[item.node_id], holdout=item.holdout)
        api.record_answer(
            self.store,
            session_id=session_id,
            item_id=item_id,
            response=CORRECT if correct else WRONG,
            correct=correct,
            confidence=4 if correct else 2,
            assistance_level=0,
            context=context,
            evaluation_method=EVALUATION_METHOD,
            ts=self.clock.tick(),
        )
        self.counters["answers_recorded"] += 1
        self._promote_at_threshold()
        return correct

    def _promote_at_threshold(self) -> None:
        threshold = self.store.settings.promote_min_uses
        rows = self.store.query(
            "SELECT item_id FROM items WHERE status = 'PRACTICE_EVIDENCE' AND retired = 0"
        )
        for row in rows:
            item_id = row["item_id"]
            if len(items_mod.uses(self.store, item_id)) < threshold:
                continue
            fraction = 1.0 if item_id in self.designated else 0.0
            base = self.store.settings
            self.store.settings = dataclasses.replace(base, holdout_fraction=fraction)
            try:
                api.item_promote(self.store, item_id)
            except LearnerError:
                # never answered correctly, or IDK-only: the item is not promotable yet
                continue
            finally:
                self.store.settings = base
            self.counters["promotions"] += 1

    # ---------------------------------------------------------------- sessions
    def probe(self, session_id: str, label: str) -> int:
        asked = 0
        consecutive_fails = 0
        while True:
            result = api.next_(
                self.store, GOAL_ID, mode="probe", n=1, session_id=session_id
            )
            picks = result["picks"]
            if not picks or picks[0]["item_id"] is None:
                break  # stop rule 1 (no node) — or the budget, which also empties picks
            pick = picks[0]
            correct = self._answer(pick["item_id"], session_id=session_id, context="probe")
            asked += 1
            self.probe_log[label].append({"node": pick["node_title"], "correct": correct})
            consecutive_fails = 0 if correct else consecutive_fails + 1
            if consecutive_fails >= PROBE_CONSECUTIVE_FAILS:
                break  # stop rule 3
        return asked

    def practice(self, session_id: str) -> None:
        for _ in range(self.config.practice_rounds):
            for node_id in self.titles:
                for item in items_mod.for_node(self.store, node_id, include_holdouts=False):
                    if item.status in items_mod.EVIDENCE_STATUSES:
                        self._answer(item.item_id, session_id=session_id, context="in-session")

    def review(self, session_id: str) -> int:
        answered: set[str] = set()
        for _ in range(MAX_REVIEW_PICKS):
            result = api.next_(self.store, GOAL_ID, mode="review", n=1, session_id=session_id)
            picks = [p for p in result["picks"] if p["item_id"]]
            if not picks or picks[0]["item_id"] in answered:
                break
            pick = picks[0]
            answered.add(pick["item_id"])
            self._answer(pick["item_id"], session_id=session_id, context=pick["context"])
        return len(answered)

    def holdout_checks(self) -> int:
        checked = 0
        self.clock.current = self.clock.now
        # Holdouts are stamped with the real clock when they are assigned
        # (items.assign_holdout), which can be later than the harness's frozen ``now`` on a
        # slow run; query at whichever is later so their age is never negative.
        check_at = max(self.clock.now, datetime.now(UTC)) + timedelta(seconds=1)
        for row in holdouts_mod.due(self.store, GOAL_ID, now=check_at):
            self._answer(row["item_id"], session_id=None, context=row["context"])
            checked += 1
        self.counters["holdout_checks"] = checked
        return checked

    def session(self, label: str, when: datetime, *, review: bool) -> dict[str, Any]:
        self.clock.current = when
        sid = api.session_start(self.store, goal_id=GOAL_ID)["session_id"]
        probes = self.probe(sid, label)
        reviewed = 0
        if review:
            reviewed = self.review(sid)
        else:
            self.practice(sid)
        api.session_end(self.store, sid, summary=f"scripted {label}")
        return {"session_id": sid, "probe_questions": probes, "review_answers": reviewed}

    # -------------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        self.setup()
        sessions: dict[str, Any] = {}
        if not self.config.setup_only:
            self.author_items()
            sessions["session_1"] = self.session("session_1", self.clock.session1, review=False)
            sessions["session_2"] = self.session("session_2", self.clock.session2, review=True)
            self.holdout_checks()
        return self.report(sessions)

    def report(self, sessions: dict[str, Any]) -> dict[str, Any]:
        metrics = api.metrics(self.store, GOAL_ID)
        states = {
            n["title"]: n["state"]["state"] for n in api.graph_show(self.store, GOAL_ID)["nodes"]
        }
        s1 = sessions.get("session_1", {}).get("probe_questions")
        s2 = sessions.get("session_2", {}).get("probe_questions")
        return {
            "learner": self.rules.name,
            "config": dataclasses.asdict(self.config),
            "settings": {
                "holdout_delay_days": self.store.settings.holdout_delay_days,
                "promote_min_uses": self.store.settings.promote_min_uses,
                "probe_budget": self.store.settings.probe_budget,
            },
            "probe_questions": {
                "session_1": s1,
                "session_2": s2,
                "shorter_on_session_2": (s2 < s1) if s1 is not None and s2 is not None else None,
                "log": self.probe_log,
            },
            "false_mastery": metrics["false_mastery"],
            "item_rejection": metrics["item_rejection"],
            "holdout_success_7d": metrics["holdout_success_7d"],
            "holdout_checks": self.counters["holdout_checks"],
            "counters": self.counters,
            "sessions": sessions,
            "node_states": states,
        }


def build_settings(data_dir: str | None) -> Settings:
    """``LT_HOLDOUT_DELAY_DAYS=0`` is what the gates are run with; the harness pins it so the
    holdouts are due inside the same run. Everything else comes from the environment."""

    return dataclasses.replace(get_settings(data_dir), holdout_delay_days=0)


def run(config: Config) -> dict[str, Any]:
    settings = build_settings(config.data_dir)
    rules = LearnerRules(
        forgets=frozenset(config.forgets), never_knows=frozenset(config.never_knows)
    )
    with open_store(settings) as store:
        return Harness(store, rules, config).run()


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data-dir", default=os.environ.get("LT_SIM_DATA_DIR"))
    parser.add_argument("--nodes", type=int, default=4)
    parser.add_argument("--items-per-node", type=int, default=2)
    parser.add_argument("--rejected-items", type=int, default=1)
    parser.add_argument("--practice-rounds", type=int, default=2)
    parser.add_argument("--gap-days", type=int, default=30)
    parser.add_argument("--forgets", action="append", default=[], metavar="NODE")
    parser.add_argument("--never-knows", action="append", default=[], metavar="NODE")
    parser.add_argument("--setup-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.data_dir:
        parser.error("--data-dir (or LT_SIM_DATA_DIR) is required: the harness needs a fresh dir")
    return Config(
        data_dir=args.data_dir,
        nodes=args.nodes,
        items_per_node=args.items_per_node,
        rejected_items=args.rejected_items,
        practice_rounds=args.practice_rounds,
        gap_days=args.gap_days,
        forgets=tuple(args.forgets),
        never_knows=tuple(args.never_knows),
        setup_only=args.setup_only,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    try:
        report = run(config)
    except LearnerError as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
