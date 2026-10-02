"""Tests for learning_tutor.llm.structured: JSON extraction, retries, caching.

Uses a FakeProvider injected through AIProviderFactory.create_provider (monkeypatched),
so nothing here touches the network. Each test sets its own LT_DATA_DIR / LT_LLM_* env
vars via monkeypatch rather than relying on tests/conftest.py fixtures, so this file
stays runnable standalone (``pytest tests/test_llm_*.py``) even if the learner-core
conftest changes shape.
"""

from __future__ import annotations

import json
import time

import pytest
from pydantic import BaseModel

from learning_tutor.llm import ai_providers as ai_providers_mod
from learning_tutor.llm import structured as structured_mod
from learning_tutor.llm.config import ProviderConfig
from learning_tutor.llm.structured import (
    GenerationMeta,
    SameSolverError,
    extract_json,
    generate_structured,
    generate_text,
)


class Answer(BaseModel):
    answer: str
    confidence: int


class FakeProvider:
    """Duck-typed stand-in for AIProviderInterface. Returns queued responses in
    order; records every prompt it was called with."""

    def __init__(self, *_args, **_kwargs):
        # Share (not copy) the class-level queue: the factory builds a fresh
        # FakeProvider on every call, but responses must still be consumed in
        # order across those calls (retries within one generate_structured call,
        # and separate calls in the same test).
        self.responses: list[str] = FakeProvider._queue
        self.prompts: list[str] = []

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str | None = None,
        json_mode: bool = False,
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> str:
        self.prompts.append(prompt)
        FakeProvider.calls.append(prompt)
        if not self.responses:
            raise AssertionError("FakeProvider ran out of queued responses")
        return self.responses.pop(0)

    async def generate_chat(self, messages, temperature: float = 0.1) -> str:  # pragma: no cover
        raise NotImplementedError

    async def transcribe_audio(self, *_a, **_k):  # pragma: no cover
        raise NotImplementedError

    def supports_audio(self) -> bool:
        return False


def _install_fake_provider(monkeypatch, responses: list[str]) -> None:
    FakeProvider._queue = responses  # type: ignore[attr-defined]
    FakeProvider.calls = []  # type: ignore[attr-defined]

    def _factory(_provider, _api_key, _model, _base_url=None):
        return FakeProvider()

    monkeypatch.setattr(ai_providers_mod.AIProviderFactory, "create_provider", staticmethod(_factory))


@pytest.fixture(autouse=True)
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("LT_LLM_CACHE_TTL_S", raising=False)
    monkeypatch.delenv("LT_LLM_ALLOW_SAME_SOLVER", raising=False)
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "test-model")
    return tmp_path


def _cfg(**overrides) -> ProviderConfig:
    base = dict(provider="ollama", model="test-model", api_key="", base_url="http://fake")
    base.update(overrides)
    return ProviderConfig(**base)


# ---------------------------------------------------------------------------
# extract_json
# ---------------------------------------------------------------------------


def test_extract_json_plain():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced_block():
    text = 'Sure, here you go:\n```json\n{"a": 1, "b": [1, 2]}\n```\nHope that helps.'
    assert extract_json(text) == {"a": 1, "b": [1, 2]}


def test_extract_json_prose_wrapped():
    text = 'The answer is {"a": 1, "b": 2} as requested.'
    assert extract_json(text) == {"a": 1, "b": 2}


def test_extract_json_trailing_comma():
    text = '{"a": 1, "b": 2,}'
    assert extract_json(text) == {"a": 1, "b": 2}


def test_extract_json_trailing_comma_in_array():
    text = '{"items": [1, 2, 3,], "ok": true,}'
    assert extract_json(text) == {"items": [1, 2, 3], "ok": True}


def test_extract_json_empty_raises():
    with pytest.raises(ValueError):
        extract_json("   ")


def test_extract_json_unparseable_raises():
    with pytest.raises(ValueError):
        extract_json("this is not json at all")


# ---------------------------------------------------------------------------
# generate_structured: happy path + retry
# ---------------------------------------------------------------------------


