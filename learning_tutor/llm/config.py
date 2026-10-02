"""LLM provider configuration, read from the environment.

Everything configurable comes from env with a default — no machine-specific values
live here. Two roles matter, per CONTRACTS.md's evidence rules: the **tutor** (who
teaches and writes questions) and the **solver** (the blind grader that answers a
generated item before it is trusted as evidence). The same model must never author,
solve, and judge a single item, so the solver role is checked against the tutor role
at call time in :mod:`learning_tutor.llm.structured`.

Env vars
--------
``LT_LLM_PROVIDER``       default provider for both roles (default ``ollama``)
``LT_LLM_MODEL``          default model for both roles (default ``llama3.1:8b``)
``LT_LLM_BASE_URL``       default base URL override for both roles (optional)
``OLLAMA_URL``            base URL for the ``ollama`` provider (default
                          ``http://localhost:11434``)
``ANTHROPIC_API_KEY``     API key for the ``claude`` provider
``OPENAI_API_KEY``        API key for the ``openai`` provider
``GOOGLE_API_KEY``        API key for the ``google`` provider
``LT_LLM_TUTOR_PROVIDER`` / ``LT_LLM_TUTOR_MODEL`` / ``LT_LLM_TUTOR_BASE_URL``
                          override the tutor role only
``LT_LLM_SOLVER_PROVIDER`` / ``LT_LLM_SOLVER_MODEL`` / ``LT_LLM_SOLVER_BASE_URL``
                          override the solver role only
``LT_LLM_ALLOW_SAME_SOLVER``
                          truthy value disables the "solver must differ from
                          tutor" guard (default off)
``LT_LLM_TEMPERATURE`` / ``LT_LLM_MAX_TOKENS``
                          generation defaults (0.2 / 4000)
``LT_LLM_TIMEOUT_S``      seconds to wait on one provider HTTP call (default 120).
                          The copied provider file used to hardcode 30/60 s, which a
                          local 12B model on a plan-sized prompt exceeds every time
"""

from __future__ import annotations

import os
from dataclasses import dataclass

_TRUE = {"1", "true", "yes", "on"}

# Providers that need no API key by default (local servers).
_NO_KEY_PROVIDERS = {"ollama", "lm_studio"}

_API_KEY_ENV = {
    "google": "GOOGLE_API_KEY",
    "openai": "OPENAI_API_KEY",
    "claude": "ANTHROPIC_API_KEY",
}


def _env_str(name: str, default: str = "") -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_opt(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value is None or value == "" else value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_opt(name)
    if raw is None:
        return default
    return raw.strip().lower() in _TRUE


def _env_float(name: str, default: float) -> float:
    raw = _env_opt(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env_opt(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class ProviderConfig:
    """Everything :class:`~learning_tutor.llm.ai_providers.AIProviderFactory` needs
    to build a provider, plus generation defaults."""

    provider: str
    model: str
    api_key: str = ""
    base_url: str | None = None
    temperature: float = 0.2
    max_tokens: int = 4000
    role: str | None = None

    def key(self) -> tuple[str, str]:
        """The (provider, model) identity used for the solver/tutor comparison."""
        return (self.provider, self.model)


def _default_base_url(provider: str) -> str | None:
    if provider == "ollama":
        return _env_opt("OLLAMA_URL") or "http://localhost:11434"
    return None


def _default_api_key(provider: str) -> str:
    if provider in _NO_KEY_PROVIDERS:
        return ""
    env_name = _API_KEY_ENV.get(provider)
    if env_name is None:
        return ""
    return _env_str(env_name, "")


def _role_prefix(role: str | None) -> str:
    if role is None:
        return "LT_LLM_"
    return f"LT_LLM_{role.upper()}_"


def get_provider_config(role: str | None = None) -> ProviderConfig:
    """Resolve a :class:`ProviderConfig` for ``role`` (``None``, ``"tutor"`` or
    ``"solver"``).

    Role-specific env vars (``LT_LLM_TUTOR_*`` / ``LT_LLM_SOLVER_*``) override the
    unscoped defaults (``LT_LLM_PROVIDER`` / ``LT_LLM_MODEL`` / ``LT_LLM_BASE_URL``).
    A role with no overrides resolves to the same provider+model as the default —
    which is exactly why the solver guard in :mod:`structured` exists.
    """
    prefix = _role_prefix(role)
    provider = _env_str(f"{prefix}PROVIDER", _env_str("LT_LLM_PROVIDER", "ollama"))
    model = _env_str(f"{prefix}MODEL", _env_str("LT_LLM_MODEL", "llama3.1:8b"))
    base_url = _env_opt(f"{prefix}BASE_URL") or _env_opt("LT_LLM_BASE_URL") or _default_base_url(
        provider
    )
    api_key = _env_opt(f"{prefix}API_KEY") or _default_api_key(provider)
    return ProviderConfig(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=_env_float("LT_LLM_TEMPERATURE", 0.2),
        max_tokens=_env_int("LT_LLM_MAX_TOKENS", 4000),
        role=role,
    )


def solver_differs_from_tutor(
    tutor_cfg: ProviderConfig | None = None, solver_cfg: ProviderConfig | None = None
) -> bool:
    """True if the resolved solver (provider, model) differs from the tutor's.

    Enforces the "same model never author + solver + judge" rule from
    CONTRACTS.md — a blind solver validating an item it (or an identically
    configured sibling) wrote is not independent evidence.
    """
    tutor_cfg = tutor_cfg or get_provider_config("tutor")
    solver_cfg = solver_cfg or get_provider_config("solver")
    return tutor_cfg.key() != solver_cfg.key()


def allow_same_solver() -> bool:
    return _env_bool("LT_LLM_ALLOW_SAME_SOLVER", False)


#: The provider module originally hardcoded 30 s and 60 s. Measured locally on 2026-09-05,
#: ``gemma3:12b`` on a plan-sized prompt hit 60 s every time; 120 s is the
#: default so a local model is slow rather than broken, and it is configurable either way.
DEFAULT_REQUEST_TIMEOUT_S = 120.0


def request_timeout() -> float:
    """Seconds to wait on one provider HTTP call (``LT_LLM_TIMEOUT_S``, default 120)."""

    return _env_float("LT_LLM_TIMEOUT_S", DEFAULT_REQUEST_TIMEOUT_S)


def cache_ttl_seconds() -> int:
    """TTL for the on-disk structured-generation cache. 0 disables caching."""
    return _env_int("LT_LLM_CACHE_TTL_S", 7 * 24 * 3600)


def cache_dir() -> str:
    """``LT_DATA_DIR/cache/llm`` — resolved independently of ``learning_tutor.config``
    so this module has no dependency on the learner core."""
    data_dir = _env_str("LT_DATA_DIR", "./data")
    return os.path.join(data_dir, "cache", "llm")
