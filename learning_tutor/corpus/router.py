"""FastAPI router for the corpus module.

Mounted by the gateway (`:5033`) as one router in one process — the container split in
CONTRACTS.md puts gateway + llm + tutor + corpus in the same box, each liftable later.

Importing this module must not touch the network: the store opens lazily per request, and
the embedding backend is only contacted when a search or an ingest actually runs.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request

# starlette's UploadFile, not fastapi's subclass: request.form() yields the base class.
from starlette.datastructures import UploadFile

from . import cite as cite_mod
from . import ingest as ingest_mod
from . import research as research_mod
from . import roles as roles_mod
from . import sanitize, sources_md
from .embed import backend_info
from .search import search as search_corpus
from .store import (
    CorpusError,
    CorpusStore,
    delete_source,
    get_structure,
    goal_sources_dir,
    list_chunks,
    list_sources,
    open_store,
)

router = APIRouter(prefix="/corpus", tags=["corpus"])

MAX_UPLOAD_BYTES = 200 * 1024 * 1024


def get_store() -> Iterator[CorpusStore]:
    store = open_store()
    try:
        yield store
    finally:
        store.close()


StoreDep = Annotated[CorpusStore, Depends(get_store)]


def _fail(exc: CorpusError) -> HTTPException:
    return HTTPException(status_code=400, detail=str(exc))


@router.get("/health")
def health(store: StoreDep) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": store.schema_version(),
        "db": str(store.db_path),
        "embeddings": backend_info(),
        "roles": [role["role"] for role in roles_mod.describe()],
    }


async def _multipart_ingest(goal: str, store: CorpusStore, request: Request) -> dict[str, Any]:
    form = await request.form()
    upload = form.get("file")
    if not isinstance(upload, UploadFile):
        raise CorpusError("multipart ingest needs a 'file' part")
    role = str(form.get("role") or roles_mod.ALIGNMENT)
    title = form.get("title")
    kind = form.get("kind")

    target_dir = goal_sources_dir(goal, store.settings)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / Path(upload.filename or "upload.bin").name
    size = 0
    with target.open("wb") as handle:
        while True:
            block = await upload.read(1 << 20)
            if not block:
                break
            size += len(block)
            if size > MAX_UPLOAD_BYTES:
                handle.close()
                target.unlink(missing_ok=True)
                raise CorpusError("upload exceeds the size limit")
            handle.write(block)
    return ingest_mod.ingest_file(
        store,
        goal,
        target,
        role=role,
        kind=str(kind) if kind else None,
        title=str(title) if title else None,
    )


@router.post("/{goal}/ingest")
async def ingest(goal: str, store: StoreDep, request: Request) -> dict[str, Any]:
    """Multipart upload, or ``{"path": ...}`` for a file already under the goal's folder.

    One path, two content types: ``multipart/form-data`` uploads bytes, anything else is
    read as JSON with ``path``, ``text`` or ``url``.
    """

    try:
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            return await _multipart_ingest(goal, store, request)

        try:
            payload = await request.json()
        except Exception as exc:
            raise CorpusError("expected a multipart upload or a JSON body") from exc
        if not isinstance(payload, dict):
            raise CorpusError("JSON body must be an object")
        role = str(payload.get("role") or roles_mod.ALIGNMENT)
        if payload.get("url"):
            return ingest_mod.ingest_youtube(
                store,
                goal,
                str(payload["url"]),
                role=str(payload.get("role") or role),
                title=payload.get("title"),
            )
        if payload.get("text"):
            return ingest_mod.ingest_text(
                store,
                goal,
                str(payload["text"]),
                role=str(payload.get("role") or role),
                title=str(payload.get("title") or "untitled"),
                kind=str(payload.get("kind") or "md"),
            )
        if not payload.get("path"):
            raise CorpusError("send a multipart file, or a JSON body with path, text or url")
        return ingest_mod.ingest_file(
            store,
            goal,
            str(payload["path"]),
            role=str(payload.get("role") or role),
            kind=payload.get("kind"),
            title=payload.get("title"),
        )
    except CorpusError as exc:
        raise _fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{goal}/sources")
def sources(
    goal: str,
    store: StoreDep,
    role: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    try:
        if role:
            roles_mod.check_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    items = list_sources(store, goal, role=role)
    return {
        "goal_id": goal,
        "count": len(items),
        "sources": items,
        "roles": roles_mod.describe(),
    }


@router.delete("/{goal}/sources/{source_id}")
def remove_source(goal: str, source_id: str, store: StoreDep) -> dict[str, Any]:
    if not delete_source(store, source_id):
        raise HTTPException(status_code=404, detail=f"unknown source {source_id}")
    return {"goal_id": goal, "source_id": source_id, "deleted": True}


@router.get("/{goal}/structure")
def structure(
    goal: str,
    store: StoreDep,
    source_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    entries = get_structure(store, goal, source_id=source_id)
    return {
        "goal_id": goal,
        "count": len(entries),
        "structure": entries,
        "note": "the document's own outline; the plan phase uses it as a prior for the graph",
    }


@router.get("/{goal}/search")
def search_endpoint(
    goal: str,
    store: StoreDep,
    q: Annotated[str, Query(min_length=1)],
    role: Annotated[str | None, Query()] = None,
    source_id: Annotated[str | None, Query()] = None,
    k: Annotated[int, Query(ge=1, le=100)] = 8,
) -> dict[str, Any]:
    try:
        results, diagnostics = search_corpus(
            store, goal, q, k=k, role=role, source_id=source_id, with_diagnostics=True
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"goal_id": goal, "query": q, "count": len(results), "results": results,
            "diagnostics": diagnostics}


@router.post("/{goal}/cite")
def cite_endpoint(
    goal: str,
    store: StoreDep,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    claim = str(body.get("claim") or "")
    try:
        return cite_mod.cite_or_abstain(
            store,
            claim,
            goal,
            k=int(body.get("k") or 5),
            min_score=body.get("min_score"),
            role=body.get("role"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{goal}/conflicts")
def conflicts_endpoint(
    goal: str,
    store: StoreDep,
    topic: Annotated[str, Query(min_length=1)],
    k: Annotated[int, Query(ge=1, le=20)] = 5,
) -> dict[str, Any]:
    return cite_mod.conflicts(store, goal, topic, k=k)


@router.get("/{goal}/sources-md")
def read_sources_md(goal: str, store: StoreDep) -> dict[str, Any]:
    spec = sources_md.load(goal, store.settings)
    path = sources_md.path_for(goal, store.settings)
    return {
        "goal_id": goal,
        "path": str(path),
        "exists": path.exists(),
        "spec": spec.to_dict(),
        "markdown": path.read_text(encoding="utf-8") if path.exists() else sources_md.render(spec),
    }


@router.put("/{goal}/sources-md")
def write_sources_md(
    goal: str,
    store: StoreDep,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    if "markdown" in body:
        spec = sources_md.save_text(goal, str(body["markdown"]), store.settings)
    else:
        spec = sources_md.update(goal, body, store.settings)
    return {
        "goal_id": goal,
        "path": str(sources_md.path_for(goal, store.settings)),
        "spec": spec.to_dict(),
    }


@router.post("/{goal}/research/propose")
def research_propose(
    goal: str,
    store: StoreDep,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    try:
        return research_mod.propose(
            store, goal, str(body.get("topic") or ""), extra=body.get("sources")
        )
    except CorpusError as exc:
        raise _fail(exc) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{goal}/research/approve")
def research_approve(
    goal: str,
    store: StoreDep,
    body: Annotated[dict[str, Any], Body()],
) -> dict[str, Any]:
    list_id = str(body.get("list_id") or "")
    if not list_id:
        raise HTTPException(status_code=400, detail="list_id is required")
    try:
        return research_mod.approve(
            store, list_id, accept=body.get("accept"), sources=body.get("sources")
        )
    except CorpusError as exc:
        raise _fail(exc) from exc


@router.get("/{goal}/research")
def research_index(goal: str, store: StoreDep) -> dict[str, Any]:
    return {"goal_id": goal, "lists": research_mod.list_lists(store, goal)}


@router.get("/{goal}/context")
def context(
    goal: str,
    store: StoreDep,
    max_tokens: Annotated[int, Query(ge=100, le=1_000_000)] = 60_000,
    role: Annotated[str | None, Query()] = None,
    source_id: Annotated[str | None, Query()] = None,
) -> dict[str, Any]:
    """Whole-corpus-in-context for the plan phase, quoted and fenced, visibly truncated."""

    try:
        if role:
            roles_mod.check_role(role)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    chunks = list_chunks(store, goal, role=role, source_id=source_id)
    rendered = sanitize.render_for_context(chunks, max_tokens=max_tokens)
    return {
        "goal_id": goal,
        "max_tokens": max_tokens,
        "chunks_total": len(chunks),
        **rendered,
        "structure": get_structure(store, goal, source_id=source_id),
    }


def to_json(value: Any) -> str:  # pragma: no cover - convenience for the gateway's logs
    return json.dumps(value, ensure_ascii=False, default=str)
