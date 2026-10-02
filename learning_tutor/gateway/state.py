"""The gateway's own durable state: the phase machine and the in-flight question cache.

Two things live here and nowhere else.

**The phase machine.** ``grounding -> plan -> probe -> teach -> done``, per goal.
CONTRACTS.md returns ``phase`` on three routes, so it has to survive a restart; it is not
derivable from learner-svc alone (a goal with a graph and no probe events could be
mid-plan-review or mid-probe).

**The answer keys.** ``learner next`` deliberately serves a stem and options and *not* the
key, and the gateway grades. So the key has to be somewhere the client cannot reach:
here, written when the item is authored, read when the answer arrives. A client that
posts its own ``correct`` flag is ignored — ``tests/test_gateway_routes.py`` pins that.

This is *not* a second learner model. Nothing here is evidence, nothing here is a number
about the learner, and losing this file costs the session in flight and nothing else
(CONTRACTS.md hard rule 5: durable facts go through learner-svc).

File: ``LT_DATA_DIR/gateway/state.json``. One writer, whole-file atomic replace.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

Phase = Literal["grounding", "plan", "probe", "teach", "done"]

PHASES: tuple[Phase, ...] = ("grounding", "plan", "probe", "teach", "done")

#: Which phase each route expects, and where a goal legally goes next. Kept as data so the
#: docs table and the code cannot drift.
NEXT_PHASE: dict[str, Phase] = {
    "grounding": "plan",
    "plan": "probe",
    "probe": "teach",
    "teach": "done",
    "done": "done",
}


@dataclass
class Question:
    """One question the gateway has served and is waiting on. Includes the key."""

    item_id: str
    item_version_id: str | None
    node_id: str
    node_title: str
    stem: str
    options: list[str]
    answer: str
    kind: str = "concept"
    explanation: str = ""
    distractor_misconceptions: dict[str, str] = field(default_factory=dict)
    context: str = "in-session"
    status: str = "TEACHING_ONLY"
    attempts: int = 0
    assistance_level: int = 0
    ask_confidence: bool = True


@dataclass
class TeachState:
    """Where the teaching loop is, for one goal."""

    node_id: str | None = None
    node_title: str = ""
    strategy: str = ""
    strategy_switches: int = 0
    backed_up: bool = False
    consecutive_fails: int = 0
    nodes_since_teach_back: int = 0
    nodes_completed: int = 0
    steps: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SessionState:
    session_id: str | None = None
    started_at: str | None = None
    channel: str = "web"
    minutes_cap: int | None = None
    probe_asked: int = 0
    consecutive_idk: int = 0
    consecutive_wrong: int = 0
    ended: bool = False


@dataclass
class GoalState:
    goal_id: str
    phase: Phase = "grounding"
    #: The GoalContract fields learner-svc has no column for.
    contract: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    plan_approved: bool = False
    session: SessionState = field(default_factory=SessionState)
    teach: TeachState = field(default_factory=TeachState)
    #: item_id -> Question. The answer keys, and the only place they live gateway-side.
    questions: dict[str, Question] = field(default_factory=dict)
    #: session_id -> item_id currently in flight.
    in_flight: dict[str, str] = field(default_factory=dict)
    misconceptions: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    log_paths: list[str] = field(default_factory=list)


def _to_jsonable(value: Any) -> Any:
    if isinstance(value, GoalState | SessionState | TeachState | Question):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, dict):
        return {k: _to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_jsonable(v) for v in value]
    return value


def _goal_from_json(data: dict[str, Any]) -> GoalState:
    goal = GoalState(goal_id=str(data.get("goal_id") or ""))
    goal.phase = data.get("phase") if data.get("phase") in PHASES else "grounding"
    goal.contract = dict(data.get("contract") or {})
    goal.plan = dict(data.get("plan") or {})
    goal.plan_approved = bool(data.get("plan_approved"))
    goal.session = SessionState(**{**asdict(SessionState()), **(data.get("session") or {})})
    goal.teach = TeachState(**{**asdict(TeachState()), **(data.get("teach") or {})})
    goal.questions = {
        item_id: Question(**{**asdict(Question("", None, "", "", "", [], "")), **payload})
        for item_id, payload in (data.get("questions") or {}).items()
    }
    goal.in_flight = dict(data.get("in_flight") or {})
    goal.misconceptions = dict(data.get("misconceptions") or {})
    goal.log_paths = list(data.get("log_paths") or [])
    return goal


class GatewayState:
    """The whole file, loaded once and written on every mutation."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self.goals: dict[str, GoalState] = {}
        self.load()

    # ------------------------------------------------------------------- io
    def load(self) -> None:
        with self._lock:
            if not self.path.exists():
                self.goals = {}
                return
            try:
                raw = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                # A corrupt state file loses the session in flight, never evidence.
                self.goals = {}
                return
            self.goals = {
                goal_id: _goal_from_json({**payload, "goal_id": goal_id})
                for goal_id, payload in (raw.get("goals") or {}).items()
            }

    def save(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "goals": {gid: _to_jsonable(goal) for gid, goal in self.goals.items()},
            }
            handle = tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=self.path.parent,
                prefix=self.path.name,
                suffix=".tmp",
                delete=False,
            )
            try:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.flush()
                os.fsync(handle.fileno())
            finally:
                handle.close()
            os.replace(handle.name, self.path)

    # ---------------------------------------------------------------- goals
    def get(self, goal_id: str) -> GoalState:
        with self._lock:
            goal = self.goals.get(goal_id)
            if goal is None:
                goal = GoalState(goal_id=goal_id)
                self.goals[goal_id] = goal
            return goal

    def has(self, goal_id: str) -> bool:
        return goal_id in self.goals

    def phase(self, goal_id: str) -> Phase:
        return self.get(goal_id).phase

    def set_phase(self, goal_id: str, phase: Phase) -> Phase:
        if phase not in PHASES:
            raise ValueError(f"unknown phase {phase!r}")
        with self._lock:
            goal = self.get(goal_id)
            goal.phase = phase
            self.save()
            return phase

    def advance(self, goal_id: str) -> Phase:
        """Move one step along ``grounding -> plan -> probe -> teach -> done``.

        Only ever forward, and only one step: a route that wants a different phase says so
        with :meth:`set_phase`, in the open, rather than by skipping.
        """

        with self._lock:
            goal = self.get(goal_id)
            return self.set_phase(goal_id, NEXT_PHASE[goal.phase])

    # ------------------------------------------------------------ questions
    def remember(self, goal_id: str, question: Question, *, session_id: str | None = None) -> None:
        with self._lock:
            goal = self.get(goal_id)
            goal.questions[question.item_id] = question
            if session_id:
                goal.in_flight[session_id] = question.item_id
            self.save()

    def question(self, goal_id: str, item_id: str) -> Question | None:
        return self.get(goal_id).questions.get(item_id)

    def in_flight(self, goal_id: str, session_id: str) -> Question | None:
        goal = self.get(goal_id)
        item_id = goal.in_flight.get(session_id)
        return goal.questions.get(item_id) if item_id else None

    def clear_in_flight(self, goal_id: str, session_id: str) -> None:
        with self._lock:
            goal = self.get(goal_id)
            goal.in_flight.pop(session_id, None)
            self.save()

    def touch(self) -> None:
        """Persist whatever a caller mutated on a returned dataclass."""

        self.save()
