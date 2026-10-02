"""Decisions. All of them. None of them made by a model.

IDEA.md *Review response* → *Decision ownership*: "One orchestration module decides the
next question, pass/fail, strategy change, session end and graph revision — regardless of
whether the LLM runs in the harness (Mode B) or in ``tutor-svc`` (Mode A). ``tutor-svc``
supplies prompts, not decisions."

This is that module. Every function here is pure, or a thin wrapper over a learner-svc
client that only shapes a call and reads its answer. No function here imports
:mod:`learning_tutor.llm`, and ``tests/test_tutor_orchestrator.py`` exercises the lot with
no model, no network and no database.

Where a rule comes from is written next to it. The rules are not invented here; they are
transcriptions of ``skills/teach/SKILL.md``, ``prompts/v1/checkpoint.md``,
``prompts/v1/plan.md`` and IDEA.md, into a form that cannot be talked out of itself.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Literal

# --------------------------------------------------------------------------- constants

#: Minutes per node used by the feasibility arithmetic. plan.md: "PACE_MIN = 12 minutes
#: per node is a Stage-0 planning constant — an assumption, not a measurement.
#: Recalibrate from `learner metrics --goal G` once real sessions exist."
PACE_MIN = int(os.environ.get("LT_PACE_MIN") or 12)

#: SKILL.md *Teach-back — after every 2–3 nodes*. Two is the low end of the stated range,
#: so a session that ends early has still produced one teach-back.
TEACH_BACK_EVERY_NODES = int(os.environ.get("LT_TEACH_BACK_EVERY_NODES") or 2)

#: SKILL.md *Frustration*: "Three fails in a row on one node".
FAILS_BEFORE_STRATEGY_SWITCH = 3

#: checkpoint.md level 6 is the reveal; 5 and 6 never count toward mastery
#: (CONTRACTS.md hard rule 1, learner/events.py ``UNEARNED_ASSISTANCE``).
REVEAL_LEVEL = 6
MAX_HINT_LEVEL = 6
UNEARNED_ASSISTANCE = 5

#: probe.md: high-confidence wrong is the misconception trigger.
HIGH_CONFIDENCE = 4

#: probe.md stop rule 3.
PROBE_CONSECUTIVE_LIMIT = 3

#: The grader for a keyed multiple-choice answer. Frozen artefact, hence ``rubric`` —
#: see *Deviations* in docs/modules/tutor.md for why it is not ``deterministic``.
MC_GRADER_VERSION = "mc-key-v1"
MC_EVALUATION_METHOD = "rubric"

#: teach-step.md *Choose the strategy*. Five, and no learning styles anywhere near them.
STRATEGIES = ("example-first", "analogy-first", "visual-first", "formal-first", "socratic")

#: The order to rotate through on a strategy switch, per expertise. Socratic is never
#: first for a novice — teach-step.md: "Never for a true novice."
_ROTATION: dict[str, tuple[str, ...]] = {
    "novice": ("example-first", "visual-first", "analogy-first"),
    "intermediate": ("socratic", "example-first", "visual-first"),
    "advanced": ("formal-first", "socratic", "example-first"),
}

MISCONCEPTION_STEPS = ("reasoning", "prediction", "counterexample")

#: What a mid-step interrupt gets instead of an answer when it is asking for the
#: checkpoint's key. checkpoint.md rules 1-3, in the learner's words: no hint before an
#: attempt, one level per failed attempt, the reveal only after a genuine attempt and
#: recorded as a non-pass. Fixed text, because a model asked to refuse politely negotiates.
CHECKPOINT_ANSWER_RULE = (
    "Not the answer — not yet, and not by asking sideways.\n\n"
    "The checkpoint is the only thing in this session that produces evidence, so the rule "
    "is fixed (`checkpoint.md`):\n\n"
    "1. No hint before an attempt. Not even level 1.\n"
    "2. One hint level per failed attempt — the ladder never skips.\n"
    "3. The worked solution is level 6, it is only available after a real attempt, and it "
    "is recorded as a non-pass.\n\n"
    "Take a shot at it, even a wrong one — a wrong attempt tells us both more than a skip. "
    "Then ask for a hint and the ladder starts. Anything else about the step itself, ask "
    "away: that is what this box is for."
)

#: An interrupt that is really "just tell me the key". Deliberately narrow: it must be
#: asking *for the answer*, not asking *about* one ("why is the answer linear?" is a
#: question about the step and gets answered).
_ANSWER_REQUEST_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(what|which)\b[^?.!]{0,40}\bthe\s+(right\s+|correct\s+|actual\s+)?answer\b",
        r"\banswer\s+(is|to)\s+(the\s+)?(checkpoint|question|this|it)\b",
        r"\bwhich\s+(option|one|letter|choice)\b[^?.!]{0,40}\b(correct|right|the answer)\b",
        r"\b(tell|give|show)\s+(me\s+)?(the\s+)?(answer|key|solution)\b",
        r"\bjust\s+tell\s+me\b",
        r"\banswer\s+key\b",
        r"\bwhat\s+should\s+i\s+(pick|choose|select|answer|put)\b",
        r"\bis\s+it\s+[a-e]\s*\??\s*$",
    )
)


def asks_for_the_checkpoint_answer(text: str) -> bool:
    """Is this interrupt asking for the checkpoint's key rather than about the step?

    SKILL.md *Interrupts and disputes* answers "wait, why?" inline; CONTRACTS.md hard rule
    1 says the key is never revealed before an attempt. Those meet here: an interrupt is
    answered unless answering it would be the reveal by another route.
    """

    haystack = " ".join((text or "").split())
    return any(pattern.search(haystack) for pattern in _ANSWER_REQUEST_PATTERNS)


Decisions = Literal[
    "continue",
    "repeat",
    "back_up",
    "switch_strategy",
    "teach_back_due",
    "end_session",
]


# --------------------------------------------------------------------------- results


@dataclass(frozen=True)
class GradeResult:
    """The outcome of grading one answer against the server-side key."""

    correct: bool
    idk: bool
    normalised_response: str
    answer_key: str
    evaluation_method: str = MC_EVALUATION_METHOD
    grader_version: str = MC_GRADER_VERSION


@dataclass(frozen=True)
class HintRuling:
    allowed: bool
    level: int
    reason: str


@dataclass(frozen=True)
class ProbeStop:
    stop: bool
    reason: str


@dataclass(frozen=True)
class Decision:
    decision: Decisions
    reason: str
    misconception_suspected: bool = False
    reveal_allowed: bool = False


@dataclass(frozen=True)
class Feasibility:
    nodes_remaining: int
    pace_min: int
    minutes_needed: int
    minutes_per_session: int
    sessions_left: int | None
    minutes_available: int | None
    fits: bool | None
    statement: str
    options: list[str] = field(default_factory=list)
    sessions_per_week: int | None = None

    @property
    def sessions_needed(self) -> int | None:
        """Whole sessions of ``minutes_per_session`` that ``minutes_needed`` costs.

        ``None`` when there is no session length to divide by — the honest answer, not a
        default someone will later read as a measurement.
        """

        if self.minutes_per_session <= 0:
            return None
        return max(1, -(-self.minutes_needed // self.minutes_per_session))  # ceil

    @property
    def verdict(self) -> str:
        """``comfortable`` | ``tight`` | ``not-feasible`` | ``unknown``.

        The thresholds are **web-ui's** (`src/lib/feasibility.ts`: comfortable at 1.25x),
        so the gateway's line and the UI's own fallback cannot disagree about the colour of
        the same arithmetic.
        """

        needed = self.sessions_needed
        if self.sessions_left is None or needed is None:
            return "unknown"
        if self.sessions_left >= needed * 1.25:
            return "comfortable"
        return "tight" if self.sessions_left >= needed else "not-feasible"

    @property
    def assumption(self) -> str:
        """The one line printed under the feasibility sentence. Never hidden."""

        plural = "" if self.nodes_remaining == 1 else "s"
        weekly = f", {self.sessions_per_week} sessions/week" if self.sessions_per_week else ""
        return (
            f"{self.nodes_remaining} concept{plural} not yet known, ~{self.pace_min} min "
            f"each (PACE_MIN, a Stage-0 planning constant), "
            f"{self.minutes_per_session} min per session{weekly}. "
            "Estimate, not a measurement — recalibrate from metrics once real sessions exist."
        )

    def as_dict(self) -> dict[str, Any]:
        """The arithmetic, plus the four keys ``web-ui`` reads off it by name.

        `web-ui/src/lib/types.ts::Feasibility` is `{sessions_needed, sessions_available,
        verdict, assumption}` — not the names this dataclass uses. Both are here rather
        than one being renamed: the UI gets the field names it already reads, and the
        arithmetic keys stay for the docs, the tests and anything reading the plan state.
        """

        return {
            "nodes_remaining": self.nodes_remaining,
            "pace_min": self.pace_min,
            "minutes_needed": self.minutes_needed,
            "minutes_per_session": self.minutes_per_session,
            "sessions_left": self.sessions_left,
            "minutes_available": self.minutes_available,
            "fits": self.fits,
            "statement": self.statement,
            "options": list(self.options),
            # web-ui's names, same numbers.
            "sessions_needed": self.sessions_needed,
            "sessions_available": self.sessions_left,
            "verdict": self.verdict,
            "assumption": self.assumption,
        }


# --------------------------------------------------------------------------- probe


def next_probe_question(next_response: dict[str, Any]) -> dict[str, Any] | None:
    """The pick learner-svc chose, or ``None`` when it chose nothing.

    probe.md: "``learner next --mode probe`` picks the node. Your job is only to write a
    good item on it. Do not override the CLI's choice." So this reads; it does not rank.
    """

    picks = next_response.get("picks") or []
    return picks[0] if picks else None


def probe_stop(
    *,
    asked: int,
    budget: int,
    consecutive_idk: int = 0,
    consecutive_wrong: int = 0,
    picks_available: bool = True,
    learner_asked_to_stop: bool = False,
) -> ProbeStop:
    """probe.md *Budget and stop rules* — the four triggers, in the order stated."""

    if learner_asked_to_stop:
        return ProbeStop(True, "the learner asked to stop")
    if not picks_available:
        return ProbeStop(True, "no unknown node left to probe")
    if asked >= budget:
        return ProbeStop(True, f"probe budget spent ({asked}/{budget} questions)")
    if consecutive_idk >= PROBE_CONSECUTIVE_LIMIT:
        return ProbeStop(
            True, f"{consecutive_idk} consecutive 'I do not know' answers at the frontier"
        )
    if consecutive_wrong >= PROBE_CONSECUTIVE_LIMIT:
        return ProbeStop(True, f"{consecutive_wrong} consecutive wrong answers at the frontier")
    return ProbeStop(False, "")


# --------------------------------------------------------------------------- teach


def next_teach_node(next_response: dict[str, Any]) -> dict[str, Any] | None:
    """The edge: the pick ``learner next --mode teach`` returned, or ``None``."""

    picks = next_response.get("picks") or []
    return picks[0] if picks else None


_OPTION_KEY_RE = re.compile(r"^\s*([A-Za-z])\s*[.)\-:]\s*(.*)$", re.DOTALL)

_IDK_MARKERS = ("i do not know", "i don't know", "idk", "dont know", "do not know")


def option_key(option: str, index: int) -> str:
    """``"B. -2 — evaluate ..."`` → ``"B"``; an unlabelled option gets A, B, C, ... ."""

    match = _OPTION_KEY_RE.match(option or "")
    if match:
        return match.group(1).upper()
    return chr(ord("A") + index)


def option_text(option: str) -> str:
    match = _OPTION_KEY_RE.match(option or "")
    return match.group(2).strip() if match else (option or "").strip()


def options_for_wire(options: list[str]) -> list[dict[str, str]]:
    """The contract's ``options: [{key, text}]`` from the stored ``list[str]``."""

    return [
        {"key": option_key(option, i), "text": option_text(option)}
        for i, option in enumerate(options or [])
    ]


