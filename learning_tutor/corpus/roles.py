"""The three source roles and the rules that follow from them.

From IDEA.md, *Three source roles*: all three live in one corpus, but they do not share an
epistemic role. Citing a slide proves **alignment**, not truth. The distinction is the whole
reason ``cite`` stamps ``proves`` on every citation and ``conflicts`` labels two readings
exam-mode vs truth-mode instead of silently picking one.
"""

from __future__ import annotations

from typing import Any

ALIGNMENT = "alignment"
AUTHORITY = "authority"
LEARNER = "learner"

ROLES: tuple[str, ...] = (ALIGNMENT, AUTHORITY, LEARNER)

KINDS: tuple[str, ...] = ("pdf", "docx", "pptx", "md", "txt", "audio", "youtube", "url")

#: What a citation from each role actually establishes. Stamped on every citation.
PROVES: dict[str, str] = {
    ALIGNMENT: "alignment",
    AUTHORITY: "correctness",
    LEARNER: "learner_evidence",
}

#: The mode label used when two roles disagree about the same topic.
MODE: dict[str, str] = {
    ALIGNMENT: "exam-mode",
    AUTHORITY: "truth-mode",
    LEARNER: "learner-mode",
}

ROLE_RULES: dict[str, dict[str, Any]] = {
    ALIGNMENT: {
        "role": ALIGNMENT,
        "examples": ["slides", "syllabus", "past exams", "problem sets", "the course handout"],
        "constrains": "scope and notation",
        "proves": PROVES[ALIGNMENT],
        "mode": MODE[ALIGNMENT],
        "rule": (
            "Alignment sources decide what is in scope and which notation to use. "
            "They do not establish that a claim is true."
        ),
    },
    AUTHORITY: {
        "role": AUTHORITY,
        "examples": ["textbook", "official docs", "standards", "peer-reviewed papers"],
        "constrains": "correctness",
        "proves": PROVES[AUTHORITY],
        "mode": MODE[AUTHORITY],
        "rule": (
            "Authority sources support correctness. They do not decide what the exam covers "
            "or which notation the course uses."
        ),
    },
    LEARNER: {
        "role": LEARNER,
        "examples": ["your notes", "your solutions", "your code", "your summaries"],
        "constrains": "nothing about the material",
        "proves": PROVES[LEARNER],
        "mode": MODE[LEARNER],
        "rule": (
            "Learner sources are evidence about the learner, not about the subject. They are "
            "never cited as proof of a claim and never used to settle a conflict."
        ),
    },
}

#: Roles whose disagreement is worth surfacing as exam-mode vs truth-mode.
CONFLICT_PAIR: tuple[str, str] = (ALIGNMENT, AUTHORITY)


def check_role(role: str) -> str:
    if role not in ROLES:
        raise ValueError(f"unknown source role {role!r}; expected one of {', '.join(ROLES)}")
    return role


def check_kind(kind: str) -> str:
    if kind not in KINDS:
        raise ValueError(f"unknown source kind {kind!r}; expected one of {', '.join(KINDS)}")
    return kind


def proves(role: str) -> str:
    """What a citation from this role establishes. Stamped on every citation."""

    return PROVES.get(role, "unknown")


def mode(role: str) -> str:
    return MODE.get(role, "unknown")


def can_prove_correctness(role: str) -> bool:
    return role == AUTHORITY


def describe() -> list[dict[str, Any]]:
    """The rules as data, for the ``/corpus/{goal}/sources`` response and the docs."""

    return [ROLE_RULES[role] for role in ROLES]
