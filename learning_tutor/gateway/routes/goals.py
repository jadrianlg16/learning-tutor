"""Goals: the goal contract in, the three read shapes out."""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends

from ...tutor import orchestrator as orch
from ..deps import IdempotencyHeader, Services, get_services
from ..errors import GatewayError
from ..models import GoalContract, GoalUpdate
from ._common import counts, graph_of, iso_now, learner_goal

router = APIRouter(prefix="/api", tags=["goals"])

_SLUG = re.compile(r"[^a-z0-9]+")

#: The GoalContract fields learner-svc has no column for. They live in the gateway's own
#: state file and are merged back onto every goal read, so a caller sees one object.
CONTRACT_ONLY = ("concept", "sessions_per_week", "target_capabilities", "transfer_required",
                 "domain")


def slugify(title: str) -> str:
    slug = _SLUG.sub("-", (title or "").strip().lower()).strip("-")
    return slug[:48] or "goal"


@router.post("/goals")
def create_goal(
    body: GoalContract,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal_id = body.goal_id or slugify(body.title)
    services.learner.post(
        "/v1/goals",
        {
            "goal_id": goal_id,
            "title": body.title,
            "depth": body.depth,
            "deadline": body.deadline,
            "minutes_per_session": body.minutes_per_session,
            "purpose": body.purpose or None,
            "assessment": body.assessment,
            "source_priority": body.source_priority,
        },
        idempotency_key=idem,
    )

    goal_state = services.state.get(goal_id)
    goal_state.contract = {
        key: getattr(body, key) for key in CONTRACT_ONLY if getattr(body, key) is not None
    }
    goal_state.phase = "grounding"
    services.state.touch()

    sources_dir = services.settings.sources_dir / goal_id
    sources_dir.mkdir(parents=True, exist_ok=True)

    return {
        "goal": learner_goal(services, goal_id),
        "sources_dir": str(sources_dir),
        "phase": "grounding",
    }


@router.get("/goals")
def list_goals(services: Services = Depends(get_services)) -> dict[str, Any]:
    listing = services.learner.get("/v1/goals")
    out: list[dict[str, Any]] = []
    for row in (listing or {}).get("goals") or []:
        goal_id = str(row.get("goal_id"))
        try:
            graph = graph_of(services, goal_id)
        except GatewayError:
            graph = {"nodes": [], "edges": []}
        tally = counts(graph)
        out.append(
            {
                **row,
                **services.state.get(goal_id).contract,
                "phase": services.state.phase(goal_id),
                "node_count": len(graph.get("nodes") or []),
                **tally,
            }
        )
    return {"goals": out}


@router.get("/goals/{goal_id}")
def read_goal(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)

    session: dict[str, Any] | None = None
    session_id = goal_state.session.session_id
    if session_id:
        try:
            row = services.learner.get(f"/v1/sessions/{session_id}")
            session = row if isinstance(row, dict) and not row.get("ended_at") else None
        except GatewayError:
            session = None

    try:
        summary_md = services.learner.text(f"/v1/summary/{goal_id}", {"format": "md"})
    except GatewayError as exc:
        summary_md = f"(summary unavailable: {exc.message})"

    return {
        "goal": goal,
        "phase": goal_state.phase,
        "session": session,
        "summary_md": summary_md,
    }


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: str,
    body: GoalUpdate,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """A moved deadline or a new cadence (CONTRACTS.md, *Study tools*).

    learner-svc owns the row (``sessions_per_week`` has a column since migration 3); the
    gateway mirrors ``sessions_per_week`` into its contract, which ``learner_goal`` merges
    over the row, and recomputes the stored plan's feasibility so ``state.json`` does not
    keep yesterday's verdict.
    """

    learner_goal(services, goal_id)
    changes = body.model_dump(exclude_none=True)
    result = (
        services.learner.patch(f"/v1/goals/{goal_id}", changes, idempotency_key=idem)
        if changes
        else {"changed": {}}
    )
    goal_state = services.state.get(goal_id)
    if body.sessions_per_week is not None:
        goal_state.contract["sessions_per_week"] = body.sessions_per_week
    goal = learner_goal(services, goal_id)
    graph = graph_of(services, goal_id)
    feasibility = None
    if graph.get("nodes"):
        computed = orch.feasibility(
            goal, graph, today=iso_now(), sessions_per_week=goal.get("sessions_per_week")
        )
        feasibility = computed.as_dict() if computed.sessions_needed is not None else None
    if goal_state.plan:
        goal_state.plan["feasibility"] = feasibility
    services.state.touch()
    return {"goal": goal, "changed": (result or {}).get("changed", {}), "feasibility": feasibility}


@router.get("/goals/{goal_id}/metrics")
def metrics(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    """Straight passthrough, as the contract says."""

    return services.learner.get(f"/v1/metrics/{goal_id}")
