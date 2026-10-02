"""Request bodies for the gateway. Response shapes are built in the routes.

Responses are deliberately plain dicts assembled to CONTRACTS.md's *Gateway response
shapes* rather than Pydantic response models: several of them are pass-throughs of
learner-svc's own JSON (``Event``, ``NodeState``, the goal row), and re-declaring those
here would create a second definition that can drift from the service that owns them.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Depth = Literal["recognize", "explain", "apply", "analyze"]
Domain = Literal["math-cs", "empirical", "procedural"]
SourcePriority = Literal["alignment", "authority"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalContract(Body):
    """CONTRACTS.md ``POST /api/goals`` body."""

    goal_id: str | None = None
    title: str
    concept: str = ""
    depth: Depth = "explain"
    purpose: str = ""
    deadline: str | None = None
    minutes_per_session: int = 45
    sessions_per_week: int | None = None
    assessment: str | None = None
    source_priority: SourcePriority | None = None
    target_capabilities: list[str] = Field(default_factory=list)
    transfer_required: bool = False
    domain: Domain = "math-cs"


class ResearchIn(Body):
    topic: str | None = None
    guidelines: str | None = None


class ResearchApproveIn(Body):
    proposal_id: str
    accept: list[str] = Field(default_factory=list)


class PlanApproveIn(Body):
    ops: list[dict[str, Any]] = Field(default_factory=list)


class ProbeStartIn(Body):
    channel: str = "web"


class ProbeAnswerIn(Body):
    session_id: str
    item_id: str
    response: str | None = None
    confidence: int | None = None
    idk: bool = False
    #: Accepted and ignored. The gateway grades against its own stored key; a client that
    #: sends this is not trusted, and tests/test_gateway_routes.py pins that.
    correct: bool | None = None


class TeachNextIn(Body):
    session_id: str


class TeachHintIn(Body):
    session_id: str
    item_id: str
    level: int = 1


class TeachInterruptIn(Body):
    """"Wait, why?" mid-step. ``node_id`` must be the node being taught right now."""

    session_id: str
    node_id: str
    question: str


class TeachAnswerIn(Body):
    session_id: str
    item_id: str
    response: str | None = None
    confidence: int | None = None
    idk: bool = False
    assistance_level: int = 0
    correct: bool | None = None  # ignored; see ProbeAnswerIn


class TeachBackIn(Body):
    session_id: str
    node_id: str
    explanation: str


class MisconceptionStepIn(Body):
    session_id: str | None = None
    node_id: str
    claim: str
    step: Literal["reasoning", "prediction", "counterexample"]
    learner_response: str = ""
    outcome: Literal["held", "dropped"] | None = None


class DisputeIn(Body):
    type: str
    node_id: str | None = None
    item_id: str | None = None
    note: str = ""
    session_id: str | None = None


class SessionEndIn(Body):
    session_id: str
    summary: str | None = None


class RenderIn(Body):
    mermaid: str
    theme: str = "default"


# --------------------------------------------------------------------------- study tools
# CONTRACTS.md, *Study tools* (2026-09-24).
class GoalUpdate(Body):
    """``PATCH /api/goals/{g}``. Absent = unchanged; ``deadline: ""`` clears it."""

    title: str | None = None
    depth: Depth | None = None
    deadline: str | None = None
    minutes_per_session: int | None = None
    sessions_per_week: int | None = None
    purpose: str | None = None
    assessment: str | None = None


class StudyImportPath(Body):
    """``POST /api/goals/{g}/study/import`` as JSON: a file already in the sources folder."""

    path: str
    key_path: str | None = None
    what: list[Literal["questions", "cards", "tables"]] | None = None
    node: str | None = None
    dry_run: bool = False


class PracticeAnswer(Body):
    item_id: str
    response: str | None = None
    #: the option order the question was served in (``order`` from practice/next)
    order: list[int] | None = None
    confidence: int | None = None
    idk: bool = False


class MockStart(Body):
    n: int | None = None
    minutes: int | None = None


class MockSubmit(Body):
    #: [{item_id, response: key|null, order?, confidence?}] — blanks may be left out
    answers: list[dict[str, Any]] = []


class CardRef(Body):
    item_id: str


class CardRating(Body):
    item_id: str
    rating: Literal["again", "hard", "good", "easy"]
