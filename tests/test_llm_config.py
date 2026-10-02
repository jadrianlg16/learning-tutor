"""Tests for learning_tutor.llm.config: env resolution and the tutor/solver split."""

from __future__ import annotations

import pytest

from learning_tutor.llm.config import (
    allow_same_solver,
    cache_ttl_seconds,
    get_provider_config,
    request_timeout,
    solver_differs_from_tutor,
)


def test_defaults(monkeypatch):
    for var in [
        "LT_LLM_PROVIDER",
        "LT_LLM_MODEL",
        "LT_LLM_BASE_URL",
        "OLLAMA_URL",
        "LT_LLM_TUTOR_PROVIDER",
        "LT_LLM_TUTOR_MODEL",
        "LT_LLM_SOLVER_PROVIDER",
        "LT_LLM_SOLVER_MODEL",
    ]:
        monkeypatch.delenv(var, raising=False)

    cfg = get_provider_config()
    assert cfg.provider == "ollama"
    assert cfg.model == "llama3.1:8b"
    assert cfg.base_url == "http://localhost:11434"


def test_ollama_url_override(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_URL", "http://example-host:11434")
    cfg = get_provider_config()
    assert cfg.base_url == "http://example-host:11434"


def test_role_specific_overrides_win_over_defaults(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "default-model")
    monkeypatch.setenv("LT_LLM_TUTOR_MODEL", "tutor-only-model")

    tutor = get_provider_config("tutor")
    default = get_provider_config()
    assert tutor.model == "tutor-only-model"
    assert default.model == "default-model"


def test_api_key_resolution_by_provider(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "claude")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    cfg = get_provider_config()
    assert cfg.provider == "claude"
    assert cfg.api_key == "sk-ant-test"


def test_local_providers_need_no_key(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = get_provider_config()
    assert cfg.api_key == ""


def test_solver_differs_from_tutor_true_when_overridden(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "tutor-model")
    monkeypatch.setenv("LT_LLM_SOLVER_MODEL", "solver-model")
    assert solver_differs_from_tutor() is True


def test_solver_differs_from_tutor_false_by_default(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "same-model")
    monkeypatch.delenv("LT_LLM_SOLVER_MODEL", raising=False)
    monkeypatch.delenv("LT_LLM_SOLVER_PROVIDER", raising=False)
    assert solver_differs_from_tutor() is False


def test_allow_same_solver_flag(monkeypatch):
    monkeypatch.delenv("LT_LLM_ALLOW_SAME_SOLVER", raising=False)
    assert allow_same_solver() is False
    monkeypatch.setenv("LT_LLM_ALLOW_SAME_SOLVER", "1")
    assert allow_same_solver() is True
    monkeypatch.setenv("LT_LLM_ALLOW_SAME_SOLVER", "yes")
    assert allow_same_solver() is True
    monkeypatch.setenv("LT_LLM_ALLOW_SAME_SOLVER", "0")
    assert allow_same_solver() is False


def test_cache_ttl_default_is_seven_days(monkeypatch):
    monkeypatch.delenv("LT_LLM_CACHE_TTL_S", raising=False)
    assert cache_ttl_seconds() == 7 * 24 * 3600


def test_cache_ttl_zero_disables(monkeypatch):
    monkeypatch.setenv("LT_LLM_CACHE_TTL_S", "0")
    assert cache_ttl_seconds() == 0


def test_request_timeout_default_is_120_seconds(monkeypatch):
    """The copied ai_providers.py hardcoded 30 s and 60 s; a local 12B model needs more."""

    monkeypatch.delenv("LT_LLM_TIMEOUT_S", raising=False)
    assert request_timeout() == 120.0


def test_request_timeout_reads_the_environment(monkeypatch):
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "300")
    assert request_timeout() == 300.0
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "45.5")
    assert request_timeout() == 45.5


def test_a_non_numeric_timeout_is_an_error_not_a_silent_default(monkeypatch):
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "soon")
    with pytest.raises(ValueError, match="LT_LLM_TIMEOUT_S"):
        request_timeout()
