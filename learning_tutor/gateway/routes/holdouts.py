"""Holdouts: the due list and the answer — the product's door to the delayed/transfer metric.

A holdout is a ``MASTERY_ELIGIBLE`` item learner-svc hides from ``next`` in every mode
(CONTRACTS.md: "holdouts are never served by ``next`` for teaching/review, only by
``holdout-check``"). The 7-day holdout success rate and the false-mastery rate in
``learner/metrics.py`` are computed on nothing else, so a product that never serves one
reports ``null`` forever. These two routes are ``learner holdout-check`` and
``learner record answer --context delayed|transfer`` over the gateway.

What this module deliberately does not do:

* touch the phase machine — a holdout check is legal in any phase past ``plan`` and
  changes none of them;
* run the hint ladder — a holdout is answered cold, at assistance 0, or not at all;
* generate anything — no feedback, no step, no model call. The check is the evidence.

The key is the gateway's, as everywhere: a holdout the gateway did not author (promoted
through the CLI or MCP) has no key here and is 409 ``unknown_item`` rather than a guess.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from ...tutor import orchestrator as orch
from ..deps import IdempotencyHeader, Services, get_services, scoped_key
from ._common import learner_goal, question_payload, recorded_event, require_question

router = APIRouter(prefix="/api", tags=["holdouts"])

HoldoutContext = Literal["delayed", "transfer"]


class HoldoutAnswerIn(BaseModel):
    """CONTRACTS.md ``POST /api/goals/{g}/holdouts/answer`` body."""

    model_config = ConfigDict(extra="forbid")

    item_id: str
    response: str | None = None
    context: HoldoutContext
    session_id: str | None = None
    confidence: int | None = None
    idk: bool = False


@router.get("/goals/{goal_id}/holdouts/due")
def holdouts_due(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    """learner-svc's ``GET /v1/holdouts/{g}/due``, each pick as the contract's ``Question``.

    The key never leaves the gateway, so a pick is rebuilt from the stored question when
    there is one; a holdout with no stored key is still listed (from learner-svc's stem
    and options) so the learner can see it is due, but answering it here is a 409.
    """

    learner_goal(services, goal_id)
    due = services.learner.get(f"/v1/holdouts/{goal_id}/due")
    due = due if isinstance(due, dict) else {}
    picks: list[dict[str, Any]] = []
    for pick in due.get("picks") or []:
        item_id = str(pick.get("item_id") or "")
        stored = services.state.question(goal_id, item_id)
        if stored is not None:
            shaped = question_payload(stored)
            shaped["node_title"] = shaped["node_title"] or str(pick.get("node_title") or "")
        else:
            shaped = {
                "item_id": item_id,
                "item_version_id": pick.get("item_version_id"),
                "node_id": pick.get("node_id"),
                "node_title": pick.get("node_title") or "",
                "stem": pick.get("stem"),
                "options": orch.options_for_wire(list(pick.get("options") or [])),
                "allow_idk": True,
                "ask_confidence": True,
                "kind": "concept",
                "assistance_level": 0,
            }
        shaped["context"] = pick.get("context") or "delayed"
        shaped["reason"] = pick.get("reason") or ""
        shaped["key_on_file"] = stored is not None
        picks.append(shaped)
    return {
        "goal_id": goal_id,
        "due": len(picks),
        "picks": picks,
        "note": str(due.get("note") or ""),
    }


@router.post("/goals/{goal_id}/holdouts/answer")
def holdouts_answer(
    goal_id: str,
    body: HoldoutAnswerIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """Grade against the stored key and record with the given ``context``. Nothing else."""

    learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    question = require_question(services, goal_id, body.item_id)

    grade = orch.grade_mc(body.response, question.answer, question.options)
    idk = bool(body.idk) or grade.idk
    channel = goal_state.session.channel or "web"

    recorded = services.learner.post(
        "/v1/events",
        {
            "kind": "answer",
            "session_id": body.session_id,
            "item_id": body.item_id,
            "response": body.response,
            "correct": grade.correct,
            "confidence": body.confidence,
            "idk": idk,
            "assistance_level": 0,
            "context": body.context,
            "channel": channel,
            "prompt_version": services.prompt_version,
            "grader_version": grade.grader_version,
            "evaluation_method": grade.evaluation_method,
        },
        idempotency_key=scoped_key(idem, "holdout"),
    )

    return {
        "correct": grade.correct,
        "context": body.context,
        "recorded": recorded_event(
            recorded,
            kind="answer",
            session_id=body.session_id,
            goal_id=goal_id,
            node_id=question.node_id,
            item_version_id=question.item_version_id,
            response=body.response,
            correct=grade.correct,
            confidence=body.confidence,
            idk=idk,
            assistance_level=0,
            context=body.context,
            channel=channel,
            prompt_version=services.prompt_version,
            grader_version=grade.grader_version,
        ),
        "node_state": recorded.get("node_state") or {},
        "counts_toward_mastery": bool(recorded.get("counts_toward_mastery")),
    }