def is_idk(response: str | None, options: list[str] | None = None) -> bool:
    """"I do not know" is a first-class answer (IDEA.md *The probe*), whether the client
    sends the letter of the IDK option or the words."""

    text = (response or "").strip().lower()
    if not text:
        return False
    if any(marker in text for marker in _IDK_MARKERS):
        return True
    for index, option in enumerate(options or []):
        key = option_key(option, index).lower()
        if text == key and any(marker in option.lower() for marker in _IDK_MARKERS):
            return True
    return False


def grade_mc(
    response: str | None, answer_key: str, options: list[str] | None = None
) -> GradeResult:
    """Grade against the stored key. Deterministic, and the client is never consulted.

    CONTRACTS.md's ``evaluation_method`` enum has no ``deterministic`` value, so a
    key-graded answer is recorded as ``rubric`` — graded against a frozen artefact — with
    ``grader_version`` ``mc-key-v1``. See docs/modules/learner-svc.md *Known limitations*.
    """

    raw = (response or "").strip()
    key = (answer_key or "").strip()
    idk = is_idk(raw, options)
    if idk:
        return GradeResult(False, True, raw, key)

    normalised = raw.rstrip(".)").strip()
    candidates = {key.upper(), option_text(key).lower()}
    # accept either the letter or the option's full/label-stripped text
    for index, option in enumerate(options or []):
        if option_key(option, index).upper() == key.upper():
            candidates.add(option.strip().lower())
            candidates.add(option_text(option).lower())
    given = {normalised.upper(), normalised.lower(), option_text(normalised).lower()}
    correct = bool(candidates & given) and bool(normalised)
    return GradeResult(correct, False, raw, key)


