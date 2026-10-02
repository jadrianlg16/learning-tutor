"""The research pass: propose a source list, get it approved, then ingest.

This module does **not** do web research. IDEA.md is explicit that the research pass is a
turn in the agent host (for example web search in Claude Code) — the place invented
sources enter is exactly the place a service should not be inventing anything. So the corpus
module owns only the durable half:

* :func:`propose` — build a candidate list from the goal's ``sources.md`` (trusted sources,
  preferred textbooks, notation, depth, language) plus the topic, and persist it as
  ``proposed``. Every entry carries ``origin``, so an entry the harness found on the web is
  visibly different from one the learner already named as trusted.
* :func:`approve` / :func:`reject` — the learner's decision, persisted.
* :func:`mark_ingested` — link an approved entry to the ``source_id`` it became.

Nothing is ingested until a list is ``approved``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from . import roles, sources_md
from .store import CorpusError, CorpusStore, loads, utcnow

ORIGIN_SOURCES_MD = "sources_md"
ORIGIN_HARNESS = "harness_research"
ORIGIN_LEARNER = "learner"


def _lid() -> str:
    return f"rl_{uuid.uuid4().hex[:12]}"


def _candidate(
    title: str,
    *,
    role: str,
    origin: str,
    why: str,
    url: str = "",
    query: str = "",
) -> dict[str, Any]:
    return {
        "title": title,
        "url": url,
        "query": query,
        "role": roles.check_role(role),
        "proves": roles.proves(role),
        "origin": origin,
        "why": why,
        "status": "proposed",
        "source_id": None,
    }


def propose(
    store: CorpusStore,
    goal_id: str,
    topic: str,
    *,
    extra: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A proposed source list for ``topic``, built from the goal's ``sources.md``.

    The returned ``search_briefs`` are what the harness should actually go and research:
    this module writes down what to look for and under which constraints, and the harness
    comes back with results to add via ``extra`` or a second ``propose`` call.
    """

    topic = (topic or "").strip()
    if not topic:
        raise CorpusError("propose needs a topic")

    spec = sources_md.load(goal_id, store.settings)
    candidates: list[dict[str, Any]] = []

    for textbook in spec.preferred_textbooks:
        candidates.append(
            _candidate(
                textbook,
                role=roles.AUTHORITY,
                origin=ORIGIN_SOURCES_MD,
                why=f"preferred textbook in sources.md; supports correctness on {topic}",
                query=f"{textbook} {topic}",
            )
        )
    for trusted in spec.trusted:
        if spec.is_banned(trusted):
            continue
        looks_like_url = trusted.startswith("http://") or trusted.startswith("https://")
        candidates.append(
            _candidate(
                trusted,
                role=roles.AUTHORITY,
                origin=ORIGIN_SOURCES_MD,
                why=f"trusted source in sources.md; scoped to {topic}",
                url=trusted if looks_like_url else "",
                query="" if looks_like_url else f"{trusted} {topic}",
            )
        )

    for entry in extra or []:
        candidate = _candidate(
            str(entry.get("title") or entry.get("url") or "untitled"),
            role=str(entry.get("role") or roles.AUTHORITY),
            origin=str(entry.get("origin") or ORIGIN_HARNESS),
            why=str(entry.get("why") or "proposed by the research pass"),
            url=str(entry.get("url") or ""),
            query=str(entry.get("query") or ""),
        )
        # Check the title *and* the url: a banned name can arrive under a fresh domain.
        if spec.is_banned(candidate["title"]) or (
            candidate["url"] and spec.is_banned(candidate["url"])
        ):
            candidate["status"] = "banned"
            candidate["why"] = "matches a banned source in sources.md"
        candidates.append(candidate)

    briefs = [
        {
            "topic": topic,
            "instruction": (
                f"Find {spec.depth or 'explain'}-level material on {topic}"
                f"{' in ' + spec.language if spec.language else ''}."
            ),
            "notation": spec.notation,
            "avoid": spec.banned,
            "prefer": spec.preferred_textbooks + spec.trusted,
            "run_by": "the harness or the gateway, never this module",
        }
    ]

    list_id = _lid()
    payload = {
        "sources": candidates,
        "search_briefs": briefs,
        "sources_md": spec.to_dict(),
    }
    store.insert(
        "research_lists",
        {
            "list_id": list_id,
            "goal_id": goal_id,
            "topic": topic,
            "status": "proposed",
            "created_at": utcnow(),
            "decided_at": None,
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return {
        "list_id": list_id,
        "goal_id": goal_id,
        "topic": topic,
        "status": "proposed",
        "sources": candidates,
        "search_briefs": briefs,
        "note": (
            "Nothing here is ingested. Approve the list first; web research itself is done by "
            "the harness, not by this module."
        ),
    }


def get_list(store: CorpusStore, list_id: str) -> dict[str, Any]:
    row = store.one("SELECT * FROM research_lists WHERE list_id = ?", (list_id,))
    if not row:
        raise CorpusError(f"unknown research list {list_id!r}")
    data = dict(row)
    payload = loads(data.pop("payload", None), {})
    data.update(payload)
    return data


def list_lists(store: CorpusStore, goal_id: str) -> list[dict[str, Any]]:
    rows = store.query(
        "SELECT list_id, goal_id, topic, status, created_at, decided_at "
        "FROM research_lists WHERE goal_id = ? ORDER BY created_at DESC",
        (goal_id,),
    )
    return [dict(row) for row in rows]


def approve(
    store: CorpusStore,
    list_id: str,
    *,
    accept: list[str] | None = None,
    sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist the learner's approval.

    ``accept`` names the titles (or urls) to keep; anything not named is marked ``rejected``.
    ``sources`` replaces the list wholesale, for a learner who edited it. Omitting both
    approves every proposed entry.
    """

    current = get_list(store, list_id)
    entries: list[dict[str, Any]] = list(sources) if sources is not None else list(
        current.get("sources") or []
    )
    if accept is not None:
        wanted = {value.strip().lower() for value in accept}
        for entry in entries:
            keys = {
                str(entry.get("title") or "").strip().lower(),
                str(entry.get("url") or "").strip().lower(),
            }
            entry["status"] = "approved" if keys & wanted else "rejected"
    else:
        for entry in entries:
            if entry.get("status") != "banned":
                entry["status"] = "approved"

    payload = {
        "sources": entries,
        "search_briefs": current.get("search_briefs") or [],
        "sources_md": current.get("sources_md") or {},
    }
    approved = [entry for entry in entries if entry.get("status") == "approved"]
    store.update(
        "research_lists",
        {"list_id": list_id},
        {
            "status": "approved" if approved else "rejected",
            "decided_at": utcnow(),
            "payload": json.dumps(payload, ensure_ascii=False),
        },
    )
    return {
        "list_id": list_id,
        "goal_id": current["goal_id"],
        "topic": current["topic"],
        "status": "approved" if approved else "rejected",
        "approved": approved,
        "rejected": [entry for entry in entries if entry.get("status") != "approved"],
    }


def reject(store: CorpusStore, list_id: str) -> dict[str, Any]:
    return approve(store, list_id, accept=[])


def mark_ingested(
    store: CorpusStore, list_id: str, *, title_or_url: str, source_id: str
) -> dict[str, Any]:
    """Record which approved entry became which ingested source."""

    current = get_list(store, list_id)
    entries = list(current.get("sources") or [])
    needle = title_or_url.strip().lower()
    hit = False
    for entry in entries:
        keys = {
            str(entry.get("title") or "").strip().lower(),
            str(entry.get("url") or "").strip().lower(),
        }
        if needle in keys:
            entry["status"] = "ingested"
            entry["source_id"] = source_id
            hit = True
    if not hit:
        raise CorpusError(f"{title_or_url!r} is not in research list {list_id!r}")
    store.update(
        "research_lists",
        {"list_id": list_id},
        {
            "payload": json.dumps(
                {
                    "sources": entries,
                    "search_briefs": current.get("search_briefs") or [],
                    "sources_md": current.get("sources_md") or {},
                },
                ensure_ascii=False,
            )
        },
    )
    return {"list_id": list_id, "title_or_url": title_or_url, "source_id": source_id}
