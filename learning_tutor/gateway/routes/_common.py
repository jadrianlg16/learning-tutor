"""Shared helpers for the gateway routes. No decisions here — those are in ``tutor``."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from ...tutor import orchestrator as orch
from ...tutor.generate import corpus_context_for
from ..deps import Services
from ..errors import GatewayError, not_found
from ..state import Question


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso_now() -> str:
    return utcnow().isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def minutes_since(started_at: str | None) -> float:
    start = parse_ts(started_at)
    if start is None:
        return 0.0
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    return max(0.0, (utcnow() - start).total_seconds() / 60.0)


# --------------------------------------------------------------------- reads


def learner_goal(services: Services, goal_id: str) -> dict[str, Any]:
    """The goal row from learner-svc, merged with the contract fields it has no column for."""

    row = services.learner.get(f"/v1/goals/{goal_id}")
    if not isinstance(row, dict) or not row:
        raise not_found(f"unknown goal {goal_id!r}")
    contract = services.state.get(goal_id).contract
    merged = {**row, **{k: v for k, v in contract.items() if v not in (None, "", [])}}
    merged["phase"] = services.state.phase(goal_id)
    return merged


def graph_of(services: Services, goal_id: str) -> dict[str, Any]:
    graph = services.learner.get(f"/v1/graph/{goal_id}")
    return graph if isinstance(graph, dict) else {"nodes": [], "edges": []}


def node_states(graph: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(node.get("node_id")): dict(node.get("state") or {}) for node in graph.get("nodes") or []
    }


def state_names(graph: dict[str, Any]) -> dict[str, str]:
    return {
        node_id: str(state.get("state") or "unknown")
        for node_id, state in node_states(graph).items()
    }


def node_by_id(graph: dict[str, Any], node_id: str) -> dict[str, Any] | None:
    for node in graph.get("nodes") or []:
        if str(node.get("node_id")) == str(node_id):
            return node
    return None


def counts(graph: dict[str, Any]) -> dict[str, int]:
    tally = {"known": 0, "fragile": 0, "unknown": 0, "misconception": 0}
    for name in state_names(graph).values():
        if name in tally:
            tally[name] += 1
    return tally


# ----------------------------------------------------------------- questions


def question_payload(question: Question) -> dict[str, Any]:
    """The contract's ``Question``. The answer key is never in it."""

    return {
        "item_id": question.item_id,
        "item_version_id": question.item_version_id,
        "node_id": question.node_id,
        "node_title": question.node_title,
        "stem": question.stem,
        "options": orch.options_for_wire(question.options),
        "allow_idk": True,
        "ask_confidence": question.ask_confidence,
        "kind": question.kind,
        "assistance_level": question.assistance_level,
    }


def recorded_event(recorded: dict[str, Any], **fields: Any) -> dict[str, Any]:
    """CONTRACTS.md returns ``recorded: Event``; learner-svc returns a write receipt.

    The receipt carries ``event_id`` and everything derived from the write, but not the
    columns of the row it just wrote. This adds them back from what the gateway sent, so a
    caller gets the ``Event`` the contract names without any learner-svc key being lost or
    renamed. Nothing is recomputed here — every value is either learner-svc's or the exact
    value the gateway posted.
    """

    return {**fields, **(recorded if isinstance(recorded, dict) else {})}


def require_question(services: Services, goal_id: str, item_id: str) -> Question:
    question = services.state.question(goal_id, item_id)
    if question is None:
        raise GatewayError(
            f"no answer key on file for item {item_id!r}: the gateway grades against the "
            "key it stored when it authored the item, and will not take the client's word "
            "for whether an answer was right",
            code="unknown_item",
            status=409,
        )
    return question


def corpus_for(
    services: Services, goal_id: str, *, query: str | None = None, max_tokens: int = 6000
) -> tuple[str | None, list[dict[str, Any]], bool]:
    return corpus_context_for(
        services.settings, goal_id, query=query, max_tokens=max_tokens
    )


def cite_claim(services: Services, goal_id: str, claim: str) -> dict[str, Any]:
    """``cite_or_abstain`` for one claim, with the abstention *reason* kept.

    :func:`citations_for` throws the reason away because its callers only render the list.
    An interrupt has to *say* it found no source, so it needs the reason — and the
    difference between "this goal has no sources at all" and "the sources do not support
    this" is exactly the thing a learner should not have to guess at.
    """

    try:
        from ...corpus import open_store as open_corpus
        from ...corpus.cite import cite_or_abstain
        from ...corpus.store import list_sources
    except ImportError:  # pragma: no cover
        return {
            "status": "abstain",
            "reason": "the corpus module is not available",
            "corpus": False,
        }
    with open_corpus(services.settings) as store:
        if not list_sources(store, goal_id):
            return {
                "status": "abstain",
                "reason": "there are no sources on this goal",
                "corpus": False,
            }
        result = cite_or_abstain(store, claim, goal_id)
    return {**result, "corpus": True}


def citations_for(services: Services, goal_id: str, claim: str) -> list[dict[str, Any]]:
    """``cite_or_abstain`` for one teaching claim. Abstention returns no citations."""

    return list(cite_claim(services, goal_id, claim).get("citations") or [])


