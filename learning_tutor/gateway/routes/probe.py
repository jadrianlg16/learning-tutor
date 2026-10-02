"""The probe: find the edge, cheaply, and never teach while doing it.

``learner next --mode probe`` picks the node (KST-style split scoring, in learner-svc);
this route writes an item on it, serves it, grades it against the stored key, and applies
probe.md's four stop rules. It does not rank nodes and it does not explain anything —
probe.md: "Teaching now contaminates the map you are building."
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...tutor import orchestrator as orch
from ...tutor.generate import author_item
from ..deps import IdempotencyHeader, Services, get_services, scoped_key
from ..errors import bad_request
from ..models import ProbeAnswerIn, ProbeStartIn
from ..state import Question
from ._common import (
    corpus_for,
    learner_goal,
    question_payload,
    recorded_event,
    require_question,
)

router = APIRouter(prefix="/api", tags=["probe"])


def _serve_next_probe_item(
    services: Services,
    goal: dict[str, Any],
    goal_id: str,
    session_id: str,
    *,
    idem: str | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    """Author and remember the next probe item. Returns ``(question_payload, next_meta)``."""

    picks = services.learner.get(
        "/v1/next", {"goal": goal_id, "mode": "probe", "n": 1, "session": session_id}
    )
    pick = orch.next_probe_question(picks)
    if pick is None:
        return None, picks

    goal_state = services.state.get(goal_id)
    context, _structure, _exists = corpus_for(
        services, goal_id, query=pick.get("node_title"), max_tokens=2500
    )
    authored = author_item(
        services.pack,
        services.learner,
        phase="probe",
        goal=goal,
        extras=goal_state.contract,
        node_id=str(pick.get("node_id")),
        node_title=str(pick.get("node_title") or ""),
        depth=str(goal.get("depth") or "explain"),
        corpus_context=context,
        avoid=[q.stem for q in goal_state.questions.values() if q.node_id == pick.get("node_id")],
        author="tutor",
        idempotency_key=scoped_key(idem, f"item:{pick.get('node_id')}"),
    )
    spec = authored["spec"]
    question = Question(
        item_id=str(authored["item_id"]),
        item_version_id=authored.get("item_version_id"),
        node_id=str(pick.get("node_id")),
        node_title=str(pick.get("node_title") or ""),
        stem=spec["stem"],
        options=list(spec["options"]),
        answer=str(spec["answer"]),
        kind=str(spec.get("kind") or "concept"),
        explanation=str(authored.get("explanation") or ""),
        distractor_misconceptions=dict(spec.get("distractor_misconceptions") or {}),
        context="probe",
        status=str(authored.get("status") or "TEACHING_ONLY"),
        ask_confidence=True,
    )
    services.state.remember(goal_id, question, session_id=session_id)
    return question_payload(question), picks


@router.post("/goals/{goal_id}/probe/start")
def probe_start(
    goal_id: str,
    body: ProbeStartIn | None = None,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    if not goal_state.plan_approved:
        raise bad_request(
            "the plan has not been reviewed: POST /api/goals/{g}/plan/approve first "
            "(plan.md — do not start the probe on an unreviewed graph)",
            code="plan_not_approved",
        )

    session = services.learner.post(
        "/v1/sessions",
        {"goal_id": goal_id, "channel": (body.channel if body else "web")},
        idempotency_key=scoped_key(idem, "session"),
    )
    session_id = str(session.get("session_id"))
    goal_state.phase = "probe"
    goal_state.session.session_id = session_id
    goal_state.session.started_at = session.get("started_at")
    goal_state.session.channel = str(session.get("channel") or "web")
    goal_state.session.minutes_cap = goal.get("minutes_per_session")
    goal_state.session.probe_asked = 0
    goal_state.session.consecutive_idk = 0
    goal_state.session.consecutive_wrong = 0
    goal_state.session.ended = False
    services.state.touch()

    question, meta = _serve_next_probe_item(services, goal, goal_id, session_id, idem=idem)
    budget = int(meta.get("probe_budget") or services.settings.probe_budget)
    return {
        "session_id": session_id,
        "budget": budget,
        "question": question,
        "done": question is None,
    }


@router.post("/goals/{goal_id}/probe/answer")
def probe_answer(
    goal_id: str,
    body: ProbeAnswerIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    question = require_question(services, goal_id, body.item_id)

    # The client's `correct` flag, if it sent one, is not read. The key is ours.
    grade = orch.grade_mc(body.response, question.answer, question.options)
    idk = bool(body.idk) or grade.idk

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
            "assistance_level": 0,  # probe.md: there are no hints in the probe
            "context": "probe",
            "channel": goal_state.session.channel or "web",
            "prompt_version": services.prompt_version,
            "grader_version": grade.grader_version,
            "evaluation_method": grade.evaluation_method,
        },
        idempotency_key=scoped_key(idem, "event"),
    )

    session_state = goal_state.session
    session_state.probe_asked += 1
    session_state.consecutive_idk = session_state.consecutive_idk + 1 if idk else 0
    session_state.consecutive_wrong = (
        session_state.consecutive_wrong + 1 if not grade.correct and not idk else 0
    )
    goal_state.teach.steps.append(
        {
            "kind": "checkpoint",
            "node_title": question.node_title,
            "item_id": question.item_id,
            "item_kind": question.kind,
            "item_status": question.status,
            "stem": question.stem,
            "response": body.response,
            "correct": grade.correct,
            "confidence": body.confidence,
            "assistance_level": 0,
            "verdict": "probe answer — maps the edge, not a mastery pass",
        }
    )
    services.state.clear_in_flight(goal_id, body.session_id)

    probe_budget = int(services.settings.probe_budget)
    stop = orch.probe_stop(
        asked=session_state.probe_asked,
        budget=probe_budget,
        consecutive_idk=session_state.consecutive_idk,
        consecutive_wrong=session_state.consecutive_wrong,
        picks_available=True,
    )

    next_question: dict[str, Any] | None = None
    if not stop.stop:
        next_question, meta = _serve_next_probe_item(
            services, goal, goal_id, body.session_id, idem=scoped_key(idem, "next")
        )
        probe_budget = int(meta.get("probe_budget") or probe_budget)
        if next_question is None:
            stop = orch.probe_stop(
                asked=session_state.probe_asked, budget=probe_budget, picks_available=False
            )

    if stop.stop:
        goal_state.phase = "teach"
    services.state.touch()

    return {
        "recorded": recorded_event(
            recorded,
            kind="probe_answer",
            session_id=body.session_id,
            goal_id=goal_id,
            node_id=question.node_id,
            item_version_id=question.item_version_id,
            response=body.response,
            correct=grade.correct,
            confidence=body.confidence,
            idk=idk,
            assistance_level=0,
            context="probe",
            channel=goal_state.session.channel or "web",
            prompt_version=services.prompt_version,
            grader_version=grade.grader_version,
        ),
        "node_state": recorded.get("node_state") or {},
        "next": next_question,
        "done": next_question is None,
        "asked": session_state.probe_asked,
        "budget": probe_budget,
        "stop_reason": stop.reason or None,
    }