def hint_allowed(level: int, attempts: int, highest_reached: int = 0) -> HintRuling:
    """checkpoint.md *Absolute rules* 2 and 3, and CONTRACTS.md hard rule 1.

    * The reveal (level 6) requires a genuine attempt first — CONTRACTS.md pins this one
      as a 409.
    * Escalation is one level at a time. A jump from 0 to 4 is refused; "never skip a
      level, never jump to the reveal because it is taking a while."
    """

    if level < 1 or level > MAX_HINT_LEVEL:
        return HintRuling(False, level, f"hint level must be 1..{MAX_HINT_LEVEL}")
    if level >= REVEAL_LEVEL and attempts < 1:
        return HintRuling(
            False, level, "no reveal before an attempt: level 6 requires a genuine attempt first"
        )
    if level > highest_reached + 1:
        return HintRuling(
            False,
            level,
            f"hints escalate one level at a time: {highest_reached} reached, "
            f"so {highest_reached + 1} is next, not {level}",
        )
    return HintRuling(True, level, "")


def counts_toward_mastery(*, correct: bool, idk: bool, assistance_level: int) -> bool:
    """checkpoint.md rule 5 / CONTRACTS.md hard rule 1: a pass at assistance >= 5 never
    counts. (learner-svc recomputes this itself; the gateway needs it to say so out loud.)
    """

    return bool(correct) and not idk and assistance_level < UNEARNED_ASSISTANCE


