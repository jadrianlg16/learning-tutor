"""Study tools on the gateway: question bank, flashcards, tables — no model involved.

CONTRACTS.md, *Study tools* → *Gateway additions*. Every route is a pass-through to
learner-svc, which owns the item bank, the keys and the scheduler; the gateway adds only
what learner-svc cannot know: which markdown files sit in the goal's sources folder, and
that the caller is the web UI (``channel: web``).

Keys stay server-side exactly as for the tutor's own checkpoints: ``practice/next`` and
``cards/next`` carry no answers, ``practice/answer`` is graded by learner-svc against the
stored key, and a card's back arrives only from ``cards/reveal``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from ..deps import IdempotencyHeader, Services, get_services
from ..errors import bad_request, not_found
from ..models import CardRating, CardRef, MockStart, MockSubmit, PracticeAnswer, StudyImportPath
from ._common import learner_goal

router = APIRouter(prefix="/api", tags=["study"])

#: who wrote what a browser upload imports — never the name of a model, so any model may
#: blind-check it later (the validator must differ from the author)
WEB_IMPORT_AUTHOR = "import:web"
MAX_IMPORTABLE = 500
_TRUE = {"1", "true", "yes", "on"}
#: a folder holding this file is sealed: mock-exam questions, imported by the harness with
#: ``--pool mock``. The web importer neither lists nor reads it — importing one of those
#: files as practice would show the learner the mock before the mock.
SEALED_MARKER = "SEALED"


def _sealed(path: Path, root: Path) -> bool:
    return any(
        (parent / SEALED_MARKER).is_file()
        for parent in path.parents
        if parent == root or parent.is_relative_to(root)
    )


def goal_source_dirs(services: Services, goal_id: str) -> list[Path]:
    """The goal's sources folders that exist.

    ``sources/<goal_id>/`` is the one the gateway writes uploads to. The underscore
    spelling (``my_goal`` for ``my-goal``) is accepted too, because Stage 0 sessions
    filled folders by hand under that name and moving a learner's files is not this
    route's business.
    """

    root = services.settings.sources_dir
    seen: list[Path] = []
    for name in (goal_id, goal_id.replace("-", "_")):
        folder = root / name
        if folder.is_dir() and folder.resolve() not in [p.resolve() for p in seen]:
            seen.append(folder)
    return seen


def importable(services: Services, goal_id: str) -> list[dict[str, Any]]:
    root = services.settings.sources_dir
    out: list[dict[str, Any]] = []
    for folder in goal_source_dirs(services, goal_id):
        for path in sorted(folder.rglob("*.md")):
            if path.is_file() and not _sealed(path, root):
                out.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "name": path.name,
                        "bytes": path.stat().st_size,
                    }
                )
    return out[:MAX_IMPORTABLE]


def _read_importable(services: Services, goal_id: str, rel: str) -> str:
    """A markdown file named by ``importable``'s ``path`` — and nothing outside the goal."""

    root = services.settings.sources_dir.resolve()
    target = (root / rel).resolve()
    allowed = [d.resolve() for d in goal_source_dirs(services, goal_id)]
    if not any(target.is_relative_to(d) for d in allowed):
        raise bad_request(f"{rel!r} is not in this goal's sources folder", code="bad_path")
    if target.suffix.lower() != ".md":
        raise bad_request(f"{rel!r} is not a markdown file", code="bad_path")
    if not target.is_file():
        raise not_found(f"no such file {rel!r}")
    if _sealed(target, root):
        raise bad_request(
            f"{rel!r} is in a sealed folder (mock-exam questions): import it with "
            "`learner study import --pool mock`, not from the browser",
            code="sealed",
        )
    return target.read_text(encoding="utf-8", errors="replace")


