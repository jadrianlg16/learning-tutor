"""Grounding: uploads, the sources list, and the research pass.

The research pass proposes and records; it does **not** browse. corpus.md is explicit
about why: "the research pass is exactly where invented sources enter", so the web half
belongs to a harness with a real search tool, and this route only persists the proposal
and the learner's decision on it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile

from ...corpus import open_store as open_corpus
from ...corpus import roles as roles_mod
from ...corpus.store import CorpusError, list_sources
from ..deps import Services, get_services
from ..errors import GatewayError, bad_request
from ..models import ResearchApproveIn, ResearchIn
from ._common import learner_goal

router = APIRouter(prefix="/api", tags=["sources"])


def _corpus_error(exc: CorpusError) -> GatewayError:
    return GatewayError(str(exc), code="corpus_error", status=400)


@router.post("/goals/{goal_id}/sources")
async def upload_sources(
    goal_id: str,
    services: Services = Depends(get_services),
    files: Annotated[list[UploadFile], File(alias="file")] = None,  # noqa: B008
    role: Annotated[str, Form()] = roles_mod.ALIGNMENT,
) -> dict[str, Any]:
    """Multipart upload. The bytes land in ``LT_DATA_DIR/sources/<goal>/`` and are ingested."""

    learner_goal(services, goal_id)
    try:
        roles_mod.check_role(role)
    except ValueError as exc:
        raise bad_request(str(exc), code="unknown_role") from exc
    if not files:
        raise bad_request("send at least one file part named 'file'", code="no_file")

    target_dir = services.settings.sources_dir / goal_id
    target_dir.mkdir(parents=True, exist_ok=True)

    from ...corpus import ingest as ingest_mod

    ingested: list[dict[str, Any]] = []
    with open_corpus(services.settings) as store:
        for upload in files:
            name = (upload.filename or "upload").replace("/", "_").replace("\\", "_")
            destination = target_dir / name
            destination.write_bytes(await upload.read())
            try:
                ingested.append(
                    ingest_mod.ingest_file(store, goal_id, destination, role=role)
                )
            except CorpusError as exc:
                raise _corpus_error(exc) from exc
        sources = list_sources(store, goal_id)
    return {"sources": sources, "ingested": ingested}


@router.get("/goals/{goal_id}/sources")
def read_sources(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    from ...corpus import sources_md as sources_md_mod

    with open_corpus(services.settings) as store:
        sources = list_sources(store, goal_id)
    spec = sources_md_mod.load(goal_id, services.settings)
    path = services.settings.sources_dir / goal_id / "sources.md"
    return {
        "sources": sources,
        "sources_md": path.read_text(encoding="utf-8") if path.exists() else None,
        "sources_md_fields": spec.to_dict(),
    }


@router.post("/goals/{goal_id}/research")
def propose_research(
    goal_id: str,
    body: ResearchIn,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    from ...corpus import research as research_mod

    goal = learner_goal(services, goal_id)
    topic = body.topic or goal.get("title") or goal_id
    extra = (
        [{"title": body.guidelines, "why": "guideline from the learner"}]
        if body.guidelines
        else None
    )
    with open_corpus(services.settings) as store:
        try:
            proposal = research_mod.propose(store, goal_id, topic, extra=extra)
        except CorpusError as exc:
            raise _corpus_error(exc) from exc
    return {
        "proposal_id": proposal["list_id"],
        "sources": [
            {
                "title": entry.get("title"),
                "url": entry.get("url"),
                "role": entry.get("role"),
                "why": entry.get("why"),
            }
            for entry in proposal.get("sources") or []
        ],
        "search_briefs": proposal.get("search_briefs") or [],
        "note": proposal.get("note"),
    }


@router.post("/goals/{goal_id}/research/approve")
def approve_research(
    goal_id: str,
    body: ResearchApproveIn,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    from ...corpus import research as research_mod

    with open_corpus(services.settings) as store:
        try:
            result = research_mod.approve(
                store, body.proposal_id, accept=body.accept or None
            )
        except CorpusError as exc:
            raise _corpus_error(exc) from exc
    return {"approved": len(result.get("approved") or []), "detail": result}
