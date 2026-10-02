"""Authoring routes: goals, the concept graph, the item bank.

Every handler is a one-line call into ``learner/api.py``. If a rule belongs anywhere, it
belongs there — HTTP is a transport.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...learner import api
from ...learner.store import Store
from ..deps import IdempotencyHeader, get_store, key_of
from ..models import (
    GoalIn,
    GraphImportIn,
    GraphReviseIn,
    ItemIn,
    ItemPromoteIn,
    ItemValidateIn,
)

router = APIRouter(prefix="/v1", tags=["authoring"])


@router.post("/goals")
def create_goal(
    body: GoalIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.goal_add(
        store,
        goal_id=body.goal_id,
        title=body.title,
        depth=body.depth,
        deadline=body.deadline,
        minutes_per_session=body.minutes_per_session,
        purpose=body.purpose,
        assessment=body.assessment,
        source_priority=body.source_priority,
        idempotency_key=key_of(body, idem),
    )


@router.get("/goals")
def list_goals(store: Store = Depends(get_store)) -> dict[str, Any]:
    return {"goals": api.goal_list(store)}


@router.get("/goals/{goal_id}")
def read_goal(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.goal_get(store, goal_id)


@router.post("/graph/{goal_id}/import")
def import_graph(
    goal_id: str,
    body: GraphImportIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    payload = {"nodes": body.nodes, "edges": body.edges}
    return api.graph_import(store, goal_id, payload, idempotency_key=key_of(body, idem))


@router.post("/graph/{goal_id}/revise")
def revise_graph(
    goal_id: str,
    body: GraphReviseIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.graph_revise(store, goal_id, body.ops, idempotency_key=key_of(body, idem))


@router.get("/graph/{goal_id}")
def show_graph(
    goal_id: str, format: str = "json", store: Store = Depends(get_store)
) -> dict[str, Any]:
    result = api.graph_show(store, goal_id, format)
    if format == "mermaid":
        return {"goal_id": goal_id, "format": "mermaid", "mermaid": result}
    return result


@router.post("/items")
def create_item(
    body: ItemIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.item_add(
        store,
        body.node,
        body.spec,
        author=body.author,
        item=body.item,
        idempotency_key=key_of(body, idem),
    )


@router.post("/items/{item_id}/validate")
def validate_item(
    item_id: str,
    body: ItemValidateIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.item_validate(
        store,
        item_id,
        by=body.by,
        result=body.result,
        notes=body.notes,
        evaluation_method=body.evaluation_method,
        idempotency_key=key_of(body, idem),
    )


@router.post("/items/{item_id}/promote")
def promote_item(
    item_id: str,
    body: ItemPromoteIn | None = None,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.item_promote(store, item_id, idempotency_key=key_of(body, idem))
