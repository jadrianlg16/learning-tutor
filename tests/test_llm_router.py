"""Smoke tests for learning_tutor.llm.router — the FastAPI surface the gateway mounts.

The router must import cleanly with no network; these tests confirm that and exercise
the three endpoints against a FakeProvider, never a real one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from learning_tutor.llm import ai_providers as ai_providers_mod
from learning_tutor.llm.router import router


class FakeProvider:
    def __init__(self, *_args, **_kwargs):
        self.responses = FakeProvider._queue  # shared queue; see test_llm_structured.py

    async def generate_text(self, prompt, system_prompt=None, json_mode=False,
                             temperature=0.1, max_tokens=4000):
        if not self.responses:
            raise AssertionError("FakeProvider ran out of queued responses")
        return self.responses.pop(0)

    async def generate_chat(self, messages, temperature=0.1):  # pragma: no cover
        raise NotImplementedError

    async def transcribe_audio(self, *_a, **_k):  # pragma: no cover
        raise NotImplementedError

    def supports_audio(self) -> bool:
        return False


def _install_fake_provider(monkeypatch, responses):
    FakeProvider._queue = responses

    def _factory(_provider, _api_key, _model, _base_url=None):
        return FakeProvider()

    monkeypatch.setattr(ai_providers_mod.AIProviderFactory, "create_provider", staticmethod(_factory))


@pytest.fixture(autouse=True)
def isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("LT_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("LT_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("LT_LLM_MODEL", "test-model")
    monkeypatch.delenv("LT_LLM_ALLOW_SAME_SOLVER", raising=False)
    monkeypatch.delenv("LT_LLM_SOLVER_PROVIDER", raising=False)
    monkeypatch.delenv("LT_LLM_SOLVER_MODEL", raising=False)


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_router_imports_cleanly():
    # If this module made a network call at import time, collecting this test
    # file at all would already have failed.
    assert router.prefix == "/llm"


def test_health_endpoint(client):
    resp = client.get("/llm/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["tutor"]["provider"] == "ollama"
    assert body["tutor"]["model"] == "test-model"


def test_generate_endpoint(client, monkeypatch):
    _install_fake_provider(monkeypatch, ["hello from the router"])
    resp = client.post("/llm/generate", json={"prompt": "hi", "cache": False})
    assert resp.status_code == 200
    body = resp.json()
    assert body["text"] == "hello from the router"
    assert body["meta"]["provider"] == "ollama"
    assert body["meta"]["cached"] is False


def test_structured_endpoint(client, monkeypatch):
    _install_fake_provider(monkeypatch, ['{"answer": "42", "confidence": 5}'])
    resp = client.post(
        "/llm/structured",
        json={
            "prompt": "what is the answer?",
            "schema": {"answer": "str", "confidence": "int"},
            "cache": False,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["result"] == {"answer": "42", "confidence": 5}
    assert body["meta"]["cached"] is False


def test_structured_endpoint_unsupported_type(client):
    resp = client.post(
        "/llm/structured",
        json={"prompt": "x", "schema": {"weird": "not-a-type"}, "cache": False},
    )
    assert resp.status_code == 400


def test_generate_endpoint_solver_refusal(client, monkeypatch):
    # Solver resolves to the same provider+model as the tutor by default (no
    # override, no LT_LLM_ALLOW_SAME_SOLVER) -> the router must surface a 409,
    # not a 500 or a silent generation.
    _install_fake_provider(monkeypatch, ["should never be returned"])
    resp = client.post("/llm/generate", json={"prompt": "hi", "role": "solver", "cache": False})
    assert resp.status_code == 409