def decide_after_answer(
    *,
    correct: bool,
    idk: bool = False,
    confidence: int | None = None,
    assistance_level: int = 0,
    consecutive_fails: int = 0,
    nodes_since_teach_back: int = 0,
    strategy_switched: bool = False,
    backed_up: bool = False,
    minutes_elapsed: float = 0.0,
    minutes_cap: int | None = None,
) -> Decision:
    """What happens next, decided by rules.

    Precedence, and why:

    1. **Session cap** (SKILL.md *Session cap*): "At 100% finish the current checkpoint
       only — never start a new node." The checkpoint just finished, so the session ends.
    2. **Three fails in a row** (SKILL.md *Frustration*): switch strategy once, then back
       up to a prerequisite, then stop the topic for today.
    3. **Teach-back due** (SKILL.md *Teach-back*): after 2–3 nodes, and a node has just
       been completed.
    4. Otherwise continue, or repeat this node with the next hint level.

    ``misconception_suspected`` is a flag on the decision, not a decision: probe.md reads
    wrong-at-confidence-4-or-5 as a *hypothesis*, and misconception.md forbids announcing
    it before the three-step confirmation.
    """

    passed = bool(correct) and not idk
    suspected = (
        not passed
        and not idk
        and confidence is not None
        and confidence >= HIGH_CONFIDENCE
        and assistance_level == 0
    )
    reveal_allowed = not passed  # an attempt has now happened; checkpoint.md rule 3

    if minutes_cap is not None and minutes_elapsed >= minutes_cap:
        return Decision(
            "end_session",
            f"session cap reached ({int(minutes_elapsed)} of {minutes_cap} minutes); "
            "the current checkpoint was the last one",
            suspected,
            reveal_allowed,
        )

    if not passed:
        if consecutive_fails >= FAILS_BEFORE_STRATEGY_SWITCH:
            if not strategy_switched:
                return Decision(
                    "switch_strategy",
                    f"{consecutive_fails} fails in a row on this node: change the "
                    "explanation strategy once before anything else",
                    suspected,
                    reveal_allowed,
                )
            if not backed_up:
                return Decision(
                    "back_up",
                    "the new strategy failed too: back up to a prerequisite node and "
                    "teach that",
                    suspected,
                    reveal_allowed,
                )
            return Decision(
                "end_session",
                "strategy switched and backed up, still failing: this is a today-problem, "
                "not a you-problem. Stop the topic for today",
                suspected,
                reveal_allowed,
            )
        return Decision(
            "repeat",
            "not yet — same item, next hint level",
            suspected,
            reveal_allowed,
        )

    if nodes_since_teach_back >= TEACH_BACK_EVERY_NODES:
        return Decision(
            "teach_back_due",
            f"{nodes_since_teach_back} nodes since the last teach-back "
            f"(due every {TEACH_BACK_EVERY_NODES}–3)",
            suspected,
            reveal_allowed,
        )

    return Decision("continue", "checkpoint passed; next node", suspected, reveal_allowed)


