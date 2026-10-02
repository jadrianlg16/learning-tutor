"""MCP server over the learner core — one tool per `learner` CLI verb.

Tool names are the CLI verbs with underscores (``learner_next``,
``learner_record_answer``, ...), exactly as CONTRACTS.md pins them, and every tool returns
a JSON object, so MCP clients get structured output rather than prose.

Four of them (``learner_events``, ``learner_disputes``, ``learner_misconceptions``,
``learner_log``) have no single CLI verb behind them: they are the reads and the log write
that ``learner-svc`` grew in Stage 2 so that nothing but the service opens ``events.db``.

Two backends, one tool surface
------------------------------
* ``LT_LEARNER_URL`` **unset** — the tools call ``learning_tutor.learner.api`` in process,
  against ``LT_DATA_DIR``. This is the agent-host setup (Claude Code or any MCP client): no
  service to run.
* ``LT_LEARNER_URL`` **set** (e.g. ``http://127.0.0.1:5034``) — the same tools proxy to
  ``learner-svc`` over HTTP. This is the Stage 2 setup, where the gateway container owns
  the volume and nothing else may touch the database directly.

The arguments and the returned JSON are identical either way; only the transport changes.

Study tools
-----------
Sixteen tools (CONTRACTS.md, *Study tools*) run the question bank, flashcards and tables
with no model inside them: ``learner_study_import`` reads markdown (by value or by a local
path this process reads, so the HTTP backend still receives text), questions are graded
against their key server-side, and a flashcard flip is self-report, never evidence. The
judgement parts — writing cards, blind-solving imported questions — are the caller's job.

Six more (CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*) aim the bank
at a fixed-date exam: ``learner_goal_blueprint`` (the official item count per concept),
``learner_progress``, and the sealed mock exam — ``learner_mock_start`` / ``_show`` /
``_submit`` / ``_list``. Practice gains ``focus`` and shuffled options: every served
question carries ``order``, and the letters are letters of *that* order, so the caller
passes ``order`` back with the answer.

The gate
--------
``LT_MCP_ENABLED`` — set it to ``0``/``false``/``no``/``off`` and every tool returns a
refusal object instead of touching the store (an environment variable is the whole
surface). It
defaults to **enabled**: registering the server is already the opt-in, and the variable
exists so a host can keep the registration and still stop the writes.

Run it
------
``uv run python -m learning_tutor.mcp_server`` (stdio, the default) or
``... --http --port 5036`` for the gateway. See ``.mcp.json.example`` at the repo root and
``docs/modules/learner-svc.md`` for the registration snippet.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import BaseModel

from .config import get_settings
from .learner import api
from .learner.models import Channel
from .learner.store import LearnerError, Store, open_store

SERVER_NAME = "learning-tutor-learner"
INSTRUCTIONS = (
    "The learner model for one-to-one teaching: an append-only event store, a concept "
    "graph, a versioned item bank, rule-based evidence and FSRS scheduling.\n\n"
    "Call learner_summary at the start of a session to read the learner's state, "
    "learner_next to pick what to ask, and learner_record_answer / "
    "learner_record_teach_back to record what happened. Never edit numbers yourself: "
    "every number in this model is recomputed by code from the events you record.\n\n"
    "Answers you grade yourself are evaluation_method=host_llm. They are recorded and "
    "they schedule the item, but they never count toward mastery; only blind_solver, "
    "rubric and human evaluations do.\n\n"
    "Study tools: learner_study_import reads questions, flashcards and tables out of "
    "markdown. Imported questions stay TEACHING_ONLY until learner_item_blind_check "
    "records a solve by someone other than their author who saw only the stem and options "
    "from learner_bank_pending; the key is compared server-side and never returned. "
    "learner_practice_next and learner_cards_next never include answers; "
    "learner_practice_answer grades against the key, and learner_card_review is the "
    "learner's self-rating, which schedules the card but is never evidence.\n\n"
    "Options are shown in a shuffled order: each practice or mock question carries `order`. "
    "Show the options with exactly the letters given, in the order given, and pass `order` "
    "back unchanged with the learner's letter. Never state or hint at a key before the "
    "learner has answered.\n\n"
    "Exam prep: learner_goal_blueprint stores the exam's item count per concept, and "
    "practice interleaves concepts by it (`focus` narrows it to one area or concept). "
    "learner_mock_start opens a sealed mock: show every question, collect every answer, and "
    "give no feedback of any kind until learner_mock_submit grades them all at once. "
    "learner_progress returns counts per area — read them back, never compute your own."
)
_FALSE = {"0", "false", "no", "off"}
#: 5033 is the gateway, 5034 learner-svc, 5035 render-svc — this is the next one free.
DEFAULT_HTTP_PORT = 5036


# --------------------------------------------------------------------------- the gate
def _mcp_enabled() -> bool:
    """``LT_MCP_ENABLED``: enabled unless explicitly switched off."""

    return os.getenv("LT_MCP_ENABLED", "1").strip().lower() not in _FALSE


def _mcp_disabled_response() -> dict[str, Any]:
    return {
        "available": False,
        "message": "MCP access to the learner model is disabled (LT_MCP_ENABLED).",
        "env_var": "LT_MCP_ENABLED",
        "how_to_enable": "set LT_MCP_ENABLED=1 in the server's environment and restart it",
    }


# --------------------------------------------------------------------------- backends
class LocalBackend:
    """Call the core in process, against ``LT_DATA_DIR``."""

    kind = "in-process"

    def __init__(self, data_dir: str | None = None) -> None:
        self.data_dir = data_dir

    def describe(self) -> dict[str, Any]:
        settings = get_settings(self.data_dir)
        return {"backend": self.kind, "data_dir": str(settings.data_dir)}

    def _store(self) -> Store:
        return open_store(get_settings(self.data_dir))

    def call(self, fn: Callable[[Store], Any]) -> Any:
        with self._store() as store:
            return fn(store)


class HttpBackend:
    """Proxy to ``learner-svc``. Same arguments, same JSON back."""

    kind = "http"

    def __init__(self, base_url: str, *, timeout: float = 30.0, client: Any = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._client = client

    def describe(self) -> dict[str, Any]:
        return {"backend": self.kind, "url": self.base_url}

    def send(self, method: str, path: str, **kwargs: Any) -> Any:
        """The raw response, errors already raised — for a text route whose headers carry
        part of the answer (``GET /v1/cards/{g}/export`` and its ``X-Card-Count``)."""

        import httpx

        if self._client is not None:
            response = self._client.request(method, path, **kwargs)
        else:
            with httpx.Client(base_url=self.base_url, timeout=self.timeout) as client:
                response = client.request(method, path, **kwargs)
        if response.status_code >= 400:
            try:
                detail = response.json().get("error", response.text)
            except ValueError:  # pragma: no cover - non-JSON error body
                detail = response.text
            raise ToolError(str(detail))
        return response

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.send(method, path, **kwargs)
        if response.headers.get("content-type", "").startswith("text/"):
            return response.text
        return response.json()


_backend: LocalBackend | HttpBackend | None = None


def backend() -> LocalBackend | HttpBackend:
    """The backend for this process: HTTP when ``LT_LEARNER_URL`` is set, else local."""

    global _backend
    if _backend is None:
        url = os.getenv("LT_LEARNER_URL", "").strip()
        _backend = HttpBackend(url) if url else LocalBackend()
    return _backend


def set_backend(value: LocalBackend | HttpBackend | None) -> None:
    """Override the backend (used by the tests to point at a TestClient)."""

    global _backend
    _backend = value


def _drop_none(data: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in data.items() if v is not None}


def _remote_goal(http: HttpBackend, goal: str | None) -> str:
    """The HTTP half of ``api.resolve_goal``: `goal` may be omitted with exactly one goal.

    Routes that take the goal in the *path* cannot let the service resolve it, so the
    proxy resolves it here — with the same rule and the same messages as the core, or the
    two backends would not be the same tool surface.
    """

    if goal:
        return goal
    goals = http.request("GET", "/v1/goals")["goals"]
    if len(goals) == 1:
        return goals[0]["goal_id"]
    if not goals:
        raise ToolError("no goals yet: run `learner goal add` first")
    raise ToolError("--goal is required: " + ", ".join(g["goal_id"] for g in goals))


def _run(local: Callable[[Store], Any], remote: Callable[[HttpBackend], Any]) -> Any:
    """Dispatch one tool to whichever backend this process is using."""

    if not _mcp_enabled():
        return _mcp_disabled_response()
    impl = backend()
    try:
        if isinstance(impl, HttpBackend):
            return remote(impl)
        return impl.call(local)
    except LearnerError as exc:
        raise ToolError(str(exc)) from exc


mcp = FastMCP(SERVER_NAME, instructions=INSTRUCTIONS)


# --------------------------------------------------------------------------- tools
@mcp.tool
def learner_goal_add(
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
    """Add a goal: concept, depth (recognize|explain|apply|analyze), purpose, deadline.

    `assessment` says how success is judged; `source_priority` is alignment|authority —
    which wins when the syllabus and the authoritative reference disagree.
    """

    body = _drop_none(
        {
            "goal_id": goal_id,
            "title": title,
            "depth": depth,
            "deadline": deadline,
            "minutes_per_session": minutes_per_session,
            "purpose": purpose,
            "assessment": assessment,
            "source_priority": source_priority,
            "idempotency_key": idempotency_key,
        }
    )
    return _run(
        lambda store: api.goal_add(
            store,
            goal_id=goal_id,
            title=title,
            depth=depth,
            deadline=deadline,
            minutes_per_session=minutes_per_session,
            purpose=purpose,
            assessment=assessment,
            source_priority=source_priority,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request("POST", "/v1/goals", json=body),
    )


@mcp.tool
def learner_graph_import(
    goal: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Import a concept graph: nodes [{id?, title, aliases[], domain?}] and edges
    [{from, to, type, provenance}]. Node ids are stable; aliases resolve names to ids."""

    payload = {"nodes": nodes, "edges": edges or []}
    return _run(
        lambda store: api.graph_import(
            store, goal, payload, idempotency_key=idempotency_key
        ),
        lambda http: http.request(
            "POST",
            f"/v1/graph/{goal}/import",
            json=_drop_none({**payload, "idempotency_key": idempotency_key}),
        ),
    )


