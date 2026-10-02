"""FastAPI router exposing the llm module over HTTP.

Mounted by the Stage 2 gateway (CONTRACTS.md: "gateway ... + llm + tutor + corpus
modules in one process, each a FastAPI router that can be lifted out later"). This
module must import cleanly with no network access — provider calls only happen
inside request handlers.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, create_model

from .config import get_provider_config
from .structured import GenerationMeta, SameSolverError, generate_structured, generate_text

router = APIRouter(prefix="/llm", tags=["llm"])


class GenerateRequest(BaseModel):
    prompt: str
    role: str | None = Field(default=None, description="'tutor', 'solver', or unset for default")
    system_prompt: str | None = None
    cache: bool = True


class GenerationMetaOut(BaseModel):
    provider: str
    model: str
    prompt_hash: str
    latency_ms: int
    cached: bool

    @classmethod
    def from_meta(cls, meta: GenerationMeta) -> GenerationMetaOut:
        return cls(
            provider=meta.provider,
            model=meta.model,
            prompt_hash=meta.prompt_hash,
            latency_ms=meta.latency_ms,
            cached=meta.cached,
        )


class GenerateResponse(BaseModel):
    text: str
    meta: GenerationMetaOut


class StructuredRequest(BaseModel):
    prompt: str
    schema_: dict[str, Any] = Field(
        alias="schema",
        description=(
            "A flat JSON-schema-like field map, e.g. "
            '{"answer": "str", "confidence": "int"}. Only a small subset of JSON '
            "Schema is supported — this endpoint is for simple, ad-hoc callers; "
            "code that owns a real Pydantic model should call "
            "learning_tutor.llm.structured.generate_structured directly."
        ),
    )
    role: str | None = None
    system_prompt: str | None = None
    retries: int = 2
    cache: bool = True

    model_config = {"populate_by_name": True}


class StructuredResponse(BaseModel):
    result: dict[str, Any]
    meta: GenerationMetaOut


_TYPE_MAP: dict[str, Any] = {
    "str": str,
    "string": str,
    "int": int,
    "integer": int,
    "float": float,
    "number": float,
    "bool": bool,
    "boolean": bool,
}


def _build_dynamic_schema(fields: dict[str, Any]) -> type[BaseModel]:
    """Build a throwaway Pydantic model from a flat {field: type-name} map.

    Deliberately minimal — this endpoint exists so a non-Python caller (e.g.
    web-ui) can ask for structured JSON without a real schema module; anything
    richer belongs in a real Pydantic model imported and used directly.
    """
    resolved: dict[str, Any] = {}
    for name, type_name in fields.items():
        py_type = _TYPE_MAP.get(str(type_name).lower())
        if py_type is None:
            raise HTTPException(
                status_code=400, detail=f"Unsupported field type '{type_name}' for '{name}'"
            )
        resolved[name] = (py_type, ...)
    return create_model("DynamicStructuredResponse", **resolved)  # type: ignore[call-overload]


@router.get("/health")
def health() -> dict[str, Any]:
    """Reports configured roles without making any network calls."""
    tutor = get_provider_config("tutor")
    solver = get_provider_config("solver")
    return {
        "status": "ok",
        "tutor": {"provider": tutor.provider, "model": tutor.model},
        "solver": {"provider": solver.provider, "model": solver.model},
    }


@router.post("/generate", response_model=GenerateResponse)
def generate(req: GenerateRequest) -> GenerateResponse:
    try:
        text, meta = generate_text(
            req.prompt,
            role=req.role,
            system_prompt=req.system_prompt,
            cache=req.cache,
        )
    except SameSolverError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 - surface a clean error to the caller
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return GenerateResponse(text=text, meta=GenerationMetaOut.from_meta(meta))


@router.post("/structured", response_model=StructuredResponse)
def structured(req: StructuredRequest) -> StructuredResponse:
    model_cls = _build_dynamic_schema(req.schema_)
    try:
        result, meta = generate_structured(
            req.prompt,
            model_cls,
            role=req.role,
            retries=req.retries,
            cache=req.cache,
            system_prompt=req.system_prompt,
        )
    except SameSolverError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return StructuredResponse(
        result=result.model_dump(mode="json"), meta=GenerationMetaOut.from_meta(meta)
    )