def expertise_for(node_state: dict[str, Any] | None) -> str:
    """``novice`` | ``intermediate`` | ``advanced``, from evidence only.

    IDEA.md *Adapting to the person*: prior knowledge and expertise level in **that area**
    are the two legitimate per-person signals. There is no style, tone or modality input
    to this function, and there never will be (CONTRACTS.md hard rule 2).
    """

    state = (node_state or {}).get("state") or "unknown"
    independent = int((node_state or {}).get("independent_passes") or 0)
    assisted = int((node_state or {}).get("assisted_passes") or 0)
    if state == "known" or independent >= 2:
        return "advanced"
    if state in ("fragile", "misconception") or independent or assisted:
        return "intermediate"
    return "novice"


def choose_strategy(
    node_state: dict[str, Any] | None,
    expertise: str | None = None,
    *,
    switches: int = 0,
) -> str:
    """Expertise reversal (Kalyuga) *(verify)*, as a lookup table.

    Novices get the worked example; higher mastery gets the problem first. teach-step.md
    forbids Socratic for a true novice, so the novice rotation cannot reach it however
    many times the strategy is switched.
    """

    level = expertise or expertise_for(node_state)
    rotation = _ROTATION.get(level, _ROTATION["novice"])
    return rotation[max(0, switches) % len(rotation)]


# --------------------------------------------------------------- misconceptions


def misconception_next_step(
    outcomes: list[dict[str, Any]] | None,
) -> tuple[str | None, str]:
    """The next step in reasoning → prediction → counterexample, and the resulting state.

    misconception.md *Confirmation rule*: any ``dropped`` kills the hypothesis; all three
    ``held`` confirms it (``active``). Never a confirmation for a sequence that was not run.
    """

    done = list(outcomes or [])
    for entry in done:
        if (entry.get("outcome") or "") == "dropped":
            return None, "dropped"
    if len(done) >= len(MISCONCEPTION_STEPS):
        return None, "active"
    return MISCONCEPTION_STEPS[len(done)], "suspected"


