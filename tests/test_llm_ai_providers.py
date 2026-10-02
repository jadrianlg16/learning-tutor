"""Sanity checks for the copied ai_providers.py — no network calls.

Confirms the provider set and factory shape of the reused provider file, and that the two
deliberate changes to it are wired in:
every request timeout reads `LT_LLM_TIMEOUT_S` instead of a hardcoded 30 or 60, and the
Claude provider follows the current Messages API (mocked HTTP only; no live call).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from learning_tutor.llm.ai_providers import (
    AIProvider,
    AIProviderFactory,
    ClaudeProvider,
    ClaudeRefusalError,
    ClaudeTruncatedError,
    CustomProvider,
    GoogleProvider,
    LMStudioProvider,
    OllamaProvider,
    OpenAIProvider,
)


def test_provider_enum_has_six_members():
    assert {p.value for p in AIProvider} == {
        "google",
        "openai",
        "claude",
        "ollama",
        "lm_studio",
        "custom",
    }


def test_factory_lists_available_providers():
    available = AIProviderFactory.get_available_providers()
    assert set(available) == {p.value for p in AIProvider}


@pytest.mark.parametrize(
    "provider,expected_cls",
    [
        (AIProvider.OPENAI, OpenAIProvider),
        (AIProvider.CLAUDE, ClaudeProvider),
        (AIProvider.OLLAMA, OllamaProvider),
        (AIProvider.LM_STUDIO, LMStudioProvider),
    ],
)
def test_factory_creates_expected_class(provider, expected_cls):
    # Construction only — no provider does network I/O in __init__ except Google
    # (genai.configure), which is exercised separately below when available.
    instance = AIProviderFactory.create_provider(provider, "key", "model", None)
    assert isinstance(instance, expected_cls)


def test_custom_provider_requires_base_url():
    with pytest.raises(ValueError):
        AIProviderFactory.create_provider(AIProvider.CUSTOM, "key", "model", None)


def test_custom_provider_with_base_url():
    instance = AIProviderFactory.create_provider(AIProvider.CUSTOM, "key", "model", "http://x")
    assert isinstance(instance, CustomProvider)


def test_ollama_default_base_url():
    instance = OllamaProvider("", "llama3.2", None)
    assert instance.base_url == "http://localhost:11434"


def test_google_provider_import_guarded():
    # google-generativeai is an optional dependency; if it isn't installed the
    # module must still import (lazy import at module load, guarded in __init__).
    from learning_tutor.llm import ai_providers as mod

    if mod.genai is None:
        with pytest.raises(ImportError):
            GoogleProvider("key", "model", None)
    else:
        # If it *is* installed, construction still shouldn't hit the network —
        # genai.configure only stores the key locally.
        GoogleProvider("key", "model", None)


# ------------------------------------------------------------------- request timeout


class FakeResponse:
    """Enough of an httpx.Response for every provider's happy path."""

    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "response": "ok",  # ollama /api/generate
            "message": {"content": "ok"},  # ollama /api/chat
            "choices": [{"message": {"content": "ok"}}],  # openai-shaped
            "content": [{"type": "text", "text": "ok"}],  # claude
            "stop_reason": "end_turn",  # claude
        }


class FakeClient:
    """Stands in for httpx.AsyncClient and records the kwargs of every POST."""

    calls: list[dict] = []

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> FakeClient:
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def post(self, url, **kwargs) -> FakeResponse:
        FakeClient.calls.append({"url": url, **kwargs})
        return FakeResponse()


@pytest.fixture
def fake_httpx(monkeypatch):
    """Swap the module's `httpx` for a namespace with the fake client in it.

    Patching the module attribute rather than `httpx.AsyncClient` itself keeps the fake
    from leaking into anything else that happens to use httpx.
    """

    from learning_tutor.llm import ai_providers as mod

    FakeClient.calls = []
    monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=FakeClient))
    return FakeClient


@pytest.mark.parametrize(
    "provider,model,base_url",
    [
        (AIProvider.OLLAMA, "llama3.2", None),
        (AIProvider.OPENAI, "gpt-4o-mini", None),
        (AIProvider.CLAUDE, "claude-opus-5-5", None),
        (AIProvider.LM_STUDIO, "local-model", None),
        (AIProvider.CUSTOM, "whatever", "http://localhost:9999/v1"),
    ],
)
def test_every_provider_passes_the_configured_timeout(
    monkeypatch, fake_httpx, provider, model, base_url
):
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "222.5")
    instance = AIProviderFactory.create_provider(provider, "key", model, base_url)

    assert asyncio.run(instance.generate_text("hi")) == "ok"
    assert fake_httpx.calls[-1]["timeout"] == 222.5, provider

    assert asyncio.run(instance.generate_chat([{"role": "user", "content": "hi"}])) == "ok"
    assert fake_httpx.calls[-1]["timeout"] == 222.5, provider


