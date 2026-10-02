"""`learner-svc` — the Stage 0 learner core behind HTTP.

The CLI core *is* this service's core: every route is a thin call into
``learning_tutor.learner.api``, the same functions ``learner`` and the MCP server call.
There is no logic in this package beyond transport concerns (parsing, status codes, the
idempotency header).

Run it with ``uv run learner-svc`` (see :mod:`learning_tutor.learner_svc.__main__`) or
``uvicorn learning_tutor.learner_svc.app:app``.

Environment:

===================  ==============  ==========================================
Variable             Default         Meaning
===================  ==============  ==========================================
``LEARNER_PORT``     ``5034``        Port to bind
``LEARNER_HOST``     ``127.0.0.1``   Interface to bind
``LT_DATA_DIR``      ``./data``      The learner data directory (as for the CLI)
===================  ==============  ==========================================

Errors are always ``{"error": "..."}``: 404 when something named does not exist, 409 on an
idempotency-key conflict, 400 on any other rejected operation, 422 on a malformed body
(FastAPI's own validation, reshaped to the same envelope).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from ..config import Settings, get_settings
from ..learner import api
from ..learner.store import LearnerError, open_store
from .deps import error_response
from .models import Health
from .routes import authoring, reads, recording, study

TITLE = "learner-svc"
DESCRIPTION = (
    "The learner model over HTTP: events, graph, items, evidence rules, scheduling, "
    "views. Same verbs as the `learner` CLI, same core."
)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. ``settings`` defaults to the environment (`LT_DATA_DIR`, ...)."""

    settings = settings or get_settings()
    settings.ensure_dirs()

    app = FastAPI(title=TITLE, description=DESCRIPTION, version="1.0")
    app.state.settings = settings

    @app.exception_handler(LearnerError)
    def _learner_error(_request: Request, exc: LearnerError) -> JSONResponse:
        return error_response(exc)

    @app.exception_handler(RequestValidationError)
    def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        # one envelope for every failure, so a caller never has to branch on shape
        first = exc.errors()[0] if exc.errors() else {}
        where = ".".join(str(p) for p in first.get("loc", ())[1:]) or "body"
        return JSONResponse(
            {"error": f"{where}: {first.get('msg', 'invalid request')}"}, status_code=422
        )

    @app.get("/healthz", response_model=Health, tags=["ops"])
    def healthz() -> dict[str, Any]:
        with open_store(settings) as store:
            return {
                "status": "ok",
                "service": TITLE,
                "schema_version": store.schema_version(),
                "data_dir": str(settings.data_dir),
                "goals": len(api.goal_list(store)),
            }

    app.include_router(authoring.router)
    app.include_router(recording.router)
    app.include_router(reads.router)
    app.include_router(study.router)
    return app


_app: FastAPI | None = None


def __getattr__(name: str) -> Any:
    """``learning_tutor.learner_svc.app:app`` for uvicorn, built on first access.

    Lazily, not at import time: importing this module must not read the environment or
    create a data directory, or a test that merely imports it would write to the repo.
    """

    if name == "app":
        global _app
        if _app is None:
            _app = create_app()
        return _app
    raise AttributeError(name)
