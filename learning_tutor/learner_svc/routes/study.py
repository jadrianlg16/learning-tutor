"""Study-tool routes: goal update, the question bank, flashcards, tables.

CONTRACTS.md, *Study tools*. Every handler is a one-line call into ``learner/api.py``; the
rules (keys compared server-side, flips never evidence) live in ``learner/study.py``.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from ...learner import api
from ...learner.store import LearnerError, Store
from ..deps import IdempotencyHeader, get_store, key_of
from ..models import (
    BlindCheckIn,
    BlueprintIn,
    CardReviewIn,
    CardsIn,
    GoalUpdateIn,
    MockStartIn,
    MockSubmitIn,
    PracticeAnswerIn,
    StudyImportIn,
    TableCardsIn,
    TableIn,
)

router = APIRouter(prefix="/v1", tags=["study"])


@router.patch("/goals/{goal_id}")
def update_goal(
    goal_id: str,
    body: GoalUpdateIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    changes = body.model_dump(exclude={"idempotency_key"})
    return api.goal_update(store, goal_id, idempotency_key=key_of(body, idem), **changes)


@router.post("/study/{goal_id}/import")
def study_import(
    goal_id: str,
    body: StudyImportIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.study_import(
        store,
        goal_id,
        body.markdown,
        key_markdown=body.key_markdown,
        what=list(body.what) if body.what else None,
        source=body.source,
        author=body.author,
        node=body.node,
        create_nodes=body.create_nodes,
        dry_run=body.dry_run,
        pool=body.pool,
        idempotency_key=key_of(body, idem),
    )


@router.get("/study/{goal_id}")
def study_overview(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.study_overview(store, goal_id)


@router.get("/bank/{goal_id}/pending")
def bank_pending(
    goal_id: str, limit: int = 25, store: Store = Depends(get_store)
) -> dict[str, Any]:
    return api.bank_pending(store, goal_id, limit=limit)


@router.get("/bank/{goal_id}/review")
def bank_review(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.bank_review(store, goal_id)


@router.post("/items/{item_id}/blind-check")
def blind_check(
    item_id: str,
    body: BlindCheckIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.item_blind_check(
        store,
        item_id,
        answer=body.answer,
        by=body.by,
        ambiguous=body.ambiguous,
        notes=body.notes,
        idempotency_key=key_of(body, idem),
    )


@router.get("/practice/{goal_id}/next")
def practice_next(
    goal_id: str, n: int = 1, focus: str | None = None, store: Store = Depends(get_store)
) -> dict[str, Any]:
    return api.practice_next(store, goal_id, n=n, focus=focus)


@router.post("/practice/answer")
def practice_answer(
    body: PracticeAnswerIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.practice_answer(
        store,
        item_id=body.item_id,
        response=body.response,
        order=body.order,
        confidence=body.confidence,
        idk=body.idk,
        session_id=body.session_id,
        channel=body.channel,
        idempotency_key=key_of(body, idem),
    )


# --- exam prep: CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*
@router.get("/goals/{goal_id}/blueprint")
def goal_blueprint(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.goal_blueprint(store, goal_id)


@router.put("/goals/{goal_id}/blueprint")
def goal_blueprint_set(
    goal_id: str,
    body: BlueprintIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    spec = body.model_dump(exclude={"idempotency_key", "author"})
    return api.goal_blueprint_set(
        store, goal_id, spec, author=body.author, idempotency_key=key_of(body, idem)
    )


@router.get("/progress/{goal_id}")
def progress(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.progress(store, goal_id)


@router.get("/mocks/{goal_id}")
def mock_list(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.mock_list(store, goal_id)


@router.post("/mocks/{goal_id}")
def mock_start(
    goal_id: str,
    body: MockStartIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.mock_start(
        store, goal_id, n=body.n, minutes=body.minutes, channel=body.channel,
        idempotency_key=key_of(body, idem),
    )


@router.get("/mock/{session_id}")
def mock_show(session_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.mock_show(store, session_id)


@router.post("/mock/{session_id}/submit")
def mock_submit(
    session_id: str,
    body: MockSubmitIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    answers = [a.model_dump() for a in body.answers]
    return api.mock_submit(store, session_id, answers, idempotency_key=key_of(body, idem))


@router.post("/cards/{goal_id}")
def cards_add(
    goal_id: str,
    body: CardsIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.cards_add(
        store,
        goal_id,
        body.cards,
        author=body.author,
        source=body.source,
        idempotency_key=key_of(body, idem),
    )


@router.get("/cards/{goal_id}/next")
def cards_next(goal_id: str, n: int = 1, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.cards_next(store, goal_id, n=n)


@router.post("/cards/{item_id}/reveal")
def card_reveal(item_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.card_reveal(store, item_id)


@router.post("/cards/{item_id}/review")
def card_review(
    item_id: str,
    body: CardReviewIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.card_review(
        store,
        item_id,
        rating=body.rating,
        session_id=body.session_id,
        channel=body.channel,
        idempotency_key=key_of(body, idem),
    )


@router.get("/cards/{goal_id}/export")
def cards_export(
    goal_id: str,
    format: Literal["tsv", "csv"] = "tsv",
    include: Literal["cards", "questions", "all"] = "cards",
    store: Store = Depends(get_store),
) -> PlainTextResponse:
    result = api.cards_export(store, goal_id, fmt=format, include=include)
    media = "text/tab-separated-values" if format == "tsv" else "text/csv"
    return PlainTextResponse(
        result["text"],
        media_type=f"{media}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{goal_id}-{include}.{format}"',
            "X-Card-Count": str(result["count"]),
        },
    )


@router.get("/tables/{goal_id}")
def tables_list(goal_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    return api.tables_list(store, goal_id)


@router.post("/tables/{goal_id}")
def table_save(
    goal_id: str,
    body: TableIn,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return api.table_save(
        store,
        goal_id,
        title=body.title,
        columns=body.columns,
        rows=body.rows,
        node=body.node,
        source=body.source,
        author=body.author,
        idempotency_key=key_of(body, idem),
    )


@router.get("/tables/{goal_id}/{table_id}")
def table_get(goal_id: str, table_id: str, store: Store = Depends(get_store)) -> dict[str, Any]:
    table = api.table_get(store, table_id)
    if table["goal_id"] != goal_id:
        api.goal_get(store, goal_id)  # 404 for an unknown goal first
        raise LearnerError(f"unknown table {table_id!r}")
    return table


@router.post("/tables/{goal_id}/{table_id}/cards")
def table_cards(
    goal_id: str,
    table_id: str,
    body: TableCardsIn | None = None,
    store: Store = Depends(get_store),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    table_get(goal_id, table_id, store)
    body = body or TableCardsIn()
    return api.table_cards(
        store, table_id, author=body.author, idempotency_key=key_of(body, idem)
    )