def test_generate_structured_happy_path(monkeypatch):
    _install_fake_provider(monkeypatch, ['{"answer": "42", "confidence": 5}'])
    result, meta = generate_structured("what is the answer?", Answer, _cfg(), cache=False)
    assert result.answer == "42"
    assert result.confidence == 5
    assert isinstance(meta, GenerationMeta)
    assert meta.provider == "ollama"
    assert meta.model == "test-model"
    assert meta.cached is False
    assert meta.latency_ms >= 0


def test_generate_structured_invalid_then_success_on_retry(monkeypatch):
    # First reply is not valid JSON; second (after the error is appended to the
    # prompt) is valid. Default retries=2 allows up to 3 attempts.
    _install_fake_provider(
        monkeypatch,
        ["not json at all", '{"answer": "yes", "confidence": 3}'],
    )
    result, meta = generate_structured("q", Answer, _cfg(), cache=False)
    assert result.answer == "yes"
    assert len(FakeProvider.calls) == 2
    # The retry prompt must carry the error forward so the model can self-correct.
    assert "could not be parsed" in FakeProvider.calls[1] or "failed" in FakeProvider.calls[1]
    assert meta.cached is False


def test_generate_structured_validation_error_then_success(monkeypatch):
    # Valid JSON but fails schema validation (confidence must be int) on first try.
    _install_fake_provider(
        monkeypatch,
        ['{"answer": "x", "confidence": "not-a-number"}', '{"answer": "x", "confidence": 2}'],
    )
    result, _meta = generate_structured("q", Answer, _cfg(), cache=False)
    assert result.confidence == 2
    assert len(FakeProvider.calls) == 2


def test_generate_structured_exhausts_retries_raises(monkeypatch):
    _install_fake_provider(monkeypatch, ["nope", "still nope", "nope again"])
    with pytest.raises(ValueError):
        generate_structured("q", Answer, _cfg(), retries=2, cache=False)
    assert len(FakeProvider.calls) == 3  # 1 + retries


# ---------------------------------------------------------------------------
# generate_text
# ---------------------------------------------------------------------------


def test_generate_text_happy_path(monkeypatch):
    _install_fake_provider(monkeypatch, ["hello there"])
    text, meta = generate_text("say hi", _cfg(), cache=False)
    assert text == "hello there"
    assert meta.cached is False


# ---------------------------------------------------------------------------
# cache hit / miss / TTL
# ---------------------------------------------------------------------------


def test_cache_miss_then_hit(monkeypatch):
    _install_fake_provider(monkeypatch, ['{"answer": "cached", "confidence": 1}'])
    cfg = _cfg()

    result1, meta1 = generate_structured("cache me", Answer, cfg, cache=True)
    assert result1.answer == "cached"
    assert meta1.cached is False
    assert len(FakeProvider.calls) == 1

    # Second call with the identical prompt+schema+provider+model must hit the
    # cache and never touch the (now-exhausted) FakeProvider queue.
    result2, meta2 = generate_structured("cache me", Answer, cfg, cache=True)
    assert result2.answer == "cached"
    assert meta2.cached is True
    assert len(FakeProvider.calls) == 1  # unchanged: no new provider call


def test_cache_disabled_always_calls_provider(monkeypatch):
    _install_fake_provider(
        monkeypatch,
        ['{"answer": "one", "confidence": 1}', '{"answer": "two", "confidence": 2}'],
    )
    cfg = _cfg()
    r1, _ = generate_structured("no cache please", Answer, cfg, cache=False)
    r2, _ = generate_structured("no cache please", Answer, cfg, cache=False)
    assert r1.answer == "one"
    assert r2.answer == "two"
    assert len(FakeProvider.calls) == 2


def test_cache_key_varies_with_prompt(monkeypatch):
    _install_fake_provider(
        monkeypatch,
        ['{"answer": "A", "confidence": 1}', '{"answer": "B", "confidence": 2}'],
    )
    cfg = _cfg()
    r1, _ = generate_structured("prompt one", Answer, cfg, cache=True)
    r2, _ = generate_structured("prompt two", Answer, cfg, cache=True)
    assert r1.answer == "A"
    assert r2.answer == "B"
    assert len(FakeProvider.calls) == 2