@router.get("/goals/{goal_id}/study")
def overview(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    learner_goal(services, goal_id)
    data = services.learner.get(f"/v1/study/{goal_id}")
    data["importable"] = importable(services, goal_id)
    return data


@router.post("/goals/{goal_id}/study/import")
async def study_import(
    goal_id: str,
    request: Request,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    """Multipart (``file``, optional ``key_file``, ``what``…, ``dry_run``) or JSON paths."""

    await run_in_threadpool(learner_goal, services, goal_id)
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise bad_request("send the markdown as a file part named 'file'", code="no_file")
        markdown = (await upload.read()).decode("utf-8", errors="replace")
        key_upload = form.get("key_file")
        key_markdown = None
        if key_upload is not None and hasattr(key_upload, "read"):
            raw = await key_upload.read()
            key_markdown = raw.decode("utf-8", errors="replace") if raw else None
        what = [str(w) for w in form.getlist("what") if str(w)] or None
        dry_run = str(form.get("dry_run") or "0").strip().lower() in _TRUE
        node = str(form.get("node") or "") or None
        source = getattr(upload, "filename", None) or "upload.md"
    else:
        try:
            body = StudyImportPath.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            raise bad_request(f"expected multipart or JSON {{path, key_path?}}: {exc}") from exc
        markdown = _read_importable(services, goal_id, body.path)
        key_markdown = _read_importable(services, goal_id, body.key_path) if body.key_path else None
        what = list(body.what) if body.what else None
        dry_run = body.dry_run
        node = body.node
        source = body.path

    payload = {
        "markdown": markdown,
        "key_markdown": key_markdown,
        "what": what,
        "source": source,
        "author": WEB_IMPORT_AUTHOR,
        "node": node,
        "dry_run": dry_run,
    }
    return await run_in_threadpool(
        lambda: services.learner.post(
            f"/v1/study/{goal_id}/import", payload, idempotency_key=None if dry_run else idem
        )
    )


# --------------------------------------------------------------------------- practice
@router.get("/goals/{goal_id}/practice/next")
def practice_next(
    goal_id: str,
    n: int = 1,
    focus: str | None = None,
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    params: dict[str, Any] = {"n": n}
    if focus:
        params["focus"] = focus
    return services.learner.get(f"/v1/practice/{goal_id}/next", params)


@router.post("/goals/{goal_id}/practice/answer")
def practice_answer(
    goal_id: str,
    body: PracticeAnswer,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    learner_goal(services, goal_id)
    return services.learner.post(
        "/v1/practice/answer",
        {
            "item_id": body.item_id,
            "response": body.response,
            "order": body.order,
            "confidence": body.confidence,
            "idk": body.idk,
            "channel": "web",
        },
        idempotency_key=idem,
    )


# --------------------------------------------------------------------------- exam prep
# CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams* → *Gateway additions*.
@router.get("/goals/{goal_id}/blueprint")
def blueprint(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.learner.get(f"/v1/goals/{goal_id}/blueprint")


@router.get("/goals/{goal_id}/progress")
def progress(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.learner.get(f"/v1/progress/{goal_id}")


@router.get("/goals/{goal_id}/mocks")
def mocks(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.learner.get(f"/v1/mocks/{goal_id}")


@router.post("/goals/{goal_id}/mocks")
def mock_start(
    goal_id: str,
    body: MockStart,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return services.learner.post(
        f"/v1/mocks/{goal_id}",
        {"n": body.n, "minutes": body.minutes, "channel": "web"},
        idempotency_key=idem,
    )


def _goal_mock(services: Services, goal_id: str, session_id: str) -> dict[str, Any]:
    data = services.learner.get(f"/v1/mock/{session_id}")
    if data.get("goal_id") != goal_id:
        raise not_found(f"unknown session {session_id!r}")
    return data


@router.get("/goals/{goal_id}/mocks/{session_id}")
def mock_show(
    goal_id: str, session_id: str, services: Services = Depends(get_services)
) -> dict[str, Any]:
    return _goal_mock(services, goal_id, session_id)


@router.post("/goals/{goal_id}/mocks/{session_id}/submit")
def mock_submit(
    goal_id: str,
    session_id: str,
    body: MockSubmit,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    _goal_mock(services, goal_id, session_id)
    return services.learner.post(
        f"/v1/mock/{session_id}/submit", {"answers": body.answers}, idempotency_key=idem
    )


@router.get("/goals/{goal_id}/practice/review")
def practice_review(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.learner.get(f"/v1/bank/{goal_id}/review")


# --------------------------------------------------------------------------- cards
@router.get("/goals/{goal_id}/cards/next")
def cards_next(
    goal_id: str, n: int = 1, services: Services = Depends(get_services)
) -> dict[str, Any]:
    return services.learner.get(f"/v1/cards/{goal_id}/next", {"n": n})


@router.post("/goals/{goal_id}/cards/reveal")
def card_reveal(
    goal_id: str, body: CardRef, services: Services = Depends(get_services)
) -> dict[str, Any]:
    return services.learner.post(f"/v1/cards/{body.item_id}/reveal", {})


@router.post("/goals/{goal_id}/cards/review")
def card_review(
    goal_id: str,
    body: CardRating,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return services.learner.post(
        f"/v1/cards/{body.item_id}/review",
        {"rating": body.rating, "channel": "web"},
        idempotency_key=idem,
    )


@router.get("/goals/{goal_id}/cards/export")
def cards_export(
    goal_id: str,
    format: str = "tsv",
    include: str = "cards",
    services: Services = Depends(get_services),
) -> Response:
    upstream = services.learner.response(
        f"/v1/cards/{goal_id}/export", {"format": format, "include": include}
    )
    headers = {
        key: upstream.headers[key]
        for key in ("content-disposition", "x-card-count")
        if key in upstream.headers
    }
    return Response(
        content=upstream.content,
        media_type=upstream.headers.get("content-type", "text/plain"),
        headers=headers,
    )


# --------------------------------------------------------------------------- tables
@router.get("/goals/{goal_id}/tables")
def tables_list(goal_id: str, services: Services = Depends(get_services)) -> dict[str, Any]:
    return services.learner.get(f"/v1/tables/{goal_id}")


@router.get("/goals/{goal_id}/tables/{table_id}")
def table_get(
    goal_id: str, table_id: str, services: Services = Depends(get_services)
) -> dict[str, Any]:
    return services.learner.get(f"/v1/tables/{goal_id}/{table_id}")


@router.post("/goals/{goal_id}/tables/{table_id}/cards")
def table_cards(
    goal_id: str,
    table_id: str,
    services: Services = Depends(get_services),
    idem: str | None = IdempotencyHeader,
) -> dict[str, Any]:
    return services.learner.post(
        f"/v1/tables/{goal_id}/{table_id}/cards", {"author": "table"}, idempotency_key=idem
    )