def misconception_sequence(
    client: Any,
    *,
    goal_id: str,
    node_id: str,
    claim: str,
    step: str,
    outcome: str,
    session_id: str | None = None,
    prompt_version: str | None = None,
) -> dict[str, Any]:
    """Record one confirmation step through learner-svc and say what comes next.

    Thin over the client on purpose: the *ordering* rule is
    :func:`misconception_next_step` (pure, tested without a network); learner-svc owns the
    state machine and rejects an out-of-order step itself.
    """

    if step not in MISCONCEPTION_STEPS:
        raise ValueError(f"step must be one of {', '.join(MISCONCEPTION_STEPS)}")
    if outcome not in ("held", "dropped"):
        raise ValueError("outcome must be held or dropped")

    body: dict[str, Any] = {"node": node_id, "claim": claim, "step": step, "outcome": outcome}
    if prompt_version:
        body["prompt_version"] = prompt_version
    recorded = client.post("/v1/misconceptions/confirm-step", body)
    steps = recorded.get("steps") or recorded.get("confirmations") or []
    if not steps:
        # learner-svc did not hand back the history; reconstruct the minimum we know.
        steps = [{"step": step, "outcome": outcome}]
    next_step, derived = misconception_next_step(steps)
    state = recorded.get("state") or derived
    return {
        "goal_id": goal_id,
        "session_id": session_id,
        "node_id": node_id,
        "claim": claim,
        "state": state,
        "next_step": next_step if state == "suspected" else None,
        "recorded": recorded,
    }


# --------------------------------------------------------------------------- graph


def learning_path(
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    states: dict[str, str] | None = None,
) -> list[str]:
    """Teaching order: a deterministic topological sort over ``strict_prerequisite``.

    Same rule as ``learner/graph.py``'s ``topological_order`` (ready set sorted by id, a
    cycle falls back to id order) so the gateway's *path* view and learner-svc's selection
    cannot disagree about what comes first. Nodes already ``known`` stay in the order —
    the path is the route, and the map colours it.
    """

    ids = [str(node.get("node_id")) for node in nodes if node.get("node_id")]
    known_ids = set(ids)
    incoming: dict[str, set[str]] = {nid: set() for nid in ids}
    for edge in edges:
        if (edge.get("type") or "strict_prerequisite") != "strict_prerequisite":
            continue
        src = edge.get("from_node") or edge.get("from")
        dst = edge.get("to_node") or edge.get("to")
        if src in known_ids and dst in known_ids:
            incoming[str(dst)].add(str(src))

    ordered: list[str] = []
    remaining = dict(incoming)
    placed: set[str] = set()
    while remaining:
        ready = sorted(nid for nid, deps in remaining.items() if not (deps - placed))
        if not ready:
            ready = sorted(remaining)
        for nid in ready:
            ordered.append(nid)
            placed.add(nid)
            remaining.pop(nid, None)
    _ = states  # colours belong to the map, not to the order
    return ordered


def path_mermaid(
    nodes: list[dict[str, Any]],
    order: list[str],
    states: dict[str, str] | None = None,
) -> str:
    """The learner path as a straight line, coloured by state.

    IDEA.md *Adopted into the design*: "curriculum map vs learner path as two visible
    graphs". The curriculum map comes from learner-svc verbatim; this is the other one.
    """

    titles = {str(n.get("node_id")): str(n.get("title") or n.get("node_id")) for n in nodes}
    colours = states or {}
    lines = ["graph LR"]
    for nid in order:
        label = titles.get(nid, nid).replace('"', "'")
        lines.append(f'    {nid}["{label}"]')
    for first, second in zip(order, order[1:], strict=False):
        lines.append(f"    {first} --> {second}")
    lines.extend(
        [
            "    classDef unknown fill:#e5e7eb,stroke:#6b7280,color:#111827;",
            "    classDef fragile fill:#fde68a,stroke:#b45309,color:#111827;",
            "    classDef known fill:#bbf7d0,stroke:#15803d,color:#111827;",
            "    classDef misconception fill:#fecaca,stroke:#b91c1c,color:#111827;",
        ]
    )
    by_state: dict[str, list[str]] = {}
    for nid in order:
        by_state.setdefault(colours.get(nid, "unknown"), []).append(nid)
    for state in ("unknown", "fragile", "known", "misconception"):
        members = by_state.get(state)
        if members:
            lines.append(f"    class {','.join(members)} {state};")
    return "\n".join(lines)


# --------------------------------------------------------------------- feasibility


