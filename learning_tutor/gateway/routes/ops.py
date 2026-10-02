"""Ops: health across all four dependencies, the passport export, the render proxy."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from typing import Any

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from ..deps import Services, get_services
from ..errors import GatewayError, UpstreamDown
from ..models import RenderIn

router = APIRouter(prefix="/api", tags=["ops"])


#: Seconds to wait on ``GET /api/tags``. Health must answer fast even when Ollama is not up.
TAGS_TIMEOUT_S = 2.0


def ollama_models(base_url: str, timeout: float = TAGS_TIMEOUT_S) -> set[str]:
    """The model names Ollama reports at ``{base_url}/api/tags``. Raises on any failure."""
    response = httpx.get(base_url.rstrip("/") + "/api/tags", timeout=timeout)
    response.raise_for_status()
    return {str(m.get("name", "")) for m in response.json().get("models") or []}


def _present(model: str, names: set[str]) -> bool:
    # `ollama pull llama3.1` is listed as `llama3.1:latest`
    return model in names or (":" not in model and f"{model}:latest" in names)


def _model_presence(tutor: Any, solver: Any) -> dict[str, Any]:
    """``model_present`` / ``solver_present`` are ``True``/``False`` when the role runs on
    ollama and the tags call answered, ``None`` when the role is not ollama or Ollama
    could not be reached (then ``error`` says why). Never raises."""
    out: dict[str, Any] = {"model_present": None, "solver_present": None}
    cache: dict[str, set[str] | None] = {}
    errors: list[str] = []
    for key, cfg in (("model_present", tutor), ("solver_present", solver)):
        if cfg.provider != "ollama":
            continue
        url = cfg.base_url or "http://localhost:11434"
        if url not in cache:
            try:
                cache[url] = ollama_models(url)
            except Exception as exc:  # unreachable, bad JSON, non-2xx — all "unknown"
                cache[url] = None
                errors.append(f"{url}: {exc.__class__.__name__}: {exc}")
        names = cache[url]
        out[key] = None if names is None else _present(cfg.model, names)
    if errors:
        out["error"] = "; ".join(errors)
    return out


def _llm_health() -> dict[str, Any]:
    try:
        from ...llm.config import get_provider_config, solver_differs_from_tutor
    except ImportError as exc:  # pragma: no cover
        return {"status": "down", "detail": str(exc)}
    try:
        tutor = get_provider_config("tutor")
        solver = get_provider_config("solver")
    except Exception as exc:
        return {"status": "unconfigured", "detail": str(exc)}
    needs_key = tutor.provider in ("google", "openai", "claude")
    status = "unconfigured" if needs_key and not tutor.api_key else "ok"
    presence = _model_presence(tutor, solver)
    if presence["model_present"] is False or presence["solver_present"] is False:
        status = "unconfigured"
    elif status == "ok" and "error" in presence:
        # an ollama role whose server did not answer: configured, but nothing can be generated
        status = "degraded"
    return {
        "status": status,
        "detail": {
            "provider": tutor.provider,
            "model": tutor.model,
            "solver_provider": solver.provider,
            "solver_model": solver.model,
            **presence,
            "tutor": {"provider": tutor.provider, "model": tutor.model},
            "solver": {"provider": solver.provider, "model": solver.model},
            "solver_differs_from_tutor": solver_differs_from_tutor(),
        },
    }


def _corpus_health(services: Services) -> dict[str, Any]:
    try:
        from ...corpus import open_store as open_corpus
    except ImportError as exc:  # pragma: no cover
        return {"status": "down", "detail": str(exc)}
    try:
        with open_corpus(services.settings) as store:
            return {
                "status": "ok",
                "detail": {"schema_version": store.schema_version(), "db": str(store.db_path)},
            }
    except Exception as exc:
        return {"status": "down", "detail": str(exc)}


@router.get("/health")
def health(services: Services = Depends(get_services)) -> dict[str, Any]:
    checks = {
        "learner": services.learner.health(),
        "render": services.render.health(),
        "llm": _llm_health(),
        "corpus": _corpus_health(services),
    }
    return {
        # the gateway itself is up as long as nothing is "down": the model-free study tools
        # still work with the LLM degraded or unconfigured
        "ok": all(check["status"] != "down" for check in checks.values()),
        "services": {name: check["status"] for name, check in checks.items()},
        "detail": {name: check.get("detail") for name, check in checks.items()},
        "prompt_version": services.prompt_version,
        "rubric_version": services.rubric_version,
    }


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


#: Where the gateway would keep rendered SVGs. Nothing writes here yet — `teach/next`
#: returns the SVG inline and does not persist it — so the directory is normally absent
#: and the zip carries only what learner-svc put in `artifacts/`.
GATEWAY_ARTIFACTS = "artifacts"


@router.get("/passport")
def passport(services: Services = Depends(get_services)) -> StreamingResponse:
    """The learner passport: "the model-independence promise made concrete" (IDEA.md).

    It is learner-svc's own zip — ``events.jsonl``, ``state.json``, ``learner.md``,
    ``notes.md``, ``goals.json``, ``graph.json``, ``disputes.json`` and the vault's
    session logs — with the two things learner-svc cannot know about added: the gateway's
    ``gateway-state.json`` (the phase machine) and any rendered artefacts.

    Wrapping rather than re-assembling is the point. The service that owns the volume
    builds every member from the database in one read; the gateway no longer opens
    ``events.db`` behind it, so an export can no longer be quietly short because the
    gateway could not see that filesystem. If learner-svc is down this is a 502, which is
    the honest answer — an empty passport is not.
    """

    settings = services.settings
    source_bytes = services.learner.content("/v1/passport")

    manifest: dict[str, Any] = {
        "generated_by": "learning-tutor gateway",
        "prompt_version": services.prompt_version,
        "source": "learner-svc GET /v1/passport",
        "added": {},
    }

    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source_bytes)) as source:
        try:
            manifest["learner_svc"] = json.loads(source.read("MANIFEST.json"))
        except KeyError:  # pragma: no cover - learner-svc always writes one
            manifest["learner_svc"] = None
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name in source.namelist():
                if name == "MANIFEST.json":
                    continue
                archive.writestr(name, source.read(name))

            state_json = _read(Path(services.state.path))
            archive.writestr("gateway-state.json", state_json)
            manifest["added"]["gateway-state.json"] = (
                "the gateway phase machine and answer keys"
                if state_json
                else "unavailable: no gateway state written yet"
            )

            rendered = Path(settings.data_dir) / "gateway" / GATEWAY_ARTIFACTS
            svgs = sorted(rendered.glob("*.svg")) if rendered.is_dir() else []
            for path in svgs:
                archive.writestr(f"artifacts/{path.name}", _read(path))
            manifest["added"]["artifacts/"] = f"{len(svgs)} rendered svg(s) from the gateway"

            archive.writestr("MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    buffer.seek(0)

    return StreamingResponse(
        iter([buffer.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="learner-passport.zip"'},
    )


@router.post("/render")
def render(body: RenderIn, services: Services = Depends(get_services)) -> dict[str, Any]:
    """Straight proxy to render-svc, with the check first so a bad diagram 400s here."""

    if not services.render.configured:
        raise UpstreamDown(
            "render",
            "LT_RENDER_URL is not set",
            code="render_unconfigured",
        )
    check = services.render.check(body.mermaid)
    if not check.get("ok"):
        raise GatewayError(
            "mermaid does not parse: "
            + "; ".join(str(e.get("message")) for e in check.get("errors") or []),
            code="bad_mermaid",
            status=400,
        )
    return services.render.render(body.mermaid, theme=body.theme)
