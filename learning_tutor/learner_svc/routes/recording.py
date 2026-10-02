"""Recording routes: sessions, evidence events, misconceptions, disputes.

``POST /v1/events`` carries both evidence kinds, discriminated on ``kind`` — the body
mirrors the CLI flags of ``record answer`` and ``record teach-back`` exactly.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from pydantic import Field

from ...learner import api
from ...learner.store import Store
from ..deps import IdempotencyHeader, get_store, key_of
from ..models import (
    AnswerIn,
    DisputeIn,
    DisputeSettleIn,
    MisconceptionResolveIn,
    MisconceptionStepIn,
    MisconceptionSuspectIn,
    SessionEndIn,
    SessionIn,
    SessionLogIn,
    TeachBackIn,
)

router = APIRouter(prefix="/v1", tags=["recording"])

EventIn = Annotated[AnswerIn | TeachBackIn, Field(discriminator="kind")]


@router.post("/sessions")
def start_session(
    body: SessionIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.session_start(
        store,
        goal_id=body.goal_id,
        channel=body.channel,
        idempotency_key=key_of(body, idem),
    )


@router.get("/sessions/{session_id}")
def read_session(session_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.session_get(store, session_id)


@router.post("/sessions/{session_id}/end")
def end_session(
    session_id: str,
    body: SessionEndIn | None = None,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.session_end(
        store,
        session_id,
        body.summary if body else None,
        idempotency_key=key_of(body, idem),
    )


@router.post("/sessions/{session_id}/log")
def log_session(
    session_id: str,
    body: SessionLogIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """Write the session md-log into the vault — the CLI's ``learner log``, by value.

    The file lands where ``learner log --file`` would put it, and the same ``note`` event
    is appended, so the vault and the event table cannot tell the two callers apart.
    """

    result = api.log_session(
        store,
        session_id,
        markdown=body.markdown,
        filename=body.filename,
        idempotency_key=key_of(body, idem),
    )
    # `log` is what the CLI and MCP return; `log_path` is the name the gateway contract
    # uses for the same string. Both, rather than a rename that would split the surfaces.
    return {**result, "log_path": result["log"]}


@router.post("/events")
def record_event(
    body: Annotated[EventIn, Body()],
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    if isinstance(body, TeachBackIn):
        return api.record_teach_back(
            store,
            session_id=body.session_id,
            node=body.node,
            score=body.score,
            rubric_version=body.rubric_version,
            assistance_level=body.assistance_level,
            notes=body.notes,
            context=body.context,
            prompt_version=body.prompt_version,
            grader_version=body.grader_version,
            idempotency_key=key_of(body, idem),
        )
    return api.record_answer(
        store,
        session_id=body.session_id,
        item_id=body.item_id,
        response=body.response,
        correct=body.correct,
        confidence=body.confidence,
        idk=body.idk,
        assistance_level=body.assistance_level,
        context=body.context,
        channel=body.channel,
        prompt_version=body.prompt_version,
        grader_version=body.grader_version,
        evaluation_method=body.evaluation_method,
        idempotency_key=key_of(body, idem),
    )


@router.post("/misconceptions/suspect")
def suspect_misconception(
    body: MisconceptionSuspectIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.misconception_suspect(
        store,
        node=body.node,
        claim=body.claim,
        session_id=body.session_id,
        prompt_version=body.prompt_version,
        grader_version=body.grader_version,
        idempotency_key=key_of(body, idem),
    )


@router.post("/misconceptions/confirm-step")
def confirm_misconception_step(
    body: MisconceptionStepIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.misconception_confirm_step(
        store,
        node=body.node,
        claim=body.claim,
        step=body.step,
        outcome=body.outcome,
        notes=body.notes,
        prompt_version=body.prompt_version,
        grader_version=body.grader_version,
        idempotency_key=key_of(body, idem),
    )


@router.post("/misconceptions/resolve")
def resolve_misconception(
    body: MisconceptionResolveIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.misconception_resolve(
        store,
        node=body.node,
        claim=body.claim,
        notes=body.notes,
        prompt_version=body.prompt_version,
        grader_version=body.grader_version,
        idempotency_key=key_of(body, idem),
    )


@router.post("/disputes")
def open_dispute(
    body: DisputeIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.dispute_open(
        store,
        dispute_type=body.type,
        node=body.node,
        item_id=body.item_id,
        note=body.note,
        session_id=body.session_id,
        idempotency_key=key_of(body, idem),
    )


@router.post("/disputes/{dispute_id}/settle")
def settle_dispute(
    dispute_id: str,
    body: DisputeSettleIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.dispute_settle(
        store,
        dispute_id,
        outcome=body.outcome,
        evidence=body.evidence,
        idempotency_key=key_of(body, idem),
    )
