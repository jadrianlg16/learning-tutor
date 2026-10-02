"""The service layer: everything the CLI does, as plain functions.

This module is deliberately transport-free. ``learner_svc`` (FastAPI) and ``mcp_server``
(FastMCP) call exactly these functions, so the CLI, HTTP and MCP surfaces cannot drift
apart.

Every write regenerates the derived views (``state.json``, and ``learner.md`` when a goal is
in scope): the model never edits numbers, code recomputes them.

Two Stage 1 additions run through every mutating function here
(CONTRACTS.md, *Adopted from the Tutor MCP audit*):

* ``idempotency_key`` — same key + same body replays the first response; same key + a
  different body raises :class:`~.store.IdempotencyConflict` (HTTP 409). See
  :mod:`.idempotency`.
* ``evaluation_method`` — how an answer was judged. ``host_llm`` (the default: the model
  grading its own learner) is recorded but never counts toward ``known``; only
  ``blind_solver``, ``rubric`` and ``human`` do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..config import Settings, get_settings
from . import blueprint as blueprint_mod
from . import disputes as disputes_mod
from . import events as events_mod
from . import exam as exam_mod
from . import export as export_mod
from . import fsrs_sched, idempotency, selection, views
from . import graph as graph_mod
from . import holdouts as holdouts_mod
from . import items as items_mod
from . import metrics as metrics_mod
from . import misconceptions as misc_mod
from . import study as study_mod
from .ids import prefixed, slugify
from .models import DEFAULT_GRADER_VERSION, DEFAULT_PROMPT_VERSION
from .store import LearnerError, Store, open_store, parse_ts, utcnow

DEPTHS = ("recognize", "explain", "apply", "analyze")
SOURCE_PRIORITIES = ("alignment", "authority")
TEACH_BACK_PASS_SCORE = 2
#: what an answer is judged by when the caller does not say
DEFAULT_EVALUATION_METHOD = "host_llm"
#: a teach-back is scored against a versioned rubric, by definition of the command
TEACH_BACK_EVALUATION_METHOD = "rubric"
#: default and hard cap for ``events`` — a raw-row read is a receipt, not a bulk export
DEFAULT_EVENT_LIMIT = 200
MAX_EVENT_ROWS = 1000
NOTES_TEMPLATE = """# Notes

Prose only: preferences, goals, qualitative observations. Numbers live in the event store
and are written by code, never here.

## Prefs

<!-- One bullet per preference, e.g. "- formal-first over analogy". They are copied into
     learner.md verbatim. Preferences never need proof; claims about knowledge do. -->