def sessions_left(
    *,
    deadline: str | None,
    today: str,
    sessions_per_week: int,
) -> int | None:
    """Whole sessions between ``today`` and ``deadline``. ``None`` when there is no deadline."""

    if not deadline:
        return None
    from datetime import date

    try:
        end = date.fromisoformat(str(deadline)[:10])
        start = date.fromisoformat(str(today)[:10])
    except ValueError:
        return None
    days = (end - start).days
    if days <= 0:
        return 0
    per_week = max(1, int(sessions_per_week or 1))
    return max(0, int(days * per_week / 7))


def feasibility(
    goal: dict[str, Any],
    graph: dict[str, Any],
    *,
    today: str,
    pace_min: int = PACE_MIN,
    sessions_per_week: int | None = None,
) -> Feasibility:
    """``nodes_remaining x PACE_MIN`` vs ``minutes_per_session x sessions_left``.

    plan.md *Feasibility, stated out loud*: show the arithmetic, and if it does not fit
    offer exactly three options — shallower depth, a narrower sub-goal, more sessions.
    "Never silently speed up."
    """

    nodes = graph.get("nodes") or []
    remaining = [
        node
        for node in nodes
        if ((node.get("state") or {}).get("state") or "unknown") != "known"
    ]
    nodes_remaining = len(remaining)
    minutes_per_session = int(goal.get("minutes_per_session") or 0)
    per_week = int(sessions_per_week or goal.get("sessions_per_week") or 3)
    left = sessions_left(
        deadline=goal.get("deadline"), today=today, sessions_per_week=per_week
    )
    needed = nodes_remaining * pace_min

    if left is None or minutes_per_session <= 0:
        return Feasibility(
            nodes_remaining=nodes_remaining,
            pace_min=pace_min,
            minutes_needed=needed,
            minutes_per_session=minutes_per_session,
            sessions_left=left,
            minutes_available=None,
            fits=None,
            statement=(
                f"{nodes_remaining} nodes left x {pace_min} min = {needed} min of teaching. "
                + (
                    "No deadline set, so there is nothing to check it against."
                    if left is None
                    else "No minutes-per-session set, so there is nothing to check it against."
                )
                + f" PACE_MIN = {pace_min} is a planning assumption, not a measurement."
            ),
            options=[],
            sessions_per_week=per_week,
        )

    available = minutes_per_session * left
    fits = needed <= available
    statement = (
        f"{nodes_remaining} nodes left x {pace_min} min = {needed} min needed; "
        f"{minutes_per_session} min x {left} sessions left = {available} min available. "
        + ("That fits." if fits else "That does not fit.")
        + f" PACE_MIN = {pace_min} is a planning assumption, not a measurement — "
        "recalibrate from metrics once real sessions exist."
    )
    options: list[str] = []
    if not fits:
        short_by = needed - available
        extra_sessions = -(-short_by // minutes_per_session)  # ceil
        keep = max(1, available // pace_min)
        options = [
            "Shallower depth — drop the Bloom level (apply -> explain). Cost: you will be "
            "able to explain it and not to do it.",
            f"A narrower sub-goal — about {keep} nodes, finished properly, instead of "
            f"{nodes_remaining} rushed.",
            f"More sessions — {extra_sessions} more session(s) of {minutes_per_session} "
            f"minutes closes the {short_by}-minute gap.",
        ]
    return Feasibility(
        nodes_remaining=nodes_remaining,
        pace_min=pace_min,
        minutes_needed=needed,
        minutes_per_session=minutes_per_session,
        sessions_left=left,
        minutes_available=available,
        fits=fits,
        statement=statement,
        options=options,
        sessions_per_week=per_week,
    )

# ----------------------------------------------------------------- promotion


#: ``items.promote``'s threshold, read from the same variable learner-svc reads so the two
#: cannot disagree about when an item has been used enough (CONTRACTS.md *holdouts*).
PROMOTE_MIN_USES_DEFAULT = 3

#: The one status learner-svc will promote from. Anything else is either not yet validated
#: (TEACHING_ONLY) or already there (MASTERY_ELIGIBLE).
PROMOTABLE_STATUS = "PRACTICE_EVIDENCE"

USE_KINDS = ("answer", "probe_answer")


@dataclass(frozen=True)
class PromotionRuling:
    """Whether the gateway should ask learner-svc to promote an item now."""

    due: bool
    uses: int
    min_uses: int
    status: str
    reason: str


def promotion_due(*, uses: int, status: str, min_uses: int | None = None) -> PromotionRuling:
    """CONTRACTS.md *Orchestrator rule (auto-promote)*: ask for promotion once a
    ``PRACTICE_EVIDENCE`` item has ``LT_PROMOTE_MIN_USES`` recorded uses — not before.

    This decides only *when to ask*. learner-svc's ``items.promote`` keeps the full rule
    (validated, enough uses, at least one correct, not all IDK, no open ambiguity dispute)
    and the holdout assignment, and refuses on its own judgement.
    """

    threshold = (
        int(os.environ.get("LT_PROMOTE_MIN_USES") or PROMOTE_MIN_USES_DEFAULT)
        if min_uses is None
        else int(min_uses)
    )
    if status == "MASTERY_ELIGIBLE":
        return PromotionRuling(False, uses, threshold, status, "already MASTERY_ELIGIBLE")
    if status != PROMOTABLE_STATUS:
        return PromotionRuling(
            False, uses, threshold, status, f"item is {status}, not {PROMOTABLE_STATUS}"
        )
    if uses < threshold:
        return PromotionRuling(
            False, uses, threshold, status, f"{uses} recorded uses, needs {threshold}"
        )
    return PromotionRuling(True, uses, threshold, status, f"{uses} uses >= {threshold}")


def count_uses(
    events: list[dict[str, Any]], *, item_id: str, item_version_id: str | None = None
) -> int:
    """Recorded uses of one item from learner-svc's raw event rows.

    Mirrors ``learner/items.py::uses``: rows of kind ``answer`` / ``probe_answer`` on the
    item. A row belongs to the item when its ``payload.item_id`` says so, or when its
    ``item_version_id`` is the version the gateway served.
    """

    n = 0
    for row in events:
        if row.get("kind") not in USE_KINDS:
            continue
        payload = row.get("payload") or {}
        if payload.get("item_id") == item_id or (
            item_version_id and row.get("item_version_id") == item_version_id
        ):
            n += 1
    return n


def promote_if_due(
    client: Any,
    *,
    node_id: str,
    item_id: str,
    item_version_id: str | None,
    status: str,
    min_uses: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """The auto-promote rule, end to end, against learner-svc.

    Reads the item's uses from ``GET /v1/events`` (learner-svc's rows, not a gateway
    counter — a replayed answer must not count twice), applies :func:`promotion_due`, and
    calls ``POST /v1/items/{i}/promote`` when it is due. A refusal from learner-svc (its
    400) is *reported*, never raised: the checkpoint was recorded either way, and the
    rule that refused is learner-svc's to explain.
    """

    rows = client.get("/v1/events", {"node": node_id, "limit": 1000})
    events = list(rows.get("events") or []) if isinstance(rows, dict) else []
    uses = count_uses(events, item_id=item_id, item_version_id=item_version_id)
    ruling = promotion_due(uses=uses, status=status, min_uses=min_uses)
    result: dict[str, Any] = {
        "attempted": False,
        "promoted": False,
        "status": ruling.status,
        "holdout": None,
        "uses": ruling.uses,
        "min_uses": ruling.min_uses,
        "reason": ruling.reason,
    }
    if not ruling.due:
        return result
    result["attempted"] = True
    try:
        promoted = client.post(
            f"/v1/items/{item_id}/promote", {}, idempotency_key=idempotency_key
        )
    except Exception as exc:  # noqa: BLE001 - the gateway's own error type, see below
        status_code = getattr(exc, "status", None)
        if status_code is None or status_code >= 500:
            raise
        result["reason"] = f"learner-svc refused: {getattr(exc, 'message', exc)}"
        return result
    promoted = promoted if isinstance(promoted, dict) else {}
    result["promoted"] = promoted.get("status") == "MASTERY_ELIGIBLE"
    result["status"] = str(promoted.get("status") or ruling.status)
    result["holdout"] = promoted.get("holdout")
    result["reason"] = str(promoted.get("note") or ruling.reason)
    return result