def leading_claim(markdown: str, fallback: str = "") -> str:
    """The first real sentence of generated prose, as the claim to look for a source for.

    ``cite_or_abstain`` scores the fraction of a claim's content words that appear in a
    chunk, so a whole answer as the claim abstains by length alone. The first sentence is
    the assertion the rest of the answer elaborates, which is the one worth a citation.
    """

    text = re.sub(r"[*_`>#\[\]]+", " ", markdown or "")
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sentence = re.split(r"(?<=[.!?])\s", stripped)[0].strip()
        if len(sentence.split()) >= 3:
            return sentence[:300]
    return fallback


# --------------------------------------------------------------- session log


def build_session_log(
    *,
    goal: dict[str, Any],
    session: dict[str, Any],
    prompt_version: str,
    rubric_version: str,
    minutes: int,
    steps: list[dict[str, Any]],
    summary_md: str,
    started: str,
    stopped: str,
    next_time: str,
) -> str:
    """The md-log, in the shape ``prompts/v1/session-log.md`` fixes.

    Every number in it was read back from a learner-svc response — the template's rule is
    "No numbers you did not read from the CLI", and the gateway holds none of its own.
    """

    day = (session.get("started_at") or iso_now())[:10]
    nodes_touched = sorted({str(s.get("node_title") or "") for s in steps if s.get("node_title")})
    front = [
        "---",
        f"goal: {goal.get('goal_id')}",
        f'goal_title: "{goal.get("title", "")}"',
        f"session: {session.get('session_id')}",
        f"date: {day}",
        f"channel: {session.get('channel', 'web')}",
        f"prompt_version: {prompt_version}",
        f"rubric_version: {rubric_version}",
        f"minutes: {minutes}",
        "nodes_touched: [" + ", ".join(nodes_touched) + "]",
        "tags: [learning-tutor, session]",
        "---",
        "",
        f"# {goal.get('title', goal.get('goal_id'))} — {day}",
        "",
        f"**Where we started:** {started}",
        f"**Where we stopped:** {stopped}",
        "",
        "---",
        "",
    ]

    body: list[str] = []
    step_no = 0
    for entry in steps:
        kind = entry.get("kind")
        if kind == "step":
            step_no += 1
            body += [
                f"## Step {step_no} — {entry.get('node_title')}",
                "",
                f"Strategy: {entry.get('strategy')}.",
                "",
                str(entry.get("markdown") or "").strip(),
                "",
            ]
            if entry.get("mermaid"):
                body += ["```mermaid", str(entry["mermaid"]).strip(), "```", ""]
            if entry.get("self_explanation_prompt"):
                body += [
                    f"**Self-explanation asked:** {entry['self_explanation_prompt']}",
                    "",
                ]
        elif kind == "checkpoint":
            verdict = entry.get("verdict") or ""
            body += [
                f"### Checkpoint — {entry.get('node_title')}",
                "",
                f"Item `{entry.get('item_id')}`, kind `{entry.get('item_kind')}`, "
                f"status `{entry.get('item_status')}`.",
                "",
                f"> {entry.get('stem')}",
                "",
                f"Answer given: **{entry.get('response')}** — "
                f"{'correct' if entry.get('correct') else 'not correct'}, assistance "
                f"**{entry.get('assistance_level')}**"
                + (
                    f", confidence {entry['confidence']}"
                    if entry.get("confidence") is not None
                    else ""
                )
                + f". {verdict}",
                "",
            ]
        elif kind == "teach_back":
            body += [
                f"## Teach-back — {entry.get('node_title')}",
                "",
                f"Score **{entry.get('score')}** (`{rubric_version}`), assistance "
                f"{entry.get('assistance_level', 0)}.",
                str(entry.get("feedback") or "").strip(),
                "",
            ]
        elif kind == "misconception":
            body += [
                f"**Misconception ({entry.get('state')}):** {entry.get('claim')} — "
                f"step `{entry.get('step')}` {entry.get('outcome')}.",
                "",
            ]

    tail = [
        "---",
        "",
        "## State after this session",
        "",
        summary_md.strip(),
        "",
        "---",
        "",
        "## Next time",
        "",
        next_time.strip(),
        "",
    ]
    return "\n".join(front + body + tail)


def write_session_log(
    services: Services,
    session: dict[str, Any],
    markdown: str,
    *,
    idempotency_key: str | None = None,
) -> str:
    """Hand it to learner-svc, which owns the vault, and return the path it wrote.

    ``POST /v1/sessions/{s}/log`` is the CLI's ``learner log`` by value: it writes
    ``<sessions_dir>/<date>-<goal>.md`` and appends the same ``note`` event. The gateway
    does not write the vault itself — the vault is on learner-svc's volume, and a gateway
    that wrote it directly would be right only when the two share a filesystem.
    """

    session_id = str(session.get("session_id") or "")
    posted = services.learner.post(
        f"/v1/sessions/{session_id}/log",
        {"markdown": markdown},
        idempotency_key=idempotency_key,
    )
    path = posted.get("log_path") or posted.get("log") if isinstance(posted, dict) else None
    if not path:
        raise GatewayError(
            "learner-svc accepted the session log but returned no path",
            code="bad_upstream_response",
            status=502,
        )
    return str(path)