"""


def _idem(store: Store, key: str | None, operation: str, body: dict[str, Any], call) -> Any:
    return idempotency.run(store, key=key, operation=operation, body=body, call=call)


# --------------------------------------------------------------------------- setup
def init(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    settings.ensure_dirs()
    store = open_store(settings)
    if not settings.notes_path.exists():
        settings.notes_path.write_text(NOTES_TEMPLATE, encoding="utf-8")
    views.write_state(store)
    return {
        "data_dir": str(settings.data_dir),
        "db": str(settings.db_path),
        "vault_dir": str(settings.vault_dir),
        "notes": str(settings.notes_path),
        "state": str(settings.state_path),
        "schema_version": store.schema_version(),
    }


# --------------------------------------------------------------------------- goals
def goal_add(
    store: Store,
    *,
    goal_id: str,
    title: str,
    depth: str = "explain",
    deadline: str | None = None,
    minutes_per_session: int | None = None,
    purpose: str | None = None,
    assessment: str | None = None,
    source_priority: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    if depth not in DEPTHS:
        raise LearnerError(f"depth must be one of {', '.join(DEPTHS)}")
    if source_priority is not None and source_priority not in SOURCE_PRIORITIES:
        raise LearnerError(f"source priority must be one of {', '.join(SOURCE_PRIORITIES)}")

    body = {
        "goal_id": goal_id,
        "title": title,
        "depth": depth,
        "deadline": deadline,
        "minutes_per_session": minutes_per_session,
        "purpose": purpose,
        "assessment": assessment,
        "source_priority": source_priority,
    }

    def _run() -> dict[str, Any]:
        if store.one("SELECT 1 FROM goals WHERE goal_id = ?", (goal_id,)):
            raise LearnerError(f"goal {goal_id!r} already exists")
        store.insert(
            "goals",
            {
                "goal_id": goal_id,
                "title": title,
                "depth": depth,
                "deadline": deadline,
                "minutes_per_session": minutes_per_session,
                "purpose": purpose,
                "assessment": assessment,
                "source_priority": source_priority,
                "contract": None,
                "created_at": utcnow(),
            },
        )
        views.write_state(store)
        # the folder the Stage 0 skill drops slides, PDFs and sources.md into
        sources = store.settings.sources_dir / slugify(goal_id)
        sources.mkdir(parents=True, exist_ok=True)
        return {
            "goal_id": goal_id,
            "title": title,
            "depth": depth,
            "deadline": deadline,
            "assessment": assessment,
            "source_priority": source_priority,
            "sources_dir": str(sources),
        }

    return _idem(store, idempotency_key, "goal_add", body, _run)


def resolve_goal(store: Store, goal_id: str | None) -> str:
    """Allow --goal to be omitted when exactly one goal exists."""

    if goal_id:
        return goal_id
    rows = store.query("SELECT goal_id FROM goals ORDER BY created_at")
    if len(rows) == 1:
        return rows[0]["goal_id"]
    if not rows:
        raise LearnerError("no goals yet: run `learner goal add` first")
    raise LearnerError(
        "--goal is required: " + ", ".join(r["goal_id"] for r in rows)
    )


def goal_get(store: Store, goal_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM goals WHERE goal_id = ?", (goal_id,))
    if not row:
        raise LearnerError(f"unknown goal {goal_id!r}")
    return dict(row)


def goal_list(store: Store) -> list[dict[str, Any]]:
    return [dict(r) for r in store.query("SELECT * FROM goals ORDER BY created_at")]


#: the goal columns ``goal update`` may change (``sessions_per_week`` since migration 3)
GOAL_UPDATE_FIELDS = (
    "title",
    "depth",
    "deadline",
    "minutes_per_session",
    "sessions_per_week",
    "purpose",
    "assessment",
    "source_priority",
)


def goal_update(
    store: Store,
    goal_id: str,
    *,
    idempotency_key: str | None = None,
    **changes: Any,
) -> dict[str, Any]:
    """Change a goal's contract fields — a moved exam date, a new weekly cadence.

    ``None`` means "leave it"; ``deadline=""`` clears the deadline. The goal row is
    configuration, not evidence, so it is updated in place — and the change is also
    appended as a ``note`` event (``payload.goal_update``, old and new value per field), so
    the event log still says when the plan's assumptions moved and from what.
    """

    unknown = sorted(set(changes) - set(GOAL_UPDATE_FIELDS))
    if unknown:
        raise LearnerError(
            f"cannot update {', '.join(unknown)}: one of {', '.join(GOAL_UPDATE_FIELDS)}"
        )
    wanted = {k: v for k, v in changes.items() if v is not None}
    if "deadline" in wanted:
        text = str(wanted["deadline"]).strip()
        if text:
            from datetime import date

            try:
                date.fromisoformat(text)
            except ValueError as exc:
                raise LearnerError(f"deadline must be YYYY-MM-DD, got {text!r}") from exc
        wanted["deadline"] = text or None
    if "depth" in wanted and wanted["depth"] not in DEPTHS:
        raise LearnerError(f"depth must be one of {', '.join(DEPTHS)}")
    if "source_priority" in wanted and wanted["source_priority"] not in SOURCE_PRIORITIES:
        raise LearnerError(f"source priority must be one of {', '.join(SOURCE_PRIORITIES)}")
    if "minutes_per_session" in wanted and int(wanted["minutes_per_session"]) < 0:
        raise LearnerError("minutes per session must be 0 or more")
    if "sessions_per_week" in wanted and not 1 <= int(wanted["sessions_per_week"]) <= 21:
        raise LearnerError("sessions per week must be 1-21")
    if "title" in wanted and not str(wanted["title"]).strip():
        raise LearnerError("title cannot be empty")

    def _run() -> dict[str, Any]:
        before = goal_get(store, goal_id)
        changed = {
            key: {"from": before.get(key), "to": value}
            for key, value in wanted.items()
            if before.get(key) != value
        }
        if changed:
            store.update("goals", {"goal_id": goal_id}, {k: v["to"] for k, v in changed.items()})
            events_mod.append(
                store, kind="note", goal_id=goal_id, payload={"goal_update": changed}
            )
            views.refresh(store, goal_id)
        return {"goal": goal_get(store, goal_id), "changed": changed}

    return _idem(
        store, idempotency_key, "goal_update", {"goal_id": goal_id, **wanted}, _run
    )


# --------------------------------------------------------------------------- sessions
def session_start(
    store: Store,
    *,
    goal_id: str,
    channel: str = "claude-code",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    goal_get(store, goal_id)
    if channel not in events_mod.VALID_CHANNELS:
        raise LearnerError(f"unknown channel {channel!r}")

    def _run() -> dict[str, Any]:
        sid = prefixed("s")
        store.insert(
            "sessions",
            {
                "session_id": sid,
                "goal_id": goal_id,
                "channel": channel,
                "started_at": utcnow(),
                "ended_at": None,
                "summary": None,
            },
        )
        events_mod.append(
            store, kind="session_start", session_id=sid, goal_id=goal_id, channel=channel
        )
        views.refresh(store, goal_id)
        return {"session_id": sid, "goal_id": goal_id, "channel": channel}

    return _idem(
        store,
        idempotency_key,
        "session_start",
        {"goal_id": goal_id, "channel": channel},
        _run,
    )


def session_get(store: Store, session_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
    if not row:
        raise LearnerError(f"unknown session {session_id!r}")
    return dict(row)


def session_end(
    store: Store,
    session_id: str,
    summary: str | None = None,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        row = session_get(store, session_id)
        if row["ended_at"]:
            raise LearnerError(f"session {session_id} already ended at {row['ended_at']}")
        store.update(
            "sessions", {"session_id": session_id}, {"ended_at": utcnow(), "summary": summary}
        )
        events_mod.append(
            store,
            kind="session_end",
            session_id=session_id,
            goal_id=row["goal_id"],
            channel=row["channel"],
            payload={"summary": summary} if summary else None,
        )
        views.refresh(store, row["goal_id"])
        return session_get(store, session_id)

    return _idem(
        store,
        idempotency_key,
        "session_end",
        {"session_id": session_id, "summary": summary},
        _run,
    )


# --------------------------------------------------------------------------- recording
def record_answer(
    store: Store,
    *,
    session_id: str | None,
    item_id: str,
    response: str | None,
    correct: bool,
    confidence: int | None = None,
    idk: bool = False,
    assistance_level: int = 0,
    context: str = "in-session",
    channel: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    evaluation_method: str = DEFAULT_EVALUATION_METHOD,
    ts: str | None = None,
    idempotency_key: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``ts`` backdates the event (used by tests and by any later backfill); it is not a
    CLI flag, because a tutor never chooses when something happened.

    ``evaluation_method`` says who judged the answer. The default, ``host_llm``, means the
    tutoring model decided the answer was right: that is recorded, it schedules the item,
    it can suspect a misconception — but it is never counted as an independent pass, so a
    node cannot reach ``known`` on self-graded evidence alone.

    ``extra`` is merged into the event payload (a mock's session, the option order shown).
    """

    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    grader_version = grader_version or DEFAULT_GRADER_VERSION
    body = {
        "session_id": session_id,
        "item_id": item_id,
        "response": response,
        "correct": bool(correct),
        "confidence": confidence,
        "idk": bool(idk),
        "assistance_level": assistance_level,
        "context": context,
        "channel": channel,
        "prompt_version": prompt_version,
        "grader_version": grader_version,
        "evaluation_method": evaluation_method,
        "ts": ts,
    }
    if extra:
        # only when present, so a replayed key recorded before `extra` existed still matches
        body["extra"] = extra

    def _run() -> dict[str, Any]:
        item = items_mod.get(store, item_id)
        session = session_get(store, session_id) if session_id else None
        goal_id = session["goal_id"] if session else None
        if goal_id is None:
            row = store.one(
                "SELECT goal_id FROM node_goals WHERE node_id = ? LIMIT 1", (item.node_id,)
            )
            goal_id = row["goal_id"] if row else None

        writes_evidence = item.status in items_mod.EVIDENCE_STATUSES
        kind = "probe_answer" if context == "probe" else "answer"
        event = events_mod.append(
            store,
            kind=kind,
            session_id=session_id,
            goal_id=goal_id,
            node_id=item.node_id,
            item_version_id=item.current_version_id,
            response=response,
            correct=int(bool(correct)),
            confidence=confidence,
            idk=idk,
            assistance_level=assistance_level,
            channel=channel or (session["channel"] if session else "claude-code"),
            context=context,
            prompt_version=prompt_version,
            grader_version=grader_version,
            evaluation_method=evaluation_method,
            payload={
                **(extra or {}),
                "item_id": item_id,
                "item_status": item.status,
                "holdout": item.holdout,
            },
            ts=ts,
        )

        schedule = None
        touched_misconceptions: list[dict[str, Any]] = []
        if writes_evidence:
            schedule = fsrs_sched.review(
                store,
                item_id,
                correct=bool(correct),
                assistance_level=assistance_level,
                confidence=confidence,
                idk=idk,
                when=parse_ts(ts) if ts else None,
            )
            distractors = item.version.distractor_misconceptions if item.version else {}
            touched_misconceptions = misc_mod.on_answer(
                store,
                item.node_id,
                correct=bool(correct),
                assistance_level=assistance_level,
                confidence=confidence,
                chosen_option=response,
                distractor_map=distractors,
            )
        if item.holdout:
            holdouts_mod.mark_checked(store, item_id)

        views.refresh(store, goal_id)
        from . import evidence as evidence_mod

        state = evidence_mod.derive(store, item.node_id)
        trusted = events_mod.is_trusted(evaluation_method)
        self_graded = bool(correct) and not idk and not trusted
        notes = []
        if not writes_evidence:
            notes.append("item is TEACHING_ONLY: the answer is logged but writes no evidence")
        if self_graded:
            notes.append(
                f"self-graded ({evaluation_method}): recorded, but a pass judged by the "
                "tutoring model never counts toward mastery"
            )
        return {
            "event_id": event.event_id,
            "item_id": item_id,
            "item_status": item.status,
            "wrote_evidence": writes_evidence,
            "evaluation_method": evaluation_method,
            "self_graded": self_graded,
            "note": "; ".join(notes) or None,
            "counts_toward_mastery": writes_evidence
            and bool(correct)
            and not idk
            and assistance_level < events_mod.UNEARNED_ASSISTANCE
            and trusted,
            "schedule": schedule,
            "node_state": state.model_dump(),
            "node_state_name": state.state,
            "misconceptions": touched_misconceptions,
        }

    return _idem(store, idempotency_key, "record_answer", body, _run)