def test_cache_ttl_expiry(monkeypatch):
    monkeypatch.setenv("LT_LLM_CACHE_TTL_S", "1")
    _install_fake_provider(
        monkeypatch,
        ['{"answer": "fresh1", "confidence": 1}', '{"answer": "fresh2", "confidence": 2}'],
    )
    cfg = _cfg()
    r1, meta1 = generate_structured("ttl test", Answer, cfg, cache=True)
    assert r1.answer == "fresh1"
    assert meta1.cached is False

    # Force the cache entry to look old by rewinding its mtime rather than
    # sleeping in the test.
    key = structured_mod._cache_key(cfg.provider, cfg.model, "ttl test", Answer.__name__)
    path = structured_mod._cache_path(key)
    old = time.time() - 10
    import os

    os.utime(path, (old, old))

    r2, meta2 = generate_structured("ttl test", Answer, cfg, cache=True)
    assert r2.answer == "fresh2"
    assert meta2.cached is False
    assert len(FakeProvider.calls) == 2


def test_cache_ttl_zero_disables_cache(monkeypatch):
    monkeypatch.setenv("LT_LLM_CACHE_TTL_S", "0")
    _install_fake_provider(
        monkeypatch,
        ['{"answer": "a", "confidence": 1}', '{"answer": "b", "confidence": 2}'],
    )
    cfg = _cfg()
    r1, _ = generate_structured("ttl zero", Answer, cfg, cache=True)
    r2, _ = generate_structured("ttl zero", Answer, cfg, cache=True)
    assert r1.answer == "a"
    assert r2.answer == "b"  # cache never wrote/read anything


# ---------------------------------------------------------------------------
# solver-must-differ-from-tutor guard
# ---------------------------------------------------------------------------


def test_solver_same_as_tutor_refuses(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "same-model")
    monkeypatch.delenv("LT_LLM_TUTOR_PROVIDER", raising=False)
    monkeypatch.delenv("LT_LLM_TUTOR_MODEL", raising=False)
    monkeypatch.delenv("LT_LLM_SOLVER_PROVIDER", raising=False)
    monkeypatch.delenv("LT_LLM_SOLVER_MODEL", raising=False)
    _install_fake_provider(monkeypatch, ['{"answer": "x", "confidence": 1}'])

    with pytest.raises(SameSolverError):
        generate_structured("solve this", Answer, role="solver", cache=False)

    # The guard must fire before any provider call is made.
    assert len(FakeProvider.calls) == 0


def test_solver_same_as_tutor_allowed_with_override(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "same-model")
    monkeypatch.setenv("LT_LLM_ALLOW_SAME_SOLVER", "1")
    _install_fake_provider(monkeypatch, ['{"answer": "x", "confidence": 1}'])

    result, _meta = generate_structured("solve this", Answer, role="solver", cache=False)
    assert result.answer == "x"


def test_solver_different_model_allowed(monkeypatch):
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "tutor-model")
    monkeypatch.setenv("LT_LLM_SOLVER_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_SOLVER_MODEL", "solver-model")
    _install_fake_provider(monkeypatch, ['{"answer": "x", "confidence": 1}'])

    result, meta = generate_structured("solve this", Answer, role="solver", cache=False)
    assert result.answer == "x"
    assert meta.model == "solver-model"


class AllDefaults(BaseModel):
    """Every field defaults — the shape that let a schema echo validate as empty."""

    nodes: list[str] = []
    edges: list[str] = []


def test_schema_echo_is_rejected_and_retried(monkeypatch):
    """llama3.1:8b once replied with the JSON schema itself; pydantic accepted it as an
    empty instance and the empty plan was cached. The echo must fail validation and be
    retried with an explicit error, never returned."""
    echo = json.dumps(AllDefaults.model_json_schema())
    _install_fake_provider(monkeypatch, [echo, '{"nodes": ["a"], "edges": []}'])
    result, meta = generate_structured("plan", AllDefaults, _cfg(), cache=False)
    assert result.nodes == ["a"]
    assert len(FakeProvider.calls) == 2
    assert "schema itself" in FakeProvider.calls[1]
