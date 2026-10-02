"""cli_bridge's HTTP path (``LT_LEARNER_URL`` set) — request shape only, no real service.

httpx.get/post are monkeypatched so these tests exercise URL/param/body construction, not
network I/O or a running learner-svc.
"""

from __future__ import annotations

import httpx
import pytest

from telegram import cli_bridge
from telegram.config import TelegramSettings


class _FakeResponse:
    def __init__(self, json_body, status_code=200):
        self._json = json_body
        self.status_code = status_code
        self.text = str(json_body)

    def json(self):
        return self._json


def make_settings(**over) -> TelegramSettings:
    base = TelegramSettings(learner_url="http://localhost:5034")
    return TelegramSettings(**{**base.__dict__, **over})


def test_get_candidates_uses_http_when_learner_url_is_set(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResponse({"picks": [{"item_id": "i_1"}]})

    monkeypatch.setattr(httpx, "get", fake_get)

    picks = cli_bridge.get_candidates(make_settings(goal="g_forms"), n=5)

    assert picks == [{"item_id": "i_1"}]
    assert captured["url"] == "http://localhost:5034/v1/next"
    assert captured["params"] == {"mode": "review", "n": 5, "goal": "g_forms"}


def test_get_candidates_falls_back_to_cli_without_learner_url(monkeypatch):
    monkeypatch.setattr(
        cli_bridge, "_run_cli", lambda args: {"picks": [{"item_id": "i_2"}]}
    )
    settings = TelegramSettings(learner_url=None)

    picks = cli_bridge.get_candidates(settings, n=3)

    assert picks == [{"item_id": "i_2"}]


def test_record_answer_uses_http_post_with_idempotency_header(monkeypatch):
    captured = {}

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _FakeResponse({"ok": True})

    monkeypatch.setattr(httpx, "post", fake_post)

    result = cli_bridge.record_answer(
        make_settings(),
        session_id="s_1",
        item_id="i_1",
        response="B. right",
        correct=True,
        idk=False,
        assistance=0,
        context="delayed",
        idempotency_key="telegram-tok1",
    )

    assert result == {"ok": True}
    assert captured["url"] == "http://localhost:5034/v1/events"
    assert captured["json"]["kind"] == "answer"
    assert captured["json"]["channel"] == "telegram"
    assert captured["json"]["evaluation_method"] == "human"
    assert captured["json"]["correct"] is True
    assert captured["headers"] == {"Idempotency-Key": "telegram-tok1"}


def test_http_get_raises_learner_error_on_http_failure(monkeypatch):
    def fake_get(url, params=None, timeout=None):
        return _FakeResponse({"error": "boom"}, status_code=500)

    monkeypatch.setattr(httpx, "get", fake_get)

    with pytest.raises(cli_bridge.LearnerError):
        cli_bridge.get_candidates(make_settings(), n=1)


@pytest.mark.parametrize("var", ["GITHUB_ACTIONS", "FORCE_COLOR", "PY_COLORS"])
def test_evaluation_method_is_detected_when_help_would_be_styled(monkeypatch, var):
    """CI sets GITHUB_ACTIONS, which makes Typer render help as a colour terminal; the
    detection must still find the flag, or every tap is recorded as self-graded."""

    monkeypatch.setattr(cli_bridge, "_EVALUATION_METHOD_SUPPORTED", None)
    monkeypatch.setenv(var, "1" if var != "GITHUB_ACTIONS" else "true")
    assert cli_bridge._supports_evaluation_method() is True