def record_teach_back(
    store: Store,
    *,
    session_id: str | None,
    node: str,
    score: int,
    rubric_version: str,
    assistance_level: int = 0,
    notes: str | None = None,
    context: str = "in-session",
    prompt_version: str | None = None,
    grader_version: str | None = None,
    ts: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """A teach-back is scored against a versioned rubric, so its ``evaluation_method`` is
    always ``rubric``: the score comes from a frozen artefact, not from free judgement.

    ``grader_version`` defaults to ``rubric_version`` — the rubric *is* the grader, and
    ``--rubric-version`` is required, so silently overwriting it with a constant would
    lose the only version tag that matters here.
    """

    if not 0 <= score <= 3:
        raise LearnerError("teach-back score must be 0-3")
    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    grader_version = grader_version or rubric_version
    body = {
        "session_id": session_id,
        "node": node,
        "score": score,
        "rubric_version": rubric_version,
        "assistance_level": assistance_level,
        "notes": notes,
        "context": context,
        "prompt_version": prompt_version,
        "grader_version": grader_version,
        "ts": ts,
    }

    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node)
        session = session_get(store, session_id) if session_id else None
        goal_id = session["goal_id"] if session else None
        event = events_mod.append(
            store,
            kind="teach_back",
            session_id=session_id,
            goal_id=goal_id,
            node_id=node_id,
            correct=int(score >= TEACH_BACK_PASS_SCORE),
            assistance_level=assistance_level,
            channel=session["channel"] if session else "claude-code",
            context=context,
            prompt_version=prompt_version,
            grader_version=grader_version,
            evaluation_method=TEACH_BACK_EVALUATION_METHOD,
            payload={"score": score, "rubric_version": rubric_version, "notes": notes},
            ts=ts,
        )
        views.refresh(store, goal_id)
        from . import evidence as evidence_mod

        state = evidence_mod.derive(store, node_id)
        return {
            "event_id": event.event_id,
            "node_id": node_id,
            "score": score,
            "passed": score >= TEACH_BACK_PASS_SCORE,
            "evaluation_method": TEACH_BACK_EVALUATION_METHOD,
            "counts_toward_mastery": score >= TEACH_BACK_PASS_SCORE
            and assistance_level < events_mod.UNEARNED_ASSISTANCE,
            "node_state": state.model_dump(),
            "node_state_name": state.state,
        }

    return _idem(store, idempotency_key, "record_teach_back", body, _run)


