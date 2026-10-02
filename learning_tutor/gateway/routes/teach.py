"""Teaching: step, hint, answer, teach-back, misconceptions, disputes, session end.

Every decision on this path is made in ``tutor/orchestrator.py`` and every durable fact is
written by learner-svc. What is left here is transport: shape a call, read its answer,
return the contracted JSON.

The three rules this module is where they can be broken, and is not:

* the hint ladder is checked before a hint is generated, not after;
* the answer is graded against the key the gateway stored, never against a flag the
  client sent;
* a misconception is suspected, never asserted, until the three steps have run.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from ...tutor import orchestrator as orch
from ...tutor.generate import (
    author_item,
    check_latex,
    generate_feedback,
    generate_hint,
    generate_interrupt_answer,
    generate_misconception_probe,
    generate_teach_step,
    grade_teach_back,
    judge_misconception_step,
    render_diagram,
)
from ..deps import IdempotencyHeader, Services, get_services, scoped_key
from ..errors import GatewayError, bad_request, conflict, not_found
from ..models import (
    DisputeIn,
    MisconceptionStepIn,
    SessionEndIn,
    TeachAnswerIn,
    TeachBackIn,
    TeachHintIn,
    TeachInterruptIn,
    TeachNextIn,
)
from ..state import Question
from ._common import (
    build_session_log,
    citations_for,
    cite_claim,
    corpus_for,
    graph_of,
    leading_claim,
    learner_goal,
    minutes_since,
    node_by_id,
    question_payload,
    recorded_event,
    require_question,
    write_session_log,
)

router = APIRouter(prefix="/api", tags=["teach"])


def _remember_checkpoint(
    services: Services,
    goal_id: str,
    session_id: str,
    *,
    node_id: str,
    node_title: str,
    authored: dict[str, Any],
    ask_confidence: bool,
    context: str = "in-session",
) -> Question:
    spec = authored["spec"]
    question = Question(
        item_id=str(authored["item_id"]),
        item_version_id=authored.get("item_version_id"),
        node_id=node_id,
        node_title=node_title,
        stem=spec["stem"],
        options=list(spec["options"]),
        answer=str(spec["answer"]),
        kind=str(spec.get("kind") or "concept"),
        explanation=str(authored.get("explanation") or ""),
        distractor_misconceptions=dict(spec.get("distractor_misconceptions") or {}),
        context=context,
        status=str(authored.get("status") or "TEACHING_ONLY"),
        ask_confidence=ask_confidence,
    )
    services.state.remember(goal_id, question, session_id=session_id)
    return question


@router.post("/goals/{goal_id}/teach/next")
def teach_next(
    goal_id: str,
    body: TeachNextIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)

    picks = services.learner.get(
        "/v1/next", {"goal": goal_id, "mode": "teach", "n": 1, "session": body.session_id}
    )
    pick = orch.next_teach_node(picks)
    if pick is None:
        goal_state.phase = "done"
        services.state.touch()
        raise conflict(
            "nothing left to teach on this goal: "
            + str(picks.get("note") or "every node is known"),
            code="nothing_to_teach",
        )

    node_id = str(pick.get("node_id"))
    node_title = str(pick.get("node_title") or "")
    graph = graph_of(services, goal_id)
    node = node_by_id(graph, node_id) or {"node_id": node_id, "title": node_title}
    state = dict(node.get("state") or {})

    teach = goal_state.teach
    if teach.node_id != node_id:
        teach.node_id = node_id
        teach.node_title = node_title
        teach.strategy_switches = 0
        teach.consecutive_fails = 0
        teach.backed_up = False
    strategy = orch.choose_strategy(state, switches=teach.strategy_switches)
    teach.strategy = strategy

    context, _structure, _exists = corpus_for(
        services, goal_id, query=node_title, max_tokens=3000
    )
    step, _meta = generate_teach_step(
        services.pack,
        goal=goal,
        extras=goal_state.contract,
        node_title=node_title,
        strategy=strategy,
        node_state=state,
        corpus_context=context,
    )
    diagram = render_diagram(
        step.mermaid,
        services.render,
        pack=services.pack,
        goal=goal,
        extras=goal_state.contract,
    )
    latex = check_latex(step.latex, services.render)

    authored = author_item(
        services.pack,
        services.learner,
        phase="checkpoint",
        goal=goal,
        extras=goal_state.contract,
        node_id=node_id,
        node_title=node_title,
        depth=str(goal.get("depth") or "explain"),
        corpus_context=context,
        avoid=[q.stem for q in goal_state.questions.values() if q.node_id == node_id],
        idempotency_key=scoped_key(idem, f"checkpoint:{node_id}"),
    )
    question = _remember_checkpoint(
        services,
        goal_id,
        body.session_id,
        node_id=node_id,
        node_title=node_title,
        authored=authored,
        ask_confidence=True,
    )

    teach.steps.append(
        {
            "kind": "step",
            "node_title": node_title,
            "strategy": strategy,
            "markdown": step.markdown,
            "mermaid": diagram.get("mermaid"),
            "self_explanation_prompt": step.self_explanation_prompt,
        }
    )
    goal_state.phase = "teach"
    services.state.touch()

    return {
        "node": {k: v for k, v in node.items() if k != "state"} | {"state": state},
        "step": {
            "strategy": strategy,
            "markdown": step.markdown,
            "self_explanation_prompt": step.self_explanation_prompt,
            "mermaid": diagram.get("mermaid"),
            "svg": diagram.get("svg"),
            "latex": step.latex,
            "latex_ok": bool(latex.get("ok")),
            "citations": citations_for(services, goal_id, node_title),
            "diagram_note": diagram.get("skipped"),
        },
        "checkpoint": question_payload(question),
        "assistance_level": 0,
    }


def _current_step(teach: Any) -> dict[str, Any] | None:
    """The most recent teaching step served for the node being taught, from gateway state."""

    for entry in reversed(list(teach.steps)):
        if entry.get("kind") == "step" and entry.get("node_title") == teach.node_title:
            return entry
    return None


@router.post("/goals/{goal_id}/teach/interrupt")
def teach_interrupt(
    goal_id: str,
    body: TeachInterruptIn,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    """"Wait, why?" — answered inline, against the step already on the learner's screen.

    SKILL.md *Interrupts and disputes*: answer inline, add no node, resume the **same**
    step. So this route is deliberately inert: it advances nothing, changes no assistance
    level, and writes no evidence. ``step_unchanged: true`` is not decoration — it is the
    contract that the client may keep rendering the step it already has.

    The one thing it refuses is the reveal by another route. An interrupt that is really
    "which option is right?" gets checkpoint.md's ladder rule back instead of an answer,
    with ``refused_checkpoint_answer: true`` so the client can say why.
    """

    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    teach = goal_state.teach

    if goal_state.phase != "teach":
        raise conflict(
            f"goal {goal_id!r} is in the {goal_state.phase!r} phase, not teaching: an "
            "interrupt is a question about a step that is on screen, and there is none",
            code="not_teaching",
        )
    open_session = goal_state.session.session_id
    if open_session and body.session_id != open_session:
        raise conflict(
            f"session {body.session_id!r} is not the open session ({open_session!r})",
            code="wrong_session",
        )
    if not teach.node_id or teach.node_id != body.node_id:
        raise conflict(
            f"node {body.node_id!r} is not the node being taught "
            f"({teach.node_id or 'none'}): interrupts are answered against the current "
            "step only, and the gateway does not re-teach a past node to answer one",
            code="not_current_node",
        )

    checkpoint = services.state.in_flight(goal_id, body.session_id)

    def remember(question: str, *, refused: bool) -> None:
        """Not evidence, and not a note: learner-svc has no note route (only
        ``POST /v1/sessions/{s}/log``, which writes the whole md-log). This is gateway
        state, so the question is visible in the passport rather than swallowed."""

        teach.steps.append(
            {
                "kind": "interrupt",
                "node_title": teach.node_title,
                "question": question,
                "refused_checkpoint_answer": refused,
            }
        )
        services.state.touch()

    # checkpoint.md rules 1-3, before any generation: an interrupt is not a hint ladder.
    if checkpoint is not None and orch.asks_for_the_checkpoint_answer(body.question):
        remember(body.question, refused=True)
        return {
            "answer_markdown": orch.CHECKPOINT_ANSWER_RULE,
            "citations": [],
            "step_unchanged": True,
            "refused_checkpoint_answer": True,
        }

    step = _current_step(teach)
    context, _structure, _exists = corpus_for(
        services, goal_id, query=f"{teach.node_title} {body.question}", max_tokens=3000
    )
    answer, _meta = generate_interrupt_answer(
        services.pack,
        goal=goal,
        extras=goal_state.contract,
        node_title=teach.node_title or body.node_id,
        strategy=teach.strategy or "example-first",
        step_markdown=str((step or {}).get("markdown") or ""),
        question=body.question,
        checkpoint_stem=checkpoint.stem if checkpoint else "",
        corpus_context=context,
    )

    markdown = (answer.answer_markdown or "").strip()
    cited = cite_claim(
        services, goal_id, leading_claim(markdown, teach.node_title or body.node_id)
    )
    citations = list(cited.get("citations") or [])
    if not citations:
        # Abstention is visible, per plan.md's cite-or-abstain rule: an answer with no
        # source says so in the answer, rather than arriving with a silently empty list.
        markdown = (
            f"{markdown}\n\n_No source found for this — {cited.get('reason') or 'abstained'}. "
            "Answered from model knowledge, not from your material._"
        ).strip()

    remember(body.question, refused=False)
    return {
        "answer_markdown": markdown,
        "citations": citations,
        "step_unchanged": True,
        "refused_checkpoint_answer": False,
    }


@router.post("/goals/{goal_id}/teach/hint")
def teach_hint(
    goal_id: str,
    body: TeachHintIn,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    question = require_question(services, goal_id, body.item_id)

    ruling = orch.hint_allowed(body.level, question.attempts, question.assistance_level)
    if not ruling.allowed:
        raise conflict(ruling.reason, code="hint_refused")

    hint, _meta = generate_hint(
        services.pack,
        goal=goal,
        extras=goal_state.contract,
        node_title=question.node_title,
        stem=question.stem,
        options=question.options,
        level=body.level,
        attempts=question.attempts,
    )
    question.assistance_level = max(question.assistance_level, body.level)
    services.state.touch()
    return {
        "level": body.level,
        "hint_markdown": hint.hint_markdown,
        "counts_toward_mastery": body.level < orch.UNEARNED_ASSISTANCE,
    }


@router.post("/goals/{goal_id}/teach/answer")
def teach_answer(
    goal_id: str,
    body: TeachAnswerIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    question = require_question(services, goal_id, body.item_id)

    grade = orch.grade_mc(body.response, question.answer, question.options)
    idk = bool(body.idk) or grade.idk
    # The recorded level is the highest reached, not the one the client claims.
    assistance = max(int(body.assistance_level or 0), question.assistance_level)
    question.attempts += 1
    question.assistance_level = assistance

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
            "assistance_level": assistance,
            "context": "in-session",
            "channel": goal_state.session.channel or "web",
            "prompt_version": services.prompt_version,
            "grader_version": grade.grader_version,
            "evaluation_method": grade.evaluation_method,
        },
        idempotency_key=scoped_key(idem, "event"),
    )

    # CONTRACTS.md *Orchestrator rule (auto-promote)*: the checkpoint is recorded, so ask
    # learner-svc to promote the item once it has enough uses. learner-svc keeps the rule
    # and the holdout draw; a refusal is reported in `promotion`, never raised.
    promotion = orch.promote_if_due(
        services.learner,
        node_id=question.node_id,
        item_id=question.item_id,
        item_version_id=question.item_version_id,
        status=str(recorded.get("item_status") or question.status),
        min_uses=services.settings.promote_min_uses,
        idempotency_key=scoped_key(idem, "promote"),
    )
    if promotion.get("promoted"):
        question.status = str(promotion.get("status") or question.status)

    teach = goal_state.teach
    passed = grade.correct and not idk
    teach.consecutive_fails = 0 if passed else teach.consecutive_fails + 1

    decision = orch.decide_after_answer(
        correct=grade.correct,
        idk=idk,
        confidence=body.confidence,
        assistance_level=assistance,
        consecutive_fails=teach.consecutive_fails,
        nodes_since_teach_back=teach.nodes_since_teach_back + (1 if passed else 0),
        strategy_switched=teach.strategy_switches > 0,
        backed_up=teach.backed_up,
        minutes_elapsed=minutes_since(goal_state.session.started_at),
        minutes_cap=goal_state.session.minutes_cap,
    )

    misconception: dict[str, Any] | None = None
    if decision.misconception_suspected:
        chosen = orch.option_key(body.response or "", 0)
        claim = question.distractor_misconceptions.get(chosen) or (
            f"a confident wrong answer on '{question.node_title}' — belief not yet named"
        )
        try:
            services.learner.post(
                "/v1/misconceptions/suspect",
                {
                    "node": question.node_id,
                    "claim": claim,
                    "session_id": body.session_id,
                    "prompt_version": services.prompt_version,
                },
                idempotency_key=scoped_key(idem, "suspect"),
            )
            misconception = {"claim": claim}
            goal_state.misconceptions.setdefault(question.node_id, [])
        except GatewayError as exc:
            if exc.status >= 500:
                raise
            misconception = {"claim": claim, "note": exc.message}

    if passed:
        teach.nodes_completed += 1
        teach.nodes_since_teach_back += 1
        services.state.clear_in_flight(goal_id, body.session_id)
    if decision.decision == "switch_strategy":
        teach.strategy_switches += 1
    elif decision.decision == "back_up":
        teach.backed_up = True

    feedback, _meta = generate_feedback(
        services.pack,
        goal=goal,
        extras=goal_state.contract,
        node_title=question.node_title,
        stem=question.stem,
        options=question.options,
        response=body.response or "",
        correct=grade.correct,
        assistance_level=assistance,
        explanation=question.explanation,
        reveal_allowed=assistance >= orch.REVEAL_LEVEL,
    )

    teach.steps.append(
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
            "assistance_level": assistance,
            "verdict": (
                "Independent pass."
                if passed and assistance == 0
                else "Assisted pass — not mastery."
                if passed and assistance < orch.UNEARNED_ASSISTANCE
                else "Recorded, but assistance >= 5 never counts toward mastery."
                if passed
                else "Not a pass."
            ),
        }
    )
    services.state.touch()

    return {
        "correct": grade.correct,
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
            assistance_level=assistance,
            context="in-session",
            channel=goal_state.session.channel or "web",
            prompt_version=services.prompt_version,
            grader_version=grade.grader_version,
        ),
        "node_state": recorded.get("node_state") or {},
        "feedback_markdown": feedback.feedback_markdown,
        "reveal_allowed": decision.reveal_allowed,
        "misconception_suspected": misconception,
        "decision": decision.decision,
        "decision_reason": decision.reason,
        "assistance_level": assistance,
        "counts_toward_mastery": bool(recorded.get("counts_toward_mastery")),
        "promotion": promotion,
    }


@router.post("/goals/{goal_id}/teach/teach-back")
def teach_back(
    goal_id: str,
    body: TeachBackIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    graph = graph_of(services, goal_id)
    node = node_by_id(graph, body.node_id)
    if node is None:
        raise not_found(f"unknown node {body.node_id!r} in goal {goal_id!r}")

    grade, _meta = grade_teach_back(
        services.pack,
        goal=goal,
        extras=goal_state.contract,
        node_title=str(node.get("title") or body.node_id),
        explanation=body.explanation,
    )
    recorded = services.learner.post(
        "/v1/events",
        {
            "kind": "teach_back",
            "session_id": body.session_id,
            "node": body.node_id,
            "score": int(grade.score),
            "rubric_version": services.rubric_version,
            "assistance_level": 0,
            "notes": grade.anchor or None,
            "context": "in-session",
            "prompt_version": services.prompt_version,
        },
        idempotency_key=scoped_key(idem, "event"),
    )

    goal_state.teach.nodes_since_teach_back = 0
    goal_state.teach.steps.append(
        {
            "kind": "teach_back",
            "node_title": node.get("title"),
            "score": int(grade.score),
            "assistance_level": 0,
            "feedback": grade.feedback_markdown,
        }
    )
    services.state.touch()

    return {
        "score": int(grade.score),
        "rubric_version": services.rubric_version,
        "feedback_markdown": grade.feedback_markdown,
        "recorded": recorded_event(
            recorded,
            kind="teach_back",
            session_id=body.session_id,
            goal_id=goal_id,
            node_id=body.node_id,
            correct=int(grade.score) >= 2,
            assistance_level=0,
            context="in-session",
            prompt_version=services.prompt_version,
            grader_version=services.rubric_version,
            evaluation_method="rubric",
        ),
        "action": {
            3: "node stands",
            2: "node stands; one transfer item next session",
            1: "reteach now with a different strategy",
            0: "back up: teach the prerequisite, then this node again",
        }[int(grade.score)],
    }


@router.post("/goals/{goal_id}/misconception/step")
def misconception_step(
    goal_id: str,
    body: MisconceptionStepIn,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    graph = graph_of(services, goal_id)
    node = node_by_id(graph, body.node_id)
    if node is None:
        raise not_found(f"unknown node {body.node_id!r} in goal {goal_id!r}")
    node_title = str(node.get("title") or body.node_id)

    outcome = body.outcome
    reply = ""
    if outcome is None:
        judged, _meta = judge_misconception_step(
            services.pack,
            goal=goal,
            extras=goal_state.contract,
            node_title=node_title,
            claim=body.claim,
            step=body.step,
            learner_response=body.learner_response,
        )
        outcome = judged.outcome
        reply = judged.reply_markdown

    result = orch.misconception_sequence(
        services.learner,
        goal_id=goal_id,
        node_id=body.node_id,
        claim=body.claim,
        step=body.step,
        outcome=outcome,
        session_id=body.session_id,
        prompt_version=services.prompt_version,
    )

    history = goal_state.misconceptions.setdefault(body.node_id, [])
    history.append({"step": body.step, "outcome": outcome, "claim": body.claim})
    goal_state.teach.steps.append(
        {
            "kind": "misconception",
            "node_title": node_title,
            "claim": body.claim,
            "step": body.step,
            "outcome": outcome,
            "state": result["state"],
        }
    )
    services.state.touch()

    next_prompt = None
    if result["next_step"]:
        probe, _meta = generate_misconception_probe(
            services.pack,
            goal=goal,
            extras=goal_state.contract,
            node_title=node_title,
            claim=body.claim,
            step=result["next_step"],
            history=history,
        )
        next_prompt = probe.prompt_markdown

    return {
        "state": result["state"],
        "next_step": result["next_step"],
        "outcome": outcome,
        "reply_markdown": reply,
        "next_prompt_markdown": next_prompt,
        "recorded": result["recorded"],
    }


@router.post("/goals/{goal_id}/dispute")
def open_dispute(
    goal_id: str,
    body: DisputeIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """A typed dispute. "I already know this" comes with its two-item check attached."""

    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    opened = services.learner.post(
        "/v1/disputes",
        {
            "type": body.type,
            "node": body.node_id,
            "item_id": body.item_id,
            "note": body.note or None,
            "session_id": body.session_id,
        },
        idempotency_key=scoped_key(idem, "dispute"),
    )

    check: dict[str, Any] | None = None
    if body.type == "I already know this" and body.node_id:
        graph = graph_of(services, goal_id)
        node = node_by_id(graph, body.node_id)
        node_title = str((node or {}).get("title") or body.node_id)
        context, _structure, _exists = corpus_for(
            services, goal_id, query=node_title, max_tokens=2000
        )
        questions = []
        for index, surface in enumerate(
            (None, "change the situation, notation or representation, not just the wording")
        ):
            authored = author_item(
                services.pack,
                services.learner,
                phase="probe",
                goal=goal,
                extras=goal_state.contract,
                node_id=body.node_id,
                node_title=node_title,
                depth=str(goal.get("depth") or "explain"),
                corpus_context=context,
                avoid=[q.stem for q in goal_state.questions.values()
                       if q.node_id == body.node_id],
                idempotency_key=scoped_key(idem, f"dispute-item:{index}"),
                surface_form=surface,
            )
            question = _remember_checkpoint(
                services,
                goal_id,
                body.session_id or "",
                node_id=body.node_id,
                node_title=node_title,
                authored=authored,
                ask_confidence=True,
                context="transfer" if index else "in-session",
            )
            questions.append(question_payload(question))
        check = {
            "items": questions,
            "rule": "both must pass at assistance 0 for the dispute to be upheld; "
            "one pass is a rejection and the node gets taught",
        }

    return {"dispute_id": opened.get("dispute_id"), "check": check, "dispute": opened}


@router.post("/goals/{goal_id}/session/end")
def end_session(
    goal_id: str,
    body: SessionEndIn,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)

    minutes = int(minutes_since(goal_state.session.started_at))
    session = services.learner.post(
        f"/v1/sessions/{body.session_id}/end",
        {"summary": body.summary},
        idempotency_key=scoped_key(idem, "end"),
    )
    try:
        summary_md = services.learner.text(f"/v1/summary/{goal_id}", {"format": "md"})
    except GatewayError as exc:
        summary_md = f"(summary unavailable: {exc.message})"

    steps = list(goal_state.teach.steps)
    started = steps[0].get("node_title") if steps else "nothing recorded"
    stopped = goal_state.teach.node_title or "nothing recorded"
    markdown = build_session_log(
        goal=goal,
        session={**session, "session_id": body.session_id, "goal_id": goal_id,
                 "channel": goal_state.session.channel},
        prompt_version=services.prompt_version,
        rubric_version=services.rubric_version,
        minutes=minutes,
        steps=steps,
        summary_md=summary_md,
        started=str(started),
        stopped=str(stopped),
        next_time=(
            f"Open with a delayed retrieval item on {stopped}, then continue from the "
            "lowest unknown node whose prerequisites are known."
        ),
    )
    log_path = write_session_log(
        services,
        {**session, "session_id": body.session_id, "goal_id": goal_id},
        markdown,
        idempotency_key=scoped_key(idem, "log"),
    )

    goal_state.log_paths.append(log_path)
    goal_state.session.ended = True
    goal_state.teach.steps = []
    services.state.touch()

    return {"session": session, "log_path": log_path}


@router.post("/goals/{goal_id}/session/start")
def start_session(
    goal_id: str,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """Not in CONTRACTS.md, but a teaching session that skips the probe needs a door.

    ``probe/start`` is the contracted way in; this is the same thing for a goal already
    past the probe, so a returning learner does not have to re-probe to be taught.
    """

    goal = learner_goal(services, goal_id)
    goal_state = services.state.get(goal_id)
    if not goal_state.plan_approved:
        raise bad_request("approve the plan first", code="plan_not_approved")
    session = services.learner.post(
        "/v1/sessions",
        {"goal_id": goal_id, "channel": "web"},
        idempotency_key=scoped_key(idem, "session"),
    )
    goal_state.session.session_id = str(session.get("session_id"))
    goal_state.session.started_at = session.get("started_at")
    goal_state.session.channel = str(session.get("channel") or "web")
    goal_state.session.minutes_cap = goal.get("minutes_per_session")
    goal_state.session.ended = False
    goal_state.phase = "teach"
    services.state.touch()
    return {"session": session, "minutes_cap": goal_state.session.minutes_cap}