@mcp.tool
def learner_graph_show(goal: str | None = None, format: str = "json") -> dict[str, Any]:
    """Show the goal's graph as json, or as mermaid coloured by node state."""

    def _local(store: Store) -> dict[str, Any]:
        goal_id = api.resolve_goal(store, goal)
        result = api.graph_show(store, goal_id, format)
        if format == "mermaid":
            return {"goal_id": goal_id, "format": "mermaid", "mermaid": result}
        return result

    return _run(
        _local,
        lambda http: http.request(
            "GET", f"/v1/graph/{_remote_goal(http, goal)}", params={"format": format}
        ),
    )


@mcp.tool
def learner_graph_revise(
    goal: str, ops: list[dict[str, Any]], idempotency_key: str | None = None
) -> dict[str, Any]:
    """Apply add / remove / split / merge ops. Evidence migrates with the nodes and the
    event table is never rewritten, so the export still replays to the same state."""

    return _run(
        lambda store: api.graph_revise(store, goal, ops, idempotency_key=idempotency_key),
        lambda http: http.request(
            "POST",
            f"/v1/graph/{goal}/revise",
            json=_drop_none({"ops": ops, "idempotency_key": idempotency_key}),
        ),
    )


@mcp.tool
def learner_item_add(
    node: str,
    spec: dict[str, Any],
    author: str = "model",
    item: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Add an item (status TEACHING_ONLY, writes no evidence until validated).

    spec: {stem, options[], answer, distractor_misconceptions{}, kind, components[],
    surface_form?}. Pass `item` to add a new version of an existing item — a reworded
    stem is a different question and goes back to TEACHING_ONLY.
    """

    return _run(
        lambda store: api.item_add(
            store, node, spec, author=author, item=item, idempotency_key=idempotency_key
        ),
        lambda http: http.request(
            "POST",
            "/v1/items",
            json=_drop_none(
                {
                    "node": node,
                    "spec": spec,
                    "author": author,
                    "item": item,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_item_validate(
    item: str,
    by: str,
    result: str,
    notes: str | None = None,
    evaluation_method: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record an independent check of an item; a pass promotes it to PRACTICE_EVIDENCE.

    `by` must differ from the author: the same model is never sole author, solver and
    judge. A validation stated as `host_llm` is recorded but never promotes.
    """

    return _run(
        lambda store: api.item_validate(
            store,
            item,
            by=by,
            result=result,
            notes=notes,
            evaluation_method=evaluation_method,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            f"/v1/items/{item}/validate",
            json=_drop_none(
                {
                    "by": by,
                    "result": result,
                    "notes": notes,
                    "evaluation_method": evaluation_method,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_item_promote(item: str, idempotency_key: str | None = None) -> dict[str, Any]:
    """Promote a validated item to MASTERY_ELIGIBLE and assign holdout membership.

    Rule-checked: enough recorded uses, at least one correct answer, no open ambiguity
    dispute. Holdout membership is a hash of the item id, so it never flips.
    """

    return _run(
        lambda store: api.item_promote(store, item, idempotency_key=idempotency_key),
        lambda http: http.request(
            "POST",
            f"/v1/items/{item}/promote",
            json=_drop_none({"idempotency_key": idempotency_key}),
        ),
    )


@mcp.tool
def learner_session_start(
    goal: str, channel: str = "claude-code", idempotency_key: str | None = None
) -> dict[str, Any]:
    """Start a session. Channel is claude-code | agent | telegram | web."""

    return _run(
        lambda store: api.session_start(
            store, goal_id=goal, channel=channel, idempotency_key=idempotency_key
        ),
        lambda http: http.request(
            "POST",
            "/v1/sessions",
            json=_drop_none(
                {
                    "goal_id": goal,
                    "channel": channel,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_session_end(
    session: str, summary: str | None = None, idempotency_key: str | None = None
) -> dict[str, Any]:
    """End a session, optionally with a one-line summary."""

    return _run(
        lambda store: api.session_end(
            store, session, summary, idempotency_key=idempotency_key
        ),
        lambda http: http.request(
            "POST",
            f"/v1/sessions/{session}/end",
            json=_drop_none({"summary": summary, "idempotency_key": idempotency_key}),
        ),
    )


@mcp.tool
def learner_next(
    goal: str | None = None,
    session: str | None = None,
    n: int = 1,
    mode: str = "auto",
) -> dict[str, Any]:
    """Pick what to ask next: probe | review | teach, or auto (review, then probe, then
    teach). Never returns a holdout item — holdouts are only served by
    learner_holdout_check, and that is what makes the mastery numbers measurable."""

    return _run(
        lambda store: api.next_(
            store, api.resolve_goal(store, goal), mode=mode, n=n, session_id=session
        ),
        lambda http: http.request(
            "GET",
            "/v1/next",
            params=_drop_none({"goal": goal, "mode": mode, "n": n, "session": session}),
        ),
    )


@mcp.tool
def learner_record_answer(
    item: str,
    correct: bool,
    session: str | None = None,
    response: str | None = None,
    confidence: int | None = None,
    idk: bool = False,
    assistance: int = 0,
    context: str = "in-session",
    channel: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    evaluation_method: str = "host_llm",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record one answer.

    assistance is the 0-6 hint ladder; a pass at 5 or 6 is recorded and never counts.
    context is in-session | delayed | transfer | probe.
    evaluation_method is host_llm | blind_solver | rubric | human — if you graded the
    answer yourself, that is host_llm, and it will not count toward mastery no matter how
    confident you are.
    """

    kwargs: dict[str, Any] = {
        "session_id": session,
        "item_id": item,
        "response": response,
        "correct": correct,
        "confidence": confidence,
        "idk": idk,
        "assistance_level": assistance,
        "context": context,
        "channel": channel,
        "evaluation_method": evaluation_method,
        "idempotency_key": idempotency_key,
    }
    if prompt_version:
        kwargs["prompt_version"] = prompt_version
    if grader_version:
        kwargs["grader_version"] = grader_version
    body = _drop_none(
        {
            "kind": "answer",
            "session_id": session,
            "item_id": item,
            "response": response,
            "correct": correct,
            "confidence": confidence,
            "idk": idk,
            "assistance_level": assistance,
            "context": context,
            "channel": channel,
            "prompt_version": prompt_version,
            "grader_version": grader_version,
            "evaluation_method": evaluation_method,
            "idempotency_key": idempotency_key,
        }
    )
    return _run(
        lambda store: api.record_answer(store, **kwargs),
        lambda http: http.request("POST", "/v1/events", json=body),
    )


@mcp.tool
def learner_record_teach_back(
    node: str,
    score: int,
    rubric_version: str,
    session: str | None = None,
    assistance: int = 0,
    notes: str | None = None,
    context: str = "in-session",
    prompt_version: str | None = None,
    grader_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record a teach-back, scored 0-3 against a versioned rubric (2 or more passes).

    A teach-back is judged against a frozen rubric, so it is recorded as
    evaluation_method=rubric and does count toward mastery.
    """

    kwargs: dict[str, Any] = {
        "session_id": session,
        "node": node,
        "score": score,
        "rubric_version": rubric_version,
        "assistance_level": assistance,
        "notes": notes,
        "context": context,
        "grader_version": grader_version,
        "idempotency_key": idempotency_key,
    }
    if prompt_version:
        kwargs["prompt_version"] = prompt_version
    body = _drop_none(
        {
            "kind": "teach_back",
            "session_id": session,
            "node": node,
            "score": score,
            "rubric_version": rubric_version,
            "assistance_level": assistance,
            "notes": notes,
            "context": context,
            "prompt_version": prompt_version,
            "grader_version": grader_version,
            "idempotency_key": idempotency_key,
        }
    )
    return _run(
        lambda store: api.record_teach_back(store, **kwargs),
        lambda http: http.request("POST", "/v1/events", json=body),
    )


@mcp.tool
def learner_misconception(
    action: str,
    node: str,
    claim: str,
    step: str | None = None,
    outcome: str | None = None,
    notes: str | None = None,
    session: str | None = None,
    prompt_version: str | None = None,
    grader_version: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """The misconception machine. action is suspect | confirm-step | resolve.

    A misconception is a hypothesis until all three confirmation steps hold, in order:
    reasoning, then a reworded prediction, then a counterexample. `step` and `outcome`
    (held | dropped) are required for confirm-step. Any dropped step ends the hypothesis.
    """

    versions = _drop_none(
        {"prompt_version": prompt_version, "grader_version": grader_version}
    )
    if action == "suspect":
        return _run(
            lambda store: api.misconception_suspect(
                store,
                node=node,
                claim=claim,
                session_id=session,
                idempotency_key=idempotency_key,
                **versions,
            ),
            lambda http: http.request(
                "POST",
                "/v1/misconceptions/suspect",
                json=_drop_none(
                    {
                        "node": node,
                        "claim": claim,
                        "session_id": session,
                        "idempotency_key": idempotency_key,
                        **versions,
                    }
                ),
            ),
        )
    if action == "confirm-step":
        if not step or not outcome:
            raise ToolError("confirm-step needs both step and outcome (held|dropped)")
        return _run(
            lambda store: api.misconception_confirm_step(
                store,
                node=node,
                claim=claim,
                step=step,
                outcome=outcome,
                notes=notes,
                idempotency_key=idempotency_key,
                **versions,
            ),
            lambda http: http.request(
                "POST",
                "/v1/misconceptions/confirm-step",
                json=_drop_none(
                    {
                        "node": node,
                        "claim": claim,
                        "step": step,
                        "outcome": outcome,
                        "notes": notes,
                        "idempotency_key": idempotency_key,
                        **versions,
                    }
                ),
            ),
        )
    if action == "resolve":
        return _run(
            lambda store: api.misconception_resolve(
                store,
                node=node,
                claim=claim,
                notes=notes,
                idempotency_key=idempotency_key,
                **versions,
            ),
            lambda http: http.request(
                "POST",
                "/v1/misconceptions/resolve",
                json=_drop_none(
                    {
                        "node": node,
                        "claim": claim,
                        "notes": notes,
                        "idempotency_key": idempotency_key,
                        **versions,
                    }
                ),
            ),
        )
    raise ToolError(f"unknown action {action!r}: suspect | confirm-step | resolve")


@mcp.tool
def learner_dispute_open(
    type: str,
    node: str | None = None,
    item: str | None = None,
    note: str | None = None,
    session: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Open a typed dispute: "I already know this" | "ambiguous question" | "misclick" |
    "not on my exam" | "this edge is wrong" | "test me instead".

    A dispute never grants mastery. "I already know this" schedules a two-item check, and
    the check still has to be passed.
    """

    return _run(
        lambda store: api.dispute_open(
            store,
            dispute_type=type,
            node=node,
            item_id=item,
            note=note,
            session_id=session,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            "/v1/disputes",
            json=_drop_none(
                {
                    "type": type,
                    "node": node,
                    "item_id": item,
                    "note": note,
                    "session_id": session,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_dispute_settle(
    dispute: str,
    outcome: str,
    evidence: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Settle a dispute (upheld | rejected) with the evidence that settled it. Node state
    is still recomputed from events; settling never sets it."""

    return _run(
        lambda store: api.dispute_settle(
            store,
            dispute,
            outcome=outcome,
            evidence=evidence,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            f"/v1/disputes/{dispute}/settle",
            json=_drop_none(
                {
                    "outcome": outcome,
                    "evidence": evidence,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_summary(goal: str | None = None, format: str = "md") -> dict[str, Any]:
    """Regenerate and return learner.md (format=md) or the same summary as JSON.

    Read this at the start of every session. It shows evidence, never probabilities:
    which concepts are known, which are fragile and why, active misconceptions, what is
    due, and any passes that were only self-graded.
    """

    def _local(store: Store) -> dict[str, Any]:
        goal_id = api.resolve_goal(store, goal)
        result = api.summary(store, goal_id, format)
        if format == "md":
            # `path` is a local filesystem detail; the HTTP backend has no equivalent, and
            # the tool surface has to be identical either way.
            return {"goal_id": goal_id, "format": "md", "markdown": result["markdown"]}
        return result

    def _remote(http: HttpBackend) -> dict[str, Any]:
        goal_id = _remote_goal(http, goal)
        result = http.request("GET", f"/v1/summary/{goal_id}", params={"format": format})
        if format == "md":
            return {"goal_id": goal_id, "format": "md", "markdown": result}
        return result

    return _run(_local, _remote)


@mcp.tool
def learner_holdout_check(goal: str | None = None) -> dict[str, Any]:
    """Serve the holdout items that are due — the only door holdouts are ever served by.

    Record the answers with context=delayed or context=transfer; these are what the
    false-mastery and 7-day holdout numbers are computed from.
    """

    return _run(
        lambda store: api.holdout_check(store, api.resolve_goal(store, goal) if goal else None),
        lambda http: http.request("GET", f"/v1/holdouts/{_remote_goal(http, goal)}/due"),
    )


@mcp.tool
def learner_metrics(goal: str | None = None) -> dict[str, Any]:
    """The three Stage 0 numbers: 7-day holdout success, false-mastery rate, item
    rejection rate — each with its denominator, and null rather than zero when empty."""

    return _run(
        lambda store: api.metrics(store, api.resolve_goal(store, goal) if goal else None),
        lambda http: http.request("GET", f"/v1/metrics/{_remote_goal(http, goal)}"),
    )


@mcp.tool
def learner_events(
    goal: str | None = None,
    node: str | None = None,
    session: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    limit: int = api.DEFAULT_EVENT_LIMIT,
) -> dict[str, Any]:
    """Raw event rows, newest first (capped at 1000) — the receipts behind a node's state.

    Read this to show *why*, never to recompute a number: the state a node is in is
    derived by code from exactly these rows.
    """

    return _run(
        lambda store: api.events(
            store,
            goal_id=goal,
            node_id=node,
            session_id=session,
            kind=kind,
            since=since,
            limit=limit,
        ),
        lambda http: http.request(
            "GET",
            "/v1/events",
            params=_drop_none(
                {
                    "goal": goal,
                    "node": node,
                    "session": session,
                    "kind": kind,
                    "since": since,
                    "limit": limit,
                }
            ),
        ),
    )


@mcp.tool
def learner_disputes(
    goal: str | None = None, status: str | None = None, node: str | None = None
) -> dict[str, Any]:
    """List typed disputes (open|settled). A dispute records a disagreement; it never
    grants mastery."""

    return _run(
        lambda store: api.disputes(store, goal_id=goal, node_id=node, status=status),
        lambda http: http.request(
            "GET",
            "/v1/disputes",
            params=_drop_none({"goal": goal, "status": status, "node": node}),
        ),
    )


@mcp.tool
def learner_misconceptions(
    goal: str | None = None, node: str | None = None, state: str | None = None
) -> dict[str, Any]:
    """List misconceptions with their confirmation steps.

    States: suspected|active|weakened|resolved|recurred. A suspicion is not a finding —
    it takes the three confirmation steps to become `active`.
    """

    return _run(
        lambda store: api.misconceptions(store, goal_id=goal, node_id=node, state=state),
        lambda http: http.request(
            "GET",
            "/v1/misconceptions",
            params=_drop_none({"goal": goal, "node": node, "state": state}),
        ),
    )


@mcp.tool
def learner_log(
    session: str,
    markdown: str,
    filename: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Write a session's markdown log into the vault (the CLI's `learner log`, by value).

    Returns the path it was written to. Prose only — every number in it must have been
    read back from this model, never invented.
    """

    def _remote(http: HttpBackend) -> dict[str, Any]:
        result = http.request(
            "POST",
            f"/v1/sessions/{session}/log",
            json=_drop_none(
                {
                    "markdown": markdown,
                    "filename": filename,
                    "idempotency_key": idempotency_key,
                }
            ),
        )
        # the route also returns `log_path`, the name the gateway contract uses. The tool
        # surface is the CLI's, and it has to be identical in both backends.
        return {k: v for k, v in result.items() if k != "log_path"}

    return _run(
        lambda store: api.log_session(
            store,
            session,
            markdown=markdown,
            filename=filename,
            idempotency_key=idempotency_key,
        ),
        _remote,
    )


@mcp.tool
def learner_export(out: str | None = None) -> dict[str, Any]:
    """Export the event table to events.jsonl. The database is the truth; this is a dump."""

    return _run(
        lambda store: api.export(store, out),
        lambda http: http.request("GET", "/v1/export", params=_drop_none({"out": out})),
    )


# --------------------------------------------------------------------------- study tools
# CONTRACTS.md, *Study tools*: no model runs inside any of these. Every goal-scoped tool
# takes `goal` optionally, resolved exactly like learner_summary's.
StudyKind = Literal["questions", "cards", "tables"]


def _read_text(path: str) -> str:
    """A local file's text, read by *this* process so the HTTP backend receives text."""

    file = Path(path).expanduser()
    if not file.is_file():
        raise ToolError(f"no such file: {path}")
    try:
        return file.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ToolError(f"{path} is not UTF-8 text: {exc}") from exc


def _local_table(store: Store, table: str, goal: str | None) -> dict[str, Any]:
    """``api.table_get``, refusing a table that belongs to another goal when one is named
    — the same answer the HTTP route (``/v1/tables/{g}/{t}``) gives."""

    found = api.table_get(store, table)
    if goal and found["goal_id"] != goal:
        api.goal_get(store, goal)  # an unknown goal is reported first, as over HTTP
        raise LearnerError(f"unknown table {table!r}")
    return found


def _remote_table_goal(http: HttpBackend, table: str, goal: str | None) -> str:
    """The goal a table belongs to, for routes that carry it in the path.

    The core finds a table by id alone; the service's routes are goal-scoped. With no
    `goal`, look the table up in each goal's list rather than make the caller name it.
    """

    if goal:
        return goal
    for row in http.request("GET", "/v1/goals")["goals"]:
        listed = http.request("GET", f"/v1/tables/{row['goal_id']}")["tables"]
        if any(t["table_id"] == table for t in listed):
            return row["goal_id"]
    raise ToolError(f"unknown table {table!r}")


@mcp.tool
def learner_goal_update(
    goal: str | None = None,
    deadline: str | None = None,
    minutes_per_session: int | None = None,
    sessions_per_week: int | None = None,
    title: str | None = None,
    purpose: str | None = None,
    assessment: str | None = None,
    depth: Literal["recognize", "explain", "apply", "analyze"] | None = None,
    source_priority: Literal["alignment", "authority"] | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Change a goal's contract: a moved exam date, a new session length or weekly cadence.

    Omitted (null) fields stay as they are. `deadline` is YYYY-MM-DD, or "" to clear it.
    The goal row is configuration, not evidence: it is updated in place and the change is
    also appended as a note event. Returns {goal (the whole row), changed: {field: {from,
    to}}} — `changed` is empty when nothing differed.
    """

    changes = {
        "deadline": deadline,
        "minutes_per_session": minutes_per_session,
        "sessions_per_week": sessions_per_week,
        "title": title,
        "purpose": purpose,
        "assessment": assessment,
        "depth": depth,
        "source_priority": source_priority,
    }
    return _run(
        lambda store: api.goal_update(
            store, api.resolve_goal(store, goal), idempotency_key=idempotency_key, **changes
        ),
        lambda http: http.request(
            "PATCH",
            f"/v1/goals/{_remote_goal(http, goal)}",
            json=_drop_none({**changes, "idempotency_key": idempotency_key}),
        ),
    )


@mcp.tool
def learner_study_import(
    goal: str | None = None,
    markdown: str | None = None,
    path: str | None = None,
    key_markdown: str | None = None,
    key_path: str | None = None,
    what: list[StudyKind] | None = None,
    source: str | None = None,
    author: str = "import",
    node: str | None = None,
    create_nodes: bool = True,
    dry_run: bool = False,
    pool: Literal["practice", "mock"] = "practice",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Import questions, flashcards and tables from markdown. Parsing only, no model.

    Give the text as `markdown`, or a local file as `path` (read by this server, UTF-8).
    A separate answer key goes in `key_markdown` / `key_path`; without one, keys are read
    from the same text. What is read: questions `N. stem … [tag]` with options `A) …` on
    the following lines; key rows `| N | tag | LETTER | explanation |`; concepts from
    headings `## <tag> <title>` (missing ones are created unless create_nodes=false;
    `node` files untagged blocks); flashcards from paragraphs opening with `**Term.**
    definition`; tables from GFM pipe tables. `what` limits it to questions|cards|tables
    (default all); `source` labels what is imported (default: the file name). Re-importing
    skips what is already there; dry_run=true reports without writing.

    pool="mock" seals the questions for mock exams (questions only): they are never served
    by practice, flashcards or the export until a mock has used them. Keep a sealed file out
    of the conversation — reading it would spoil the mock.

    Imported questions are TEACHING_ONLY: they count as evidence only after
    learner_item_blind_check. Returns {source, pool, dry_run, questions: {parsed, imported,
    skipped_existing, problems}, cards, tables, nodes_created}.
    """

    def _texts() -> tuple[str, str | None, str | None]:
        if markdown is not None and path:
            raise ToolError("pass markdown or path, not both")
        if markdown is None and not path:
            raise ToolError("nothing to import: pass markdown or path")
        if key_markdown is not None and key_path:
            raise ToolError("pass key_markdown or key_path, not both")
        text = markdown if markdown is not None else _read_text(path or "")
        key_text = key_markdown
        if key_text is None and key_path:
            key_text = _read_text(key_path)
        label = source or (Path(path).name if path else None)
        return text, key_text, label

    kinds = list(what) if what else None

    def _local(store: Store) -> dict[str, Any]:
        text, key_text, label = _texts()
        return api.study_import(
            store,
            api.resolve_goal(store, goal),
            text,
            key_markdown=key_text,
            what=kinds,
            source=label,
            author=author,
            node=node,
            create_nodes=create_nodes,
            dry_run=dry_run,
            pool=pool,
            idempotency_key=idempotency_key,
        )

    def _remote(http: HttpBackend) -> dict[str, Any]:
        text, key_text, label = _texts()
        return http.request(
            "POST",
            f"/v1/study/{_remote_goal(http, goal)}/import",
            json=_drop_none(
                {
                    "markdown": text,
                    "key_markdown": key_text,
                    "what": kinds,
                    "source": label,
                    "author": author,
                    "node": node,
                    "create_nodes": create_nodes,
                    "dry_run": dry_run,
                    "pool": pool,
                    "idempotency_key": idempotency_key,
                }
            ),
        )

    return _run(_local, _remote)


@mcp.tool
def learner_bank_pending(goal: str | None = None, limit: int = 25) -> dict[str, Any]:
    """Imported questions waiting for a blind check: stem and keyed options, NO key.

    This is exactly what a blind solver may see. Send the solver (a model or person other
    than the question's `author`) ONLY each item's stem and options — never the source
    file, an explanation or the key table — then record its pick with
    learner_item_blind_check. Returns {goal_id, pending (total), items: [{item_id,
    node_title, stem, options: [{key, text}], author}]}, at most `limit` items (max 200).
    """

    return _run(
        lambda store: api.bank_pending(store, api.resolve_goal(store, goal), limit=limit),
        lambda http: http.request(
            "GET", f"/v1/bank/{_remote_goal(http, goal)}/pending", params={"limit": limit}
        ),
    )


@mcp.tool
def learner_bank_review(goal: str | None = None) -> dict[str, Any]:
    """Questions whose latest blind check FAILED — WITH their keys, for a person to judge.

    Each row: stem, options, the stored `answer`, the solver's pick, whether the solver
    called it ambiguous, notes, validator and explanation. A failed question is held out of
    practice until a later blind check passes or a corrected version is added. Show these
    to the learner or whoever maintains the bank; never give them to a blind solver — the
    key is included.
    """

    return _run(
        lambda store: api.bank_review(store, api.resolve_goal(store, goal)),
        lambda http: http.request("GET", f"/v1/bank/{_remote_goal(http, goal)}/review"),
    )


@mcp.tool
def learner_item_blind_check(
    item: str,
    answer: str,
    by: str,
    ambiguous: bool = False,
    notes: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record a blind solve of an imported question; the key is compared server-side.

    `answer` is the solver's pick: an option key ("B") or the option's text. The solver
    must have seen only the stem and options from learner_bank_pending, never the key, and
    `by` names the solver — it must differ from the question's author. The check passes
    when the pick matches the key and ambiguous=false: the question becomes
    PRACTICE_EVIDENCE and answers to it start counting. Otherwise it fails, stays
    TEACHING_ONLY, is held out of practice and appears in learner_bank_review. The result
    never contains the key: {item_id, result: pass|fail, status, key_matched, ambiguous}.
    """

    return _run(
        lambda store: api.item_blind_check(
            store,
            item,
            answer=answer,
            by=by,
            ambiguous=ambiguous,
            notes=notes,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            f"/v1/items/{item}/blind-check",
            json=_drop_none(
                {
                    "answer": answer,
                    "by": by,
                    "ambiguous": ambiguous,
                    "notes": notes,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_practice_next(
    goal: str | None = None, n: int = 1, focus: str | None = None
) -> dict[str, Any]:
    """The next practice questions: due ones first, then new ones up to the daily cap,
    interleaved across concepts by the exam blueprint (equal shares without one).

    `focus` narrows practice to one blueprint area ("3") or one concept (a ref like "3.2",
    an alias, id or title); omit it for mixed practice.

    The options come SHUFFLED: `order[k]` is the stored option shown as the k-th letter.
    Show the options with exactly the letters and in the order given, then pass that
    question's `order` back unchanged to learner_practice_answer. Stem and options only —
    never the key or the explanation; never state or hint at a key before the learner has
    answered. Holdouts, sealed mock questions and questions that failed a blind check are
    never served. Returns {questions: [{item_id, item_version_id, node_id, node_title, ref,
    area, stem, options: [{key, text}], order, allow_idk, checked, reason: due|new,
    context}], counts (with counts.focus), done, note}.
    """

    return _run(
        lambda store: api.practice_next(
            store, api.resolve_goal(store, goal), n=n, focus=focus
        ),
        lambda http: http.request(
            "GET",
            f"/v1/practice/{_remote_goal(http, goal)}/next",
            params=_drop_none({"n": n, "focus": focus}),
        ),
    )


@mcp.tool
def learner_practice_answer(
    item: str,
    response: str | None = None,
    order: list[int] | None = None,
    confidence: int | None = None,
    idk: bool = False,
    session: str | None = None,
    channel: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Grade the learner's pick against the stored key, record it, schedule the question.

    `response` is the learner's own choice — the letter as it was shown ("B") or the
    option's text — never your guess; idk=true records "I don't know" (no response
    needed). `order` is the question's `order` from learner_practice_next, passed back
    unchanged: the letter is read against it (without it, today's shuffle is assumed).
    Graded server-side (evaluation_method=rubric): it counts toward mastery only when the
    question is `checked` (passed a blind check). The result carries `correct_answer` (a
    letter of the same shown order) and `explanation` as feedback for this attempt, plus
    node_state and the next due date.
    """

    return _run(
        lambda store: api.practice_answer(
            store,
            item_id=item,
            response=response,
            order=order,
            confidence=confidence,
            idk=idk,
            session_id=session,
            channel=channel,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            "/v1/practice/answer",
            json=_drop_none(
                {
                    "item_id": item,
                    "response": response,
                    "order": order,
                    "confidence": confidence,
                    "idk": idk,
                    "session_id": session,
                    "channel": channel,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_cards_add(
    cards: list[dict[str, Any]],
    goal: str | None = None,
    author: str = "model",
    source: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Add flashcards you wrote: [{front, back, node, source?}].

    `node` (required on every card) is the concept's title, alias or id. Write cards from
    the goal's sources, and set `author` to whoever wrote them. A card with the same front
    and back as an existing one is skipped. Returns {parsed, imported, skipped_existing}.
    """

    def _local(store: Store) -> dict[str, Any]:
        return api.cards_add(
            store,
            api.resolve_goal(store, goal),
            cards,
            author=author,
            source=source,
            idempotency_key=idempotency_key,
        )

    return _run(
        _local,
        lambda http: http.request(
            "POST",
            f"/v1/cards/{_remote_goal(http, goal)}",
            json=_drop_none(
                {
                    "cards": cards,
                    "author": author,
                    "source": source,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_cards_next(goal: str | None = None, n: int = 1) -> dict[str, Any]:
    """The next flashcards: due first, then new ones up to the daily cap. FRONTS ONLY.

    The back comes from learner_card_reveal, after the learner has tried to recall it.
    Returns {cards: [{item_id, node_id, node_title, front, reason: due|new}], counts, done}.
    """

    return _run(
        lambda store: api.cards_next(store, api.resolve_goal(store, goal), n=n),
        lambda http: http.request(
            "GET", f"/v1/cards/{_remote_goal(http, goal)}/next", params={"n": n}
        ),
    )


@mcp.tool
def learner_card_reveal(item: str) -> dict[str, Any]:
    """The back of a flashcard: {item_id, front, back, source}. Call it only after the
    learner has attempted to recall the answer from the front."""

    return _run(
        lambda store: api.card_reveal(store, item),
        lambda http: http.request("POST", f"/v1/cards/{item}/reveal"),
    )


@mcp.tool
def learner_card_review(
    item: str,
    rating: Literal["again", "hard", "good", "easy"],
    session: str | None = None,
    channel: str | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Record the learner's own rating of a flashcard flip: again | hard | good | easy.

    This is SELF-REPORT (event kind card_review, evaluation_method self_report). It
    schedules the card with FSRS and is never evidence: counts_toward_mastery is always
    false, whatever the rating. Pass the learner's rating, not your judgement of them.
    """

    return _run(
        lambda store: api.card_review(
            store,
            item,
            rating=rating,
            session_id=session,
            channel=channel,
            idempotency_key=idempotency_key,
        ),
        lambda http: http.request(
            "POST",
            f"/v1/cards/{item}/review",
            json=_drop_none(
                {
                    "rating": rating,
                    "session_id": session,
                    "channel": channel,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_cards_export(
    goal: str | None = None,
    format: Literal["tsv", "csv"] = "tsv",
    include: Literal["cards", "questions", "all"] = "cards",
) -> dict[str, Any]:
    """Flashcards (and/or questions, with the keyed option on the back) as Anki-importable
    text: front, back, tags per line. Returns {goal_id, format, include, count, text}.

    The text contains answers: it is for the learner's own deck, never for a blind solver.
    """

    def _remote(http: HttpBackend) -> dict[str, Any]:
        goal_id = _remote_goal(http, goal)
        response = http.send(
            "GET",
            f"/v1/cards/{goal_id}/export",
            params={"format": format, "include": include},
        )
        return {
            "goal_id": goal_id,
            "format": format,
            "include": include,
            "count": int(response.headers.get("X-Card-Count", "0")),
            "text": response.text,
        }

    return _run(
        lambda store: api.cards_export(
            store, api.resolve_goal(store, goal), fmt=format, include=include
        ),
        _remote,
    )


@mcp.tool
def learner_tables_list(goal: str | None = None) -> dict[str, Any]:
    """The goal's comparison/definition tables, without their rows: {tables: [{table_id,
    goal_id, title, node_id, node_title, columns, row_count, source, author, created_at}]}.
    """

    return _run(
        lambda store: api.tables_list(store, api.resolve_goal(store, goal)),
        lambda http: http.request("GET", f"/v1/tables/{_remote_goal(http, goal)}"),
    )


@mcp.tool
def learner_table_get(table: str, goal: str | None = None) -> dict[str, Any]:
    """One table with its rows (the tables_list fields plus `rows`). `goal` is optional;
    when given, a table from another goal is reported as unknown."""

    return _run(
        lambda store: _local_table(store, table, goal),
        lambda http: http.request(
            "GET", f"/v1/tables/{_remote_table_goal(http, table, goal)}/{table}"
        ),
    )


@mcp.tool
def learner_table_save(
    title: str,
    columns: list[str],
    rows: list[list[str]],
    goal: str | None = None,
    node: str | None = None,
    source: str | None = None,
    author: str = "model",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Save a comparison or definition table: `columns` (at least two) and `rows` (each
    exactly as long as `columns`).

    File it under a concept with `node` (title, alias or id) so it can become flashcards
    via learner_table_cards. Identical content is stored once: `new` is false when it
    already existed. Returns the table with its rows, plus `new`.
    """

    def _local(store: Store) -> dict[str, Any]:
        return api.table_save(
            store,
            api.resolve_goal(store, goal),
            title=title,
            columns=columns,
            rows=rows,
            node=node,
            source=source,
            author=author,
            idempotency_key=idempotency_key,
        )

    return _run(
        _local,
        lambda http: http.request(
            "POST",
            f"/v1/tables/{_remote_goal(http, goal)}",
            json=_drop_none(
                {
                    "title": title,
                    "columns": columns,
                    "rows": rows,
                    "node": node,
                    "source": source,
                    "author": author,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_table_cards(
    table: str,
    goal: str | None = None,
    author: str = "table",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Turn a table into flashcards. Two columns: one card per row, left → right. Three or
    more, with row labels in the first column: one card per cell, "<column> — <row
    label>" → cell. The table must be filed under a concept. Existing cards are skipped.
    Returns {parsed, imported, skipped_existing}."""

    def _local(store: Store) -> dict[str, Any]:
        _local_table(store, table, goal)
        return api.table_cards(
            store, table, author=author, idempotency_key=idempotency_key
        )

    return _run(
        _local,
        lambda http: http.request(
            "POST",
            f"/v1/tables/{_remote_table_goal(http, table, goal)}/{table}/cards",
            json=_drop_none({"author": author, "idempotency_key": idempotency_key}),
        ),
    )


# --------------------------------------------------------------------------- exam prep
# CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. Still no model: the
# blueprint is data copied from the exam's guide, and a mock is graded by key on submit.
class MockAnswer(BaseModel):
    """One answer of a mock: the learner's letter (as the mock showed it) or the option's
    text; `response` null or the question left out = "I don't know"."""

    item_id: str
    response: str | None = None
    order: list[int] | None = None
    confidence: int | None = None


def _blueprint_spec(blueprint: dict[str, Any]) -> dict[str, Any]:
    """The fields ``PUT /v1/goals/{g}/blueprint`` takes, and no others, so an object read
    back from learner_goal_blueprint (with its totals and shares) can be sent as is — and
    both backends see exactly the same spec."""

    spec = {key: blueprint.get(key) for key in ("exam", "source", "areas")}
    if not isinstance(spec["areas"], list) or not spec["areas"]:
        raise LearnerError("a blueprint needs a non-empty `areas` list")
    return spec


@mcp.tool
def learner_goal_blueprint(
    goal: str | None = None,
    blueprint: dict[str, Any] | None = None,
    author: str = "model",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """The exam blueprint: the official item count per concept. Without `blueprint`, read
    it (an error when the goal has none); with it, REPLACE the stored one.

    `blueprint` = {"exam", "source", "areas": [{"code": "1", "title", "subareas": [{"ref":
    "1.1", "title", "items": 12}, ...]}, ...]}, copied from the exam's official guide —
    never estimated. Every `ref` must name a live concept of the goal (its tag alias, id or
    exact title), or nothing is written. Practice interleaves concepts by these counts and
    mocks draw by them. Returns {goal_id, exam, source, total_items, updated_at, areas:
    [{code, title, exam_items, share, subareas: [{ref, title, node_id, node_title,
    exam_items, share}]}]}.
    """

    if blueprint is None:
        return _run(
            lambda store: api.goal_blueprint(store, api.resolve_goal(store, goal)),
            lambda http: http.request("GET", f"/v1/goals/{_remote_goal(http, goal)}/blueprint"),
        )

    def _local(store: Store) -> dict[str, Any]:
        return api.goal_blueprint_set(
            store,
            api.resolve_goal(store, goal),
            _blueprint_spec(blueprint),
            author=author,
            idempotency_key=idempotency_key,
        )

    def _remote(http: HttpBackend) -> dict[str, Any]:
        spec = _blueprint_spec(blueprint)
        return http.request(
            "PUT",
            f"/v1/goals/{_remote_goal(http, goal)}/blueprint",
            json=_drop_none({**spec, "author": author, "idempotency_key": idempotency_key}),
        )

    return _run(_local, _remote)


@mcp.tool
def learner_progress(goal: str | None = None) -> dict[str, Any]:
    """Progress against the blueprint — counts, not a score or a mastery estimate.

    Per area and concept: bank size, how many were seen, first tries and first-try correct
    with a 95% range (`low`..`high`, percent), attempts, what is due now; plus the
    blueprint-weighted `disciplinar` headline (null until every area has 5 first tries),
    daily `activity` for the last 28 days, the due `forecast` up to the deadline, and past
    mocks. Read the numbers back to the learner as they are; name the weakest heavy area
    and one next step — never invent or recompute a number.
    """

    return _run(
        lambda store: api.progress(store, api.resolve_goal(store, goal)),
        lambda http: http.request("GET", f"/v1/progress/{_remote_goal(http, goal)}"),
    )


@mcp.tool
def learner_mock_list(goal: str | None = None) -> dict[str, Any]:
    """Sealed mock exams: {sealed_available (checked sealed questions a new mock can use),
    sealed_unchecked, sealed_by_area, open (the mock in progress, or null), mocks (the
    submitted ones: n, answered, correct, minutes_used, per-area results)}."""

    return _run(
        lambda store: api.mock_list(store, api.resolve_goal(store, goal)),
        lambda http: http.request("GET", f"/v1/mocks/{_remote_goal(http, goal)}"),
    )


@mcp.tool
def learner_mock_start(
    goal: str | None = None,
    n: int | None = None,
    minutes: int | None = None,
    channel: Channel = "claude-code",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Open a SEALED mock exam: checked sealed questions drawn by the blueprint's weights,
    in a shuffled order with shuffled options. Only one mock may be open per goal.

    `n` defaults to every checked sealed question (at most 60); `minutes` to the exam's
    pace per question. Returns {session_id, status: "open", started_at, minutes, ends_at,
    n, questions: [{item_id, ref, area, node_title, stem, options: [{key, text}], order}]}.

    A mock gives NO feedback until submit: show every question with exactly the letters
    and order given, collect every answer, and do not say whether any answer is right, give
    hints, explain, or state a key while it is open. Then send all the answers at once with
    learner_mock_submit, passing each question's `order` back unchanged.
    """

    def _local(store: Store) -> dict[str, Any]:
        return api.mock_start(
            store,
            api.resolve_goal(store, goal),
            n=n,
            minutes=minutes,
            channel=channel,
            idempotency_key=idempotency_key,
        )

    return _run(
        _local,
        lambda http: http.request(
            "POST",
            f"/v1/mocks/{_remote_goal(http, goal)}",
            json=_drop_none(
                {
                    "n": n,
                    "minutes": minutes,
                    "channel": channel,
                    "idempotency_key": idempotency_key,
                }
            ),
        ),
    )


@mcp.tool
def learner_mock_show(session: str) -> dict[str, Any]:
    """A mock by its session id. Open: the same questions as learner_mock_start, no keys —
    still no feedback. Submitted: the graded result {status: "submitted", minutes_used,
    overtime, n, answered, correct, areas: [{code, title, n, correct, subareas}], items:
    [{..., your_answer, correct_answer, correct, explanation}]} — only now may keys and
    explanations be shown."""

    return _run(
        lambda store: api.mock_show(store, session),
        lambda http: http.request("GET", f"/v1/mock/{session}"),
    )


@mcp.tool
def learner_mock_submit(
    session: str,
    answers: list[MockAnswer],
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Grade every answer of an open mock at once, record them, and close it.

    `answers` = [{item_id, response, order?, confidence?}]: `response` is the learner's own
    letter as the mock showed it (or the option's text), never your guess; a question left
    out, or with a null response, is recorded as "I don't know". Nothing is written if any
    answer is invalid. A mock is submitted once; after that it is a read-only result.
    Returns the graded result, as learner_mock_show does for a submitted mock.
    """

    rows = [answer.model_dump() for answer in answers]
    return _run(
        lambda store: api.mock_submit(store, session, rows, idempotency_key=idempotency_key),
        lambda http: http.request(
            "POST",
            f"/v1/mock/{session}/submit",
            json=_drop_none({"answers": rows, "idempotency_key": idempotency_key}),
        ),
    )


# --------------------------------------------------------------------------- entry point
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="learning_tutor.mcp_server",
        description=(
            "MCP server over the learner model. stdio by default; --http serves "
            "streamable HTTP for the Stage 2 gateway. Set LT_LEARNER_URL to proxy to "
            "learner-svc instead of opening the database in this process."
        ),
    )
    parser.add_argument(
        "--http", action="store_true", help="Serve streamable HTTP instead of stdio."
    )
    parser.add_argument("--host", default=os.environ.get("LT_MCP_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("LT_MCP_PORT", DEFAULT_HTTP_PORT)),
    )
    parser.add_argument(
        "--data-dir", default=None, help="Override LT_DATA_DIR for this process."
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ["LT_DATA_DIR"] = args.data_dir
    if args.http:
        mcp.run(transport="http", host=args.host, port=args.port)
    else:
        mcp.run()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
