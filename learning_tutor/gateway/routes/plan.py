"""Plan, map and receipts.

The plan phase is the one place the model gets to propose durable structure, and it is
fenced on both sides: the corpus structure goes in as a prior, and every node claim comes
back out through ``cite_or_abstain``. The learner's manual review
(``POST /api/goals/{g}/plan/approve``) is required before the probe — plan.md: "Do not
start the probe on an unreviewed graph."
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...tutor import orchestrator as orch
from ...tutor.generate import generate_plan, verify_plan
from ..deps import IdempotencyHeader, Services, get_services
from ..errors import bad_request, not_found
from ..models import PlanApproveIn
from ._common import (
    corpus_for,
    graph_of,
    iso_now,
    learner_goal,
    node_by_id,
    node_states,
    state_names,
)

router = APIRouter(prefix="/api", tags=["plan"])


@router.post("/goals/{goal_id}/plan")
def make_plan(
    goal_id: str,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    extras = services.state.get(goal_id).contract

    context, structure, corpus_exists = corpus_for(services, goal_id)
    plan, _meta = generate_plan(
        services.pack,
        goal=goal,
        extras=extras,
        corpus_context=context,
        structure=structure,
    )
    if not plan.nodes:
        raise bad_request("the model returned no nodes for this goal", code="empty_plan")

    payload, dropped_edges = plan.payload()
    services.learner.post(f"/v1/graph/{goal_id}/import", payload, idempotency_key=idem)
    graph = graph_of(services, goal_id)
    verification = verify_plan(
        graph.get("nodes") or [], goal_id=goal_id, settings=services.settings
    )
    course_prior_used = bool(structure) or any(
        (edge.get("provenance") == "course") for edge in graph.get("edges") or []
    )

    # Optional in the contract, and honest about when it has nothing to say: without a
    # session length there is no arithmetic, only a wish, and `null` lets web-ui fall back
    # to its own estimate (src/lib/feasibility.ts) instead of rendering empty numbers.
    computed = orch.feasibility(
        goal,
        graph,
        today=iso_now(),
        sessions_per_week=extras.get("sessions_per_week"),
    )
    feasibility = computed.as_dict() if computed.sessions_needed is not None else None

    goal_state = services.state.get(goal_id)
    goal_state.phase = "plan"
    goal_state.plan_approved = False
    goal_state.plan = {
        "graph_version": graph.get("graph_version"),
        "course_prior_used": course_prior_used,
        "corpus_exists": corpus_exists,
        "dropped_edges": dropped_edges,
        "verification": verification,
        "feasibility": feasibility,
    }
    services.state.touch()

    response = {
        "graph_version": graph.get("graph_version"),
        "mermaid": services.learner.get(f"/v1/graph/{goal_id}", {"format": "mermaid"}).get(
            "mermaid", ""
        ),
        "nodes": graph.get("nodes") or [],
        "edges": graph.get("edges") or [],
        "course_prior_used": course_prior_used,
        "verification": verification,
        "feasibility": feasibility,
        "dropped_edges": dropped_edges,
        "review_questions": [
            "Anything here you already know cold?",
            "Anything missing that your course or exam covers?",
            "Any arrow wrong — does A really need B first?",
            "Anything here not on your exam, or not needed for your purpose?",
        ],
    }
    # Optional in the contract, and present only when there is something to say: a plan
    # too short to be a curriculum after a retry (see tutor/generate.py::generate_plan).
    if plan.warnings:
        response["warnings"] = list(plan.warnings)
    return response


@router.post("/goals/{goal_id}/plan/approve")
def approve_plan(
    goal_id: str,
    body: PlanApproveIn | None = None,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    learner_goal(services, goal_id)
    ops = (body.ops if body else None) or []
    if ops:
        services.learner.post(
            f"/v1/graph/{goal_id}/revise", {"ops": ops}, idempotency_key=idem
        )
    graph = graph_of(services, goal_id)

    goal_state = services.state.get(goal_id)
    goal_state.plan_approved = True
    goal_state.plan["graph_version"] = graph.get("graph_version")
    goal_state.phase = "probe"
    services.state.touch()
    return {"graph_version": graph.get("graph_version")}


@router.get("/goals/{goal_id}/map")
def read_map(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    """Two graphs, per IDEA.md: the curriculum map, and the learner's path through it."""

    learner_goal(services, goal_id)
    graph = graph_of(services, goal_id)
    nodes = graph.get("nodes") or []
    edges = graph.get("edges") or []
    names = state_names(graph)
    order = orch.learning_path(nodes, edges, names)
    curriculum_mermaid = services.learner.get(f"/v1/graph/{goal_id}", {"format": "mermaid"}).get(
        "mermaid", ""
    )
    return {
        "curriculum": {"mermaid": curriculum_mermaid, "nodes": nodes, "edges": edges},
        "path": {"mermaid": orch.path_mermaid(nodes, order, names), "order": order},
        "states": node_states(graph),
    }


@router.get("/goals/{goal_id}/receipts/{node_id}")
def receipts(
    goal_id: str, node_id: str, services: Services = Depends(get_services)
) -> dict[str, Any]:
    """"Why is this fragile?" — the evidence behind one node's colour.

    Every part of it is learner-svc's: the node and its state from ``GET /v1/graph``, the
    rows from ``GET /v1/events``, ``/v1/misconceptions`` and ``/v1/disputes``. ``why`` is
    learner-svc's own ``reasons`` list — the rules that produced the state, not a
    paraphrase of them — and nothing here is recomputed.
    """

    learner_goal(services, goal_id)
    graph = graph_of(services, goal_id)
    node = node_by_id(graph, node_id)
    if node is None:
        raise not_found(f"unknown node {node_id!r} in goal {goal_id!r}")
    state = dict(node.get("state") or {})

    learner = services.learner
    return {
        "node": {k: v for k, v in node.items() if k != "state"},
        "state": state,
        "evidence": learner.get("/v1/events", {"node": node_id}).get("events") or [],
        "why": list(state.get("reasons") or []),
        "misconceptions": learner.get("/v1/misconceptions", {"node": node_id}).get(
            "misconceptions"
        )
        or [],
        "disputes": learner.get("/v1/disputes", {"node": node_id}).get("disputes") or [],
        "evidence_source": "learner-svc GET /v1/events",
    }