# --------------------------------------------------------------------------- graph/items
def graph_import(
    store: Store, goal_id: str, payload: dict[str, Any], *, idempotency_key: str | None = None
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = graph_mod.import_graph(store, goal_id, payload)
        views.refresh(store, goal_id)
        return result

    return _idem(
        store, idempotency_key, "graph_import", {"goal_id": goal_id, "payload": payload}, _run
    )


def graph_show(store: Store, goal_id: str, fmt: str = "json") -> Any:
    from . import evidence as evidence_mod

    nodes = graph_mod.get_nodes(store, goal_id)
    node_ids = [n.node_id for n in nodes]
    edges = graph_mod.get_edges(store, node_ids)
    states = evidence_mod.derive_all(store, node_ids)
    if fmt == "mermaid":
        return graph_mod.to_mermaid(nodes, edges, {k: v.state for k, v in states.items()})
    return {
        "goal_id": goal_id,
        "graph_version": graph_mod.current_version(store),
        "nodes": [
            {**n.model_dump(), "state": states[n.node_id].model_dump()} for n in nodes
        ],
        "edges": [e.model_dump() for e in edges],
    }


def graph_revise(
    store: Store, goal_id: str, ops: Any, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    if isinstance(ops, dict):
        ops = ops.get("ops") or []

    def _run() -> dict[str, Any]:
        result = graph_mod.revise(store, goal_id, ops)
        views.refresh(store, goal_id)
        return result

    return _idem(store, idempotency_key, "graph_revise", {"goal_id": goal_id, "ops": ops}, _run)


def _item_payload(record) -> dict[str, Any]:
    data = record.model_dump()
    # `item_version_id` mirrors `current_version_id`: it is the name every other command
    # uses for the version that was served.
    data["item_version_id"] = record.current_version_id
    return data


def item_add(
    store: Store,
    node: str,
    spec: dict[str, Any],
    *,
    author: str = "model",
    item: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node)
        record = items_mod.add(store, node_id, spec, author=author, item=item)
        views.write_state(store)
        return _item_payload(record)

    return _idem(
        store,
        idempotency_key,
        "item_add",
        {"node": node, "spec": spec, "author": author, "item": item},
        _run,
    )


def item_validate(
    store: Store,
    item: str,
    *,
    by: str,
    result: str,
    notes: str | None = None,
    evaluation_method: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """``evaluation_method`` is optional here and defaults to *unstated*.

    Stage 0's guarantee is the identity check (the validator may not be the author) and it
    is unchanged. When a method *is* stated, ``host_llm`` no longer promotes the item to
    ``PRACTICE_EVIDENCE``: a model that both wrote and approved the question has not
    validated anything (CONTRACTS.md, *Adopted from the Tutor MCP audit*).
    """

    def _run() -> dict[str, Any]:
        record = items_mod.validate(
            store, item, by=by, result=result, notes=notes, evaluation_method=evaluation_method
        )
        views.write_state(store)
        data = _item_payload(record)
        if result == "pass" and evaluation_method == "host_llm":
            data["note"] = (
                "recorded, but a host_llm validation does not promote the item: the same "
                "model is never sole author, solver and judge"
            )
        return data

    return _idem(
        store,
        idempotency_key,
        "item_validate",
        {
            "item": item,
            "by": by,
            "result": result,
            "notes": notes,
            "evaluation_method": evaluation_method,
        },
        _run,
    )


def item_promote(
    store: Store, item: str, *, idempotency_key: str | None = None
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        record = items_mod.promote(store, item)
        views.write_state(store)
        data = _item_payload(record)
        data["note"] = (
            "hidden holdout: never served by `next`, only by `holdout-check`"
            if record.holdout
            else "in the teaching pool"
        )
        return data

    return _idem(store, idempotency_key, "item_promote", {"item": item}, _run)


# --------------------------------------------------------------------- misconceptions
def misconception_suspect(
    store: Store,
    *,
    node: str,
    claim: str,
    session_id: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    grader_version = grader_version or DEFAULT_GRADER_VERSION

    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node)
        result = misc_mod.suspect(
            store,
            node_id,
            claim,
            session_id=session_id,
            prompt_version=prompt_version,
            grader_version=grader_version,
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "misconception_suspect",
        {
            "node": node,
            "claim": claim,
            "session_id": session_id,
            "prompt_version": prompt_version,
            "grader_version": grader_version,
        },
        _run,
    )


def misconception_confirm_step(
    store: Store,
    *,
    node: str,
    claim: str,
    step: str,
    outcome: str,
    notes: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    grader_version = grader_version or DEFAULT_GRADER_VERSION

    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node)
        result = misc_mod.confirm_step(
            store,
            node_id,
            claim,
            step=step,
            outcome=outcome,
            note=notes,
            prompt_version=prompt_version,
            grader_version=grader_version,
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "misconception_confirm_step",
        {
            "node": node,
            "claim": claim,
            "step": step,
            "outcome": outcome,
            "notes": notes,
            "prompt_version": prompt_version,
            "grader_version": grader_version,
        },
        _run,
    )


def misconception_resolve(
    store: Store,
    *,
    node: str,
    claim: str,
    notes: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    prompt_version = prompt_version or DEFAULT_PROMPT_VERSION
    grader_version = grader_version or DEFAULT_GRADER_VERSION

    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node)
        result = misc_mod.resolve(
            store,
            node_id,
            claim,
            reason=notes,
            prompt_version=prompt_version,
            grader_version=grader_version,
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "misconception_resolve",
        {
            "node": node,
            "claim": claim,
            "notes": notes,
            "prompt_version": prompt_version,
            "grader_version": grader_version,
        },
        _run,
    )


# --------------------------------------------------------------------------- disputes
def dispute_open(
    store: Store,
    *,
    dispute_type: str,
    node: str | None = None,
    item_id: str | None = None,
    note: str | None = None,
    session_id: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        node_id = graph_mod.resolve(store, node) if node else None
        result = disputes_mod.open_dispute(
            store,
            dispute_type=dispute_type,
            node_id=node_id,
            item_id=item_id,
            note=note,
            session_id=session_id,
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "dispute_open",
        {
            "type": dispute_type,
            "node": node,
            "item_id": item_id,
            "note": note,
            "session_id": session_id,
        },
        _run,
    )


def dispute_settle(
    store: Store,
    dispute_id: str,
    *,
    outcome: str,
    evidence: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = disputes_mod.settle(store, dispute_id, outcome=outcome, evidence=evidence)
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "dispute_settle",
        {"dispute_id": dispute_id, "outcome": outcome, "evidence": evidence},
        _run,
    )


# --------------------------------------------------------------------------- reads
def next_(
    store: Store, goal_id: str, *, mode: str = "auto", n: int = 1, session_id: str | None = None
) -> dict[str, Any]:
    return selection.next_picks(store, goal_id, mode=mode, n=n, session_id=session_id)


def summary(store: Store, goal_id: str, fmt: str = "md") -> Any:
    path, text = views.write_learner_md(store, goal_id)
    views.write_state(store)
    if fmt == "md":
        return {"path": path, "markdown": text}
    data = views.build_summary(store, goal_id).model_dump()
    data["path"] = path
    return data


def holdout_check(store: Store, goal_id: str | None = None) -> dict[str, Any]:
    picks = holdouts_mod.due_picks(store, goal_id)
    return {
        "goal_id": goal_id,
        "due": len(picks),
        "picks": [p.model_dump() for p in picks],
        "note": "record the answer with --context delayed or --context transfer",
    }


def metrics(store: Store, goal_id: str | None = None) -> dict[str, Any]:
    data = metrics_mod.compute(store, goal_id).model_dump()
    data["disputes"] = disputes_mod.stats(store)
    return data


def export(store: Store, out: str | None = None) -> dict[str, Any]:
    return export_mod.export_events(store, out)


def events(
    store: Store,
    *,
    goal_id: str | None = None,
    node_id: str | None = None,
    session_id: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    limit: int = DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Raw event rows, newest first, capped at :data:`MAX_EVENT_ROWS`.

    The read behind "why is this node fragile?". It returns the rows themselves, not a
    fold over them: every number about the learner is recomputed elsewhere, and a caller
    showing receipts needs the evidence, not a second opinion about it.
    """

    capped = max(1, min(int(limit or DEFAULT_EVENT_LIMIT), MAX_EVENT_ROWS))
    rows = events_mod.query(
        store,
        node_id=node_id,
        session_id=session_id,
        goal_id=goal_id,
        kinds=[kind] if kind else None,
        since=since,
        limit=capped,
        newest_first=True,
    )
    return {
        "events": [event.model_dump() for event in rows],
        "count": len(rows),
        "limit": capped,
        "max_limit": MAX_EVENT_ROWS,
        "order": "newest_first",
    }


def _node_ids_for_goal(store: Store, goal_id: str) -> list[str]:
    goal_get(store, goal_id)
    return [
        row["node_id"]
        for row in store.query("SELECT node_id FROM node_goals WHERE goal_id = ?", (goal_id,))
    ]


def disputes(
    store: Store,
    *,
    goal_id: str | None = None,
    node_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """Typed disputes, oldest first. ``goal_id`` restricts to that goal's nodes.

    A dispute with no node (``misclick`` on nothing in particular) belongs to no goal, so
    it is *excluded* by a ``goal`` filter rather than silently attached to it.
    """

    sql = "SELECT dispute_id FROM disputes WHERE 1=1"
    params: list[Any] = []
    if node_id:
        sql += " AND node_id = ?"
        params.append(node_id)
    if goal_id:
        node_ids = _node_ids_for_goal(store, goal_id)
        if not node_ids:
            return {"disputes": [], "count": 0}
        sql += " AND node_id IN ({})".format(", ".join("?" for _ in node_ids))
        params.extend(node_ids)
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY created_at"
    rows = [disputes_mod.get(store, r["dispute_id"]) for r in store.query(sql, params)]
    return {"disputes": rows, "count": len(rows)}


def misconceptions(
    store: Store,
    *,
    goal_id: str | None = None,
    node_id: str | None = None,
    state: str | None = None,
) -> dict[str, Any]:
    """Misconception rows with their confirmation steps, oldest first."""

    sql = "SELECT * FROM misconceptions WHERE 1=1"
    params: list[Any] = []
    if node_id:
        sql += " AND node_id = ?"
        params.append(node_id)
    if goal_id:
        node_ids = _node_ids_for_goal(store, goal_id)
        if not node_ids:
            return {"misconceptions": [], "count": 0}
        sql += " AND node_id IN ({})".format(", ".join("?" for _ in node_ids))
        params.extend(node_ids)
    if state:
        sql += " AND state = ?"
        params.append(state)
    sql += " ORDER BY created_at"
    rows = [misc_mod.to_dict(row) for row in store.query(sql, params)]
    return {"misconceptions": rows, "count": len(rows)}


def passport(store: Store, goal_id: str | None = None) -> dict[str, Any]:
    """Every table about the learner, as one JSON object. See :mod:`.export`."""

    if goal_id:
        goal_get(store, goal_id)
    return export_mod.export_passport(store, goal_id)


def passport_zip(store: Store, goal_id: str | None = None) -> bytes:
    """The same passport as a zip archive. See :func:`.export.passport_zip`."""

    return export_mod.passport_zip(store, goal_id)


def log_session(
    store: Store,
    session_id: str,
    file: str | None = None,
    *,
    markdown: str | None = None,
    filename: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Copy a session md-log into the vault, from a path (the CLI) or text (HTTP/MCP).

    The two forms write the same file to the same place and append the same ``note``
    event; only where the bytes come from differs. A caller that has the markdown in hand
    — the gateway, at session end — should not have to invent a temporary file to reach
    the vault through a service that may not share its filesystem.
    """

    if (file is None) == (markdown is None):
        raise LearnerError("log_session takes exactly one of file or markdown")

    def _run() -> dict[str, Any]:
        session = session_get(store, session_id)
        if file is not None:
            source = Path(file)
            if not source.exists():
                raise LearnerError(f"no such file: {file}")
            text = source.read_text(encoding="utf-8")
        else:
            text = markdown or ""
        store.settings.ensure_dirs()
        goal = (session["goal_id"] or "session").replace("/", "-")
        day = session["started_at"][:10]
        target = store.settings.sessions_dir / (_log_filename(filename) or f"{day}-{goal}.md")
        target.write_text(text, encoding="utf-8", newline="\n")
        events_mod.append(
            store,
            kind="note",
            session_id=session_id,
            goal_id=session["goal_id"],
            payload={"log": str(target)},
        )
        return {"session_id": session_id, "log": str(target)}

    return _idem(
        store,
        idempotency_key,
        "log_session",
        {"session_id": session_id, "file": file, "markdown": markdown, "filename": filename},
        _run,
    )


# --------------------------------------------------------------------------- study tools
# CONTRACTS.md, *Study tools*. The rules live in `study.py`; these add idempotency and keep
# the derived views fresh, like every other mutation here.
def study_import(
    store: Store,
    goal_id: str,
    markdown: str,
    *,
    key_markdown: str | None = None,
    what: list[str] | None = None,
    source: str | None = None,
    author: str = "import",
    node: str | None = None,
    create_nodes: bool = True,
    dry_run: bool = False,
    pool: str = "practice",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = {
        "goal_id": goal_id,
        "pool": pool,
        "markdown": markdown,
        "key_markdown": key_markdown,
        "what": what,
        "source": source,
        "author": author,
        "node": node,
        "create_nodes": create_nodes,
        "dry_run": dry_run,
    }

    def _run() -> dict[str, Any]:
        report = study_mod.import_markdown(
            store,
            goal_id,
            markdown,
            key_markdown=key_markdown,
            what=what,
            source=source,
            author=author,
            node=node,
            create_nodes=create_nodes,
            dry_run=dry_run,
            pool=pool,
        )
        if not dry_run:
            views.refresh(store, goal_id)
        return report

    if dry_run:
        return _run()
    return _idem(store, idempotency_key, "study_import", body, _run)


def study_overview(store: Store, goal_id: str) -> dict[str, Any]:
    return study_mod.overview(store, goal_id)


def bank_pending(store: Store, goal_id: str, *, limit: int = 25) -> dict[str, Any]:
    return study_mod.bank_pending(store, goal_id, limit=limit)


def bank_review(store: Store, goal_id: str) -> dict[str, Any]:
    return study_mod.bank_review(store, goal_id)


def item_blind_check(
    store: Store,
    item: str,
    *,
    answer: str | None,
    by: str,
    ambiguous: bool = False,
    notes: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = study_mod.blind_check(
            store, item, answer=answer, by=by, ambiguous=ambiguous, notes=notes
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "item_blind_check",
        {"item": item, "answer": answer, "by": by, "ambiguous": ambiguous, "notes": notes},
        _run,
    )


def practice_next(
    store: Store, goal_id: str, *, n: int = 1, focus: str | None = None
) -> dict[str, Any]:
    return study_mod.practice_next(store, goal_id, n=n, focus=focus)


def practice_answer(
    store: Store,
    *,
    item_id: str,
    response: str | None,
    order: Any = None,
    confidence: int | None = None,
    idk: bool = False,
    session_id: str | None = None,
    channel: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = {
        "item_id": item_id,
        "response": response,
        "order": order,
        "confidence": confidence,
        "idk": bool(idk),
        "session_id": session_id,
        "channel": channel,
    }

    def _run() -> dict[str, Any]:
        return study_mod.practice_answer(
            store,
            item_id=item_id,
            response=response,
            order=order,
            confidence=confidence,
            idk=idk,
            session_id=session_id,
            channel=channel,
        )

    return _idem(store, idempotency_key, "practice_answer", body, _run)


# --------------------------------------------------------------------------- exam prep
# CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. The rules live in
# `blueprint.py` and `exam.py`.
def goal_blueprint(store: Store, goal_id: str) -> dict[str, Any]:
    return blueprint_mod.get(store, goal_id)


def goal_blueprint_set(
    store: Store,
    goal_id: str,
    spec: dict[str, Any],
    *,
    author: str = "cli",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = blueprint_mod.set_blueprint(store, goal_id, spec, author=author)
        views.refresh(store, goal_id)
        return result

    return _idem(
        store, idempotency_key, "goal_blueprint_set",
        {"goal_id": goal_id, "spec": spec, "author": author}, _run,
    )


def progress(store: Store, goal_id: str) -> dict[str, Any]:
    return exam_mod.progress(store, goal_id)


def mock_list(store: Store, goal_id: str) -> dict[str, Any]:
    return exam_mod.mock_list(store, goal_id)


def mock_start(
    store: Store,
    goal_id: str,
    *,
    n: int | None = None,
    minutes: int | None = None,
    channel: str = "web",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        return exam_mod.mock_start(store, goal_id, n=n, minutes=minutes, channel=channel)

    return _idem(
        store, idempotency_key, "mock_start",
        {"goal_id": goal_id, "n": n, "minutes": minutes, "channel": channel}, _run,
    )


def mock_show(store: Store, session_id: str) -> dict[str, Any]:
    return exam_mod.mock_show(store, session_id)


def mock_submit(
    store: Store,
    session_id: str,
    answers: list[dict[str, Any]] | None,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        return exam_mod.mock_submit(store, session_id, answers)

    return _idem(
        store, idempotency_key, "mock_submit",
        {"session_id": session_id, "answers": answers}, _run,
    )


def cards_add(
    store: Store,
    goal_id: str,
    cards: list[dict[str, Any]],
    *,
    author: str = "model",
    source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = study_mod.cards_add(store, goal_id, cards, author=author, source=source)
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "cards_add",
        {"goal_id": goal_id, "cards": cards, "author": author, "source": source},
        _run,
    )


def cards_next(store: Store, goal_id: str, *, n: int = 1) -> dict[str, Any]:
    return study_mod.cards_next(store, goal_id, n=n)


def card_reveal(store: Store, item: str) -> dict[str, Any]:
    return study_mod.card_reveal(store, item)


def card_review(
    store: Store,
    item: str,
    *,
    rating: str,
    session_id: str | None = None,
    channel: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = study_mod.card_review(
            store, item, rating=rating, session_id=session_id, channel=channel
        )
        views.write_state(store)
        return result

    return _idem(
        store,
        idempotency_key,
        "card_review",
        {"item": item, "rating": rating, "session_id": session_id, "channel": channel},
        _run,
    )


def cards_export(
    store: Store, goal_id: str, *, fmt: str = "tsv", include: str = "cards"
) -> dict[str, Any]:
    return study_mod.cards_export(store, goal_id, fmt=fmt, include=include)


def tables_list(store: Store, goal_id: str) -> dict[str, Any]:
    return study_mod.tables_list(store, goal_id)


def table_get(store: Store, table_id: str) -> dict[str, Any]:
    return study_mod.table_get(store, table_id)


def table_save(
    store: Store,
    goal_id: str,
    *,
    title: str,
    columns: list[str],
    rows: list[list[str]],
    node: str | None = None,
    source: str | None = None,
    author: str = "model",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    body = {
        "goal_id": goal_id,
        "title": title,
        "columns": columns,
        "rows": rows,
        "node": node,
        "source": source,
        "author": author,
    }

    def _run() -> dict[str, Any]:
        return study_mod.table_save(
            store,
            goal_id,
            title=title,
            columns=columns,
            rows=rows,
            node=node,
            source=source,
            author=author,
        )

    return _idem(store, idempotency_key, "table_save", body, _run)


def table_cards(
    store: Store, table_id: str, *, author: str = "table", idempotency_key: str | None = None
) -> dict[str, Any]:
    def _run() -> dict[str, Any]:
        result = study_mod.table_cards(store, table_id, author=author)
        views.write_state(store)
        return result

    return _idem(
        store, idempotency_key, "table_cards", {"table_id": table_id, "author": author}, _run
    )


def _log_filename(name: str | None) -> str | None:
    """A caller-supplied log filename, or ``None`` for the default ``<date>-<goal>.md``.

    One path segment, ``.md``, no traversal: the vault is a directory of session logs and
    a filename arriving over HTTP must not be able to name anything outside it.
    """

    if not name:
        return None
    candidate = str(name).strip()
    if not candidate or candidate != Path(candidate).name or candidate in (".", ".."):
        raise LearnerError(f"invalid log filename {name!r}: one path segment, no separators")
    if not candidate.endswith(".md"):
        raise LearnerError(f"invalid log filename {name!r}: must end in .md")
    return candidate