def test_the_default_timeout_is_120_seconds(monkeypatch, fake_httpx):
    """The copied file used 60 s, which a local 12B model on a plan-sized prompt exceeds."""

    monkeypatch.delenv("LT_LLM_TIMEOUT_S", raising=False)
    asyncio.run(OllamaProvider("", "llama3.2", None).generate_text("hi"))
    assert fake_httpx.calls[-1]["timeout"] == 120.0


def test_the_timeout_is_read_per_call_not_at_import(monkeypatch, fake_httpx):
    instance = OllamaProvider("", "llama3.2", None)
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "5")
    asyncio.run(instance.generate_text("hi"))
    monkeypatch.setenv("LT_LLM_TIMEOUT_S", "600")
    asyncio.run(instance.generate_text("hi"))
    assert [call["timeout"] for call in fake_httpx.calls[-2:]] == [5.0, 600.0]


def test_no_hardcoded_timeout_survives_in_the_file():
    """The whole point of the change: ten call sites, none of them a literal."""

    from learning_tutor.llm import ai_providers as mod

    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "timeout=30.0" not in source
    assert "timeout=60.0" not in source
    assert source.count("timeout=request_timeout()") == 10


# ------------------------------------------------------------------- Claude Messages API


class ClaudeResponse:
    status_code = 200

    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


@pytest.fixture
def claude_http(monkeypatch):
    """Install a fake httpx whose POST always answers with ``body``; returns the calls."""

    from learning_tutor.llm import ai_providers as mod

    def install(body: dict) -> list[dict]:
        calls: list[dict] = []

        class Client:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc) -> bool:
                return False

            async def post(self, url, **kwargs):
                calls.append({"url": url, **kwargs})
                return ClaudeResponse(body)

        monkeypatch.setattr(mod, "httpx", SimpleNamespace(AsyncClient=Client))
        return calls

    return install


CHAT = [{"role": "system", "content": "be brief"}, {"role": "user", "content": "hi"}]


def test_claude_sends_no_temperature_and_room_for_thinking(claude_http):
    calls = claude_http({"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
    provider = ClaudeProvider("key", "claude-opus-5-5", None)

    assert asyncio.run(provider.generate_text("hi", temperature=0.7, max_tokens=4000)) == "ok"
    assert asyncio.run(provider.generate_chat(CHAT, temperature=0.3)) == "ok"

    assert len(calls) == 2
    for call in calls:
        payload = call["json"]
        assert call["url"].endswith("/messages")
        assert payload["model"] == "claude-opus-5-5"
        assert "temperature" not in payload
        assert "top_p" not in payload and "top_k" not in payload
        assert payload["max_tokens"] == 16000
    assert calls[1]["json"]["system"] == "be brief"


def test_claude_keeps_a_larger_max_tokens(claude_http):
    calls = claude_http({"stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]})
    asyncio.run(ClaudeProvider("key", "claude-opus-5-5", None).generate_text("hi", max_tokens=32000))
    assert calls[0]["json"]["max_tokens"] == 32000


def test_claude_reads_the_text_block_after_a_thinking_block(claude_http):
    claude_http(
        {
            "stop_reason": "end_turn",
            "content": [
                {"type": "thinking", "thinking": "", "signature": "sig"},
                {"type": "text", "text": "the answer"},
            ],
        }
    )
    provider = ClaudeProvider("key", "claude-opus-5-5", None)
    assert asyncio.run(provider.generate_text("hi")) == "the answer"
    assert asyncio.run(provider.generate_chat(CHAT)) == "the answer"


def test_claude_refusal_raises_a_clear_error(claude_http):
    claude_http(
        {
            "stop_reason": "refusal",
            "stop_details": {"type": "refusal", "category": "cyber", "explanation": "declined"},
            "content": [],
        }
    )
    provider = ClaudeProvider("key", "claude-opus-5-5", None)
    with pytest.raises(ClaudeRefusalError, match="refusal.*cyber"):
        asyncio.run(provider.generate_text("hi"))
    with pytest.raises(ClaudeRefusalError):
        asyncio.run(provider.generate_chat(CHAT))


def test_claude_max_tokens_raises_a_truncation_error(claude_http):
    claude_http(
        {
            "stop_reason": "max_tokens",
            "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "partial"}],
        }
    )
    provider = ClaudeProvider("key", "claude-opus-5-5", None)
    with pytest.raises(ClaudeTruncatedError, match="max_tokens"):
        asyncio.run(provider.generate_text("hi"))
    with pytest.raises(ClaudeTruncatedError):
        asyncio.run(provider.generate_chat(CHAT))
