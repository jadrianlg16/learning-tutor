"""learning_tutor.llm — the pluggable LLM layer (Stage 2).

``ai_providers.py`` is reused from earlier work of mine: one interface,
six providers (Google / OpenAI / Claude / Ollama / LM Studio / Custom) behind
``AIProviderFactory``. Everything else in this package is new:

- ``config``: provider configuration from environment variables, including the
  tutor/solver role split and the "solver must differ from tutor" guard.
- ``structured``: ``generate_text`` / ``generate_structured`` — robust JSON
  extraction, Pydantic validation with retries, and an on-disk cache.
- ``router``: a FastAPI ``APIRouter`` so the gateway can mount ``/llm/*``.

See docs/modules/llm.md for the full contract.
"""

from __future__ import annotations

from .config import (
    ProviderConfig,
    get_provider_config,
    solver_differs_from_tutor,
)
from .structured import GenerationMeta, SameSolverError, generate_structured, generate_text

__all__ = [
    "ProviderConfig",
    "get_provider_config",
    "solver_differs_from_tutor",
    "GenerationMeta",
    "SameSolverError",
    "generate_structured",
    "generate_text",
]
