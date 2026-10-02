"""Pydantic models shared by the CLI now and by HTTP + MCP at Stage 1.

These are the wire shapes. The SQLite rows are the storage shapes; the mapping is
one-to-one except where JSON columns expand into lists/dicts here.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Depth = Literal["recognize", "explain", "apply", "analyze"]
EdgeType = Literal[
    "strict_prerequisite",
    "recommended_background",
    "course_sequence",
    "co_requisite",
    "supports",
    "misconception_for",
    "transfer_related",
]
Provenance = Literal["course", "reference", "model", "learner_evidence", "human"]
ItemStatus = Literal["TEACHING_ONLY", "PRACTICE_EVIDENCE", "MASTERY_ELIGIBLE"]
EventKind = Literal[
    "answer",
    "teach_back",
    "probe_answer",
    "dispute",
    "graph_revision",
    "session_start",
    "session_end",
    "misconception_step",
    "note",
    "card_review",
]
Channel = Literal["claude-code", "agent", "telegram", "web"]
Context = Literal["in-session", "delayed", "transfer", "probe"]
NodeStateName = Literal["unknown", "fragile", "known", "misconception"]
Uncertainty = Literal["low", "medium", "high"]
MisconceptionState = Literal["suspected", "active", "weakened", "resolved", "recurred"]
MisconceptionStep = Literal["reasoning", "prediction", "counterexample"]
DisputeType = Literal[
    "I already know this",
    "ambiguous question",
    "misclick",
    "not on my exam",
    "this edge is wrong",
    "test me instead",
]
SelectionMode = Literal["probe", "review", "teach"]
#: How an answer was judged (Tutor MCP audit §5, CONTRACTS.md "Adopted from the audit").
#: Only ``blind_solver``, ``rubric`` and ``human`` are trusted evidence; ``host_llm`` is the
#: model grading its own learner and never counts toward ``known``.
#: ``self_report`` (study tools, 2026-09-24) is the learner rating their own flashcard recall:
#: it schedules the card and is never evidence.
EvaluationMethod = Literal["host_llm", "blind_solver", "rubric", "human", "self_report"]
TRUSTED_EVALUATION_METHODS: tuple[str, ...] = ("blind_solver", "rubric", "human")
#: Which sources win when they disagree: the syllabus, or the authoritative reference.
SourcePriority = Literal["alignment", "authority"]

#: Defaults for the version tags every evidence event carries (CONTRACTS.md, CLI section,
#: "Additions decided 2026-09-05").
DEFAULT_PROMPT_VERSION = "teach/v1"
DEFAULT_GRADER_VERSION = "teach-back-v1"


class Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Goal(Base):
    goal_id: str
    title: str
    depth: Depth = "explain"
    deadline: str | None = None
    minutes_per_session: int | None = None
    purpose: str | None = None
    assessment: str | None = None
    source_priority: SourcePriority | None = None
    contract: dict[str, Any] | None = None
    created_at: str | None = None
    sessions_per_week: int | None = None


class Node(Base):
    node_id: str
    title: str
    slug: str
    domain: str | None = None
    aliases: list[str] = Field(default_factory=list)
    goals: list[str] = Field(default_factory=list)
    graph_version: int = 0
    retired: bool = False


class Edge(Base):
    edge_id: str
    from_node: str
    to_node: str
    type: EdgeType = "strict_prerequisite"
    provenance: Provenance = "model"
    graph_version: int = 0
    retired: bool = False


class ItemVersion(Base):
    item_version_id: str
    item_id: str
    version: int
    stem: str
    options: list[str] = Field(default_factory=list)
    answer: str
    distractor_misconceptions: dict[str, str] = Field(default_factory=dict)
    kind: str = "mc"
    components: list[str] = Field(default_factory=list)
    surface_form: str | None = None
    author: str
    created_at: str
    #: why the key is right — shown only after an attempt (migration 3)
    explanation: str | None = None
    #: where an imported question came from (migration 3)
    source: str | None = None


class Item(Base):
    item_id: str
    node_id: str
    status: ItemStatus = "TEACHING_ONLY"
    current_version_id: str | None = None
    author: str
    created_at: str
    holdout: bool = False
    retired: bool = False
    version: ItemVersion | None = None


class Event(Base):
    event_id: str
    ts: str
    session_id: str | None = None
    goal_id: str | None = None
    node_id: str | None = None
    item_version_id: str | None = None
    kind: EventKind
    response: str | None = None
    correct: int | None = None
    confidence: int | None = None
    idk: int = 0
    assistance_level: int = 0
    channel: Channel = "claude-code"
    context: Context = "in-session"
    prompt_version: str | None = None
    grader_version: str | None = None
    evaluation_method: EvaluationMethod | None = None
    payload: dict[str, Any] | None = None


class Misconception(Base):
    misconception_id: str
    node_id: str
    claim: str
    state: MisconceptionState
    steps: list[dict[str, str]] = Field(default_factory=list)
    resolution_reason: str | None = None
    created_at: str
    updated_at: str


class Dispute(Base):
    dispute_id: str
    type: DisputeType
    node_id: str | None = None
    item_id: str | None = None
    note: str | None = None
    status: Literal["open", "settled"] = "open"
    outcome: Literal["upheld", "rejected"] | None = None
    evidence: str | None = None
    check_items: list[str] = Field(default_factory=list)
    created_at: str
    settled_at: str | None = None


class Session(Base):
    session_id: str
    goal_id: str | None = None
    channel: Channel = "claude-code"
    started_at: str
    ended_at: str | None = None
    summary: str | None = None


class NodeState(Base):
    node_id: str
    title: str
    state: NodeStateName = "unknown"
    independent_passes: int = 0
    assisted_passes: int = 0
    unearned_passes: int = 0  # passes at assistance >= 5; recorded, never counted
    self_graded_passes: int = 0  # host_llm-judged passes; recorded, never counted
    fails: int = 0
    last_delayed: Literal["pass", "fail"] | None = None
    transfer_passes: int = 0
    uncertainty: Uncertainty = "high"
    active_misconception: str | None = None
    due_items: int = 0
    reasons: list[str] = Field(default_factory=list)


class Pick(Base):
    """One selection returned by ``learner next`` / ``holdout-check``."""

    mode: SelectionMode | Literal["holdout"]
    reason: str
    node_id: str | None = None
    node_title: str | None = None
    item_id: str | None = None
    item_version_id: str | None = None
    stem: str | None = None
    options: list[str] = Field(default_factory=list)
    context: Context = "in-session"


class Summary(Base):
    goal_id: str
    generated_at: str
    goal_line: str
    known: list[str] = Field(default_factory=list)
    fragile: list[dict[str, Any]] = Field(default_factory=list)
    unknown: list[str] = Field(default_factory=list)
    #: nodes whose only passes were judged by the tutoring model itself
    self_graded: list[dict[str, Any]] = Field(default_factory=list)
    misconceptions: list[dict[str, Any]] = Field(default_factory=list)
    due_today: int = 0
    prefs: list[str] = Field(default_factory=list)
    last_session: dict[str, Any] | None = None
    collapsed: list[str] = Field(default_factory=list)
    feasibility: dict[str, Any] | None = None


class Metrics(Base):
    goal_id: str
    generated_at: str
    holdout_success_7d: dict[str, Any]
    false_mastery: dict[str, Any]
    item_rejection: dict[str, Any]
