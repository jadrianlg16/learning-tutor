"""Request bodies for `learner-svc`.

Every field mirrors a CLI flag, and every enum is imported from
:mod:`learning_tutor.learner.models` rather than re-spelled here — the CLI, HTTP and MCP
surfaces share one definition of what a channel, a context or an evaluation method is.

Response bodies are whatever `learner/api.py` returns (plain JSON dicts built from the
learner models), so HTTP cannot drift from the CLI by construction.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..learner.models import (
    DEFAULT_GRADER_VERSION,
    DEFAULT_PROMPT_VERSION,
    Channel,
    Context,
    Depth,
    EvaluationMethod,
    SourcePriority,
)


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")

    #: Same key + same body replays the first response; same key + a different body is 409.
    idempotency_key: str | None = None


class GoalIn(Body):
    goal_id: str
    title: str
    depth: Depth = "explain"
    deadline: str | None = None
    minutes_per_session: int | None = None
    purpose: str | None = None
    assessment: str | None = None
    source_priority: SourcePriority | None = None


class GraphImportIn(Body):
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class GraphReviseIn(Body):
    ops: list[dict[str, Any]] = Field(default_factory=list)


class ItemIn(Body):
    node: str
    spec: dict[str, Any]
    author: str = "model"
    item: str | None = None


class ItemValidateIn(Body):
    by: str
    result: Literal["pass", "fail"]
    notes: str | None = None
    evaluation_method: EvaluationMethod | None = None


class ItemPromoteIn(Body):
    pass


class SessionIn(Body):
    goal_id: str
    channel: Channel = "claude-code"


class SessionEndIn(Body):
    summary: str | None = None


class SessionLogIn(Body):
    """The md-log as text. The CLI's ``learner log --file`` passes a path; a caller on
    another filesystem cannot, so HTTP takes the markdown itself."""

    markdown: str
    #: one path segment ending in ``.md``; omitted → ``<session date>-<goal>.md``
    filename: str | None = None


class AnswerIn(Body):
    kind: Literal["answer"] = "answer"
    session_id: str | None = None
    item_id: str
    response: str | None = None
    correct: bool
    confidence: int | None = None
    idk: bool = False
    assistance_level: int = 0
    context: Context = "in-session"
    channel: Channel | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION
    grader_version: str = DEFAULT_GRADER_VERSION
    evaluation_method: EvaluationMethod = "host_llm"


class TeachBackIn(Body):
    kind: Literal["teach_back"]
    session_id: str | None = None
    node: str
    score: int
    rubric_version: str
    assistance_level: int = 0
    notes: str | None = None
    context: Context = "in-session"
    prompt_version: str = DEFAULT_PROMPT_VERSION
    #: defaults to ``rubric_version``: the rubric is the grader
    grader_version: str | None = None


class MisconceptionSuspectIn(Body):
    node: str
    claim: str
    session_id: str | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION
    grader_version: str = DEFAULT_GRADER_VERSION


class MisconceptionStepIn(Body):
    node: str
    claim: str
    step: Literal["reasoning", "prediction", "counterexample"]
    outcome: Literal["held", "dropped"]
    notes: str | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION
    grader_version: str = DEFAULT_GRADER_VERSION


class MisconceptionResolveIn(Body):
    node: str
    claim: str
    notes: str | None = None
    prompt_version: str = DEFAULT_PROMPT_VERSION
    grader_version: str = DEFAULT_GRADER_VERSION


class DisputeIn(Body):
    type: str
    node: str | None = None
    item_id: str | None = None
    note: str | None = None
    session_id: str | None = None


class DisputeSettleIn(Body):
    outcome: Literal["upheld", "rejected"]
    evidence: str | None = None


class Health(BaseModel):
    status: Literal["ok"] = "ok"
    service: str = "learner-svc"
    schema_version: int
    data_dir: str
    goals: int


class Error(BaseModel):
    error: str


# --------------------------------------------------------------------------- study tools
# CONTRACTS.md, *Study tools* (2026-09-24). Same rule as above: every field mirrors a flag.
class GoalUpdateIn(Body):
    """Absent (or null) = unchanged. ``deadline: ""`` clears the deadline."""

    title: str | None = None
    depth: Depth | None = None
    deadline: str | None = None
    minutes_per_session: int | None = None
    sessions_per_week: int | None = None
    purpose: str | None = None
    assessment: str | None = None
    source_priority: SourcePriority | None = None


StudyKind = Literal["questions", "cards", "tables"]


class StudyImportIn(Body):
    markdown: str
    key_markdown: str | None = None
    what: list[StudyKind] | None = None
    source: str | None = None
    author: str = "import"
    #: where untagged blocks go; tagged ones follow their source's own numbering
    node: str | None = None
    create_nodes: bool = True
    dry_run: bool = False
    #: ``mock`` seals the questions for mock exams (questions only)
    pool: Literal["practice", "mock"] = "practice"


class BlindCheckIn(Body):
    #: the solver's pick, as an option key or the option's text
    answer: str | None = None
    by: str
    ambiguous: bool = False
    notes: str | None = None


class PracticeAnswerIn(Body):
    item_id: str
    response: str | None = None
    #: the option order the question was served in (``order`` from practice/next)
    order: list[int] | None = None
    confidence: int | None = None
    idk: bool = False
    session_id: str | None = None
    channel: Channel | None = None


class BlueprintIn(Body):
    """CONTRACTS.md, *Exam blueprint*: ``{exam, source, areas: [{code, title, subareas:
    [{ref, title, items}]}]}``. Replaces the goal's blueprint."""

    exam: str | None = None
    source: str | None = None
    areas: list[dict[str, Any]]
    author: str = "cli"


class MockStartIn(Body):
    n: int | None = None
    minutes: int | None = None
    channel: Channel = "web"


class MockAnswerIn(BaseModel):
    item_id: str
    #: a letter of the order the mock showed, or the option's text; null = left blank
    response: str | None = None
    order: list[int] | None = None
    confidence: int | None = None


class MockSubmitIn(Body):
    answers: list[MockAnswerIn] = []


class CardsIn(Body):
    cards: list[dict[str, Any]]
    author: str = "model"
    source: str | None = None


class CardReviewIn(Body):
    rating: Literal["again", "hard", "good", "easy"]
    session_id: str | None = None
    channel: Channel | None = None


class TableIn(Body):
    title: str
    columns: list[str]
    rows: list[list[str]]
    node: str | None = None
    source: str | None = None
    author: str = "model"


class TableCardsIn(Body):
    author: str = "table"
