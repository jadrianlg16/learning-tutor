"""Tap mapping: a callback_query becomes the right `learner record answer` call — or, for
"not now", no learner-facing call at all.

Network and the ``learner`` CLI are stubbed here so these tests exercise only the mapping
logic in telegram/job.py and telegram/cli_bridge.py, not a real subprocess or Bot API call.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telegram import cli_bridge, job
from telegram.config import TelegramSettings
from telegram.state import ANSWERED, NOT_NOW, PENDING, PushHistory, PushRecord

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def make_settings(**over) -> TelegramSettings:
    base = TelegramSettings(dry_run=True, bot_token="t", chat_id="1")
    return TelegramSettings(**{**base.__dict__, **over})


def make_pending_push(**over) -> PushRecord:
    base = dict(
        token="tok1",
        item_id="i_1",
        item_version_id="iv_1",
        node_id="n_1",
        node_title="Covectors",
        session_id="s_1",
        sent_at=NOW.isoformat(),
        stem="What does a covector eat?",
        answer_key="B",
        options=["A. wrong", "B. right", "C. I do not know"],
        context="delayed",
        status=PENDING,
        message_id=555,
    )
    base.update(over)
    return PushRecord(**base)


@pytest.fixture(autouse=True)
def stub_item_lookup(monkeypatch):
    monkeypatch.setattr(
        cli_bridge,
        "read_item_version",
        lambda item_version_id: {
            "stem": "What does a covector eat?",
            "options": ["A. wrong", "B. right", "C. I do not know"],
            "answer": "B",
            "distractor_misconceptions": {"A": "confuses covectors and vectors"},
            "surface_form": None,
        },
    )


def test_option_tap_correct_records_correct_1(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_bridge,
        "record_answer",
        lambda settings, **kwargs: calls.append(kwargs) or {"ok": True},
    )
    history = PushHistory(pushes=[make_pending_push()], unanswered_streak=2)
    query = {"id": "cbq1", "data": "lt:opt:tok1:B"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["handled"] is True
    assert result["correct"] is True
    assert calls == [
        {
            "session_id": "s_1",
            "item_id": "i_1",
            "response": "B. right",
            "correct": True,
            "idk": False,
            "assistance": 0,
            "context": "delayed",
            "idempotency_key": "telegram-tok1",
        }
    ]
    push = history.find("tok1")
    assert push.status == ANSWERED
    assert push.correct is True
    assert history.unanswered_streak == 0  # any tap resets the streak


def test_option_tap_incorrect_records_correct_0(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_bridge,
        "record_answer",
        lambda settings, **kwargs: calls.append(kwargs) or {"ok": True},
    )
    history = PushHistory(pushes=[make_pending_push()])
    query = {"id": "cbq1", "data": "lt:opt:tok1:A"}

    job.handle_callback(make_settings(), history, query, NOW)

    assert calls[0]["correct"] is False
    assert calls[0]["response"] == "A. wrong"
    push = history.find("tok1")
    assert push.correct is False


def test_transfer_context_is_preserved_on_the_record_call(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_bridge,
        "record_answer",
        lambda settings, **kwargs: calls.append(kwargs) or {"ok": True},
    )
    history = PushHistory(pushes=[make_pending_push(context="transfer")])
    query = {"id": "cbq1", "data": "lt:opt:tok1:B"}

    job.handle_callback(make_settings(), history, query, NOW)

    assert calls[0]["context"] == "transfer"


def test_idk_tap_records_idk_true_and_correct_false(monkeypatch):
    calls = []
    monkeypatch.setattr(
        cli_bridge,
        "record_answer",
        lambda settings, **kwargs: calls.append(kwargs) or {"ok": True},
    )
    history = PushHistory(pushes=[make_pending_push()])
    query = {"id": "cbq1", "data": "lt:idk:tok1"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["kind"] == "idk"
    assert calls[0]["idk"] is True
    assert calls[0]["correct"] is False
    push = history.find("tok1")
    assert push.idk is True
    assert push.status == ANSWERED


def test_not_now_never_calls_record_answer(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("not now must never call learner record answer")

    monkeypatch.setattr(cli_bridge, "record_answer", fail)
    history = PushHistory(pushes=[make_pending_push()])
    query = {"id": "cbq1", "data": "lt:notnow:tok1"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["handled"] is True
    assert result["kind"] == "notnow"
    push = history.find("tok1")
    assert push.status == NOT_NOW
    assert push.correct is None  # not a wrong answer — it just never happened


def test_not_now_still_resets_the_unanswered_streak(monkeypatch):
    monkeypatch.setattr(cli_bridge, "record_answer", lambda *a, **k: {"ok": True})
    history = PushHistory(pushes=[make_pending_push()], unanswered_streak=2)
    query = {"id": "cbq1", "data": "lt:notnow:tok1"}

    job.handle_callback(make_settings(), history, query, NOW)

    assert history.unanswered_streak == 0


def test_unknown_token_is_reported_and_not_recorded(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("an unknown token must never record an answer")

    monkeypatch.setattr(cli_bridge, "record_answer", fail)
    history = PushHistory(pushes=[])
    query = {"id": "cbq1", "data": "lt:opt:doesnotexist:A"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["handled"] is False


def test_already_resolved_token_is_not_recorded_again(monkeypatch):
    def fail(*a, **k):
        raise AssertionError("an already-answered push must never be recorded twice")

    monkeypatch.setattr(cli_bridge, "record_answer", fail)
    history = PushHistory(pushes=[make_pending_push(status=ANSWERED, correct=True)])
    query = {"id": "cbq1", "data": "lt:opt:tok1:B"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["handled"] is False


def test_foreign_callback_data_is_ignored():
    history = PushHistory(pushes=[make_pending_push()])
    query = {"id": "cbq1", "data": "mp:model_picker:whatever"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result["handled"] is False
    assert history.find("tok1").status == PENDING  # untouched


def test_resume_callback_clears_pause_without_touching_pushes(monkeypatch):
    monkeypatch.setattr(
        "telegram.telegram_api.answer_callback_query", lambda *a, **k: {"ok": True}
    )
    history = PushHistory(paused=True, paused_reason="8 days ignored", unanswered_streak=5)
    query = {"id": "cbq1", "data": "lt:resume:_"}

    result = job.handle_callback(make_settings(), history, query, NOW)

    assert result == {"handled": True, "kind": "resume"}
    assert history.paused is False
    assert history.paused_reason is None
    assert history.unanswered_streak == 0


# --------------------------------------------------------------------------- CLI argv shape
def test_record_answer_builds_expected_argv(monkeypatch):
    captured = {}

    def fake_run_cli(args):
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(cli_bridge, "_run_cli", fake_run_cli)
    monkeypatch.setattr(cli_bridge, "_supports_evaluation_method", lambda: False)

    cli_bridge.record_answer(
        make_settings(),
        session_id="s_1",
        item_id="i_1",
        response="B",
        correct=True,
        idk=False,
        assistance=0,
        context="delayed",
    )

    args = captured["args"]
    assert args[:2] == ["record", "answer"]
    assert "--item" in args and args[args.index("--item") + 1] == "i_1"
    assert "--correct" in args and args[args.index("--correct") + 1] == "1"
    assert "--session" in args and args[args.index("--session") + 1] == "s_1"
    assert "--context" in args and args[args.index("--context") + 1] == "delayed"
    assert "--channel" in args and args[args.index("--channel") + 1] == "telegram"
    assert "--idk" not in args


def test_record_answer_idk_flag_present(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli_bridge, "_run_cli", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(cli_bridge, "_supports_evaluation_method", lambda: False)

    cli_bridge.record_answer(
        make_settings(),
        session_id=None,
        item_id="i_1",
        response=None,
        correct=False,
        idk=True,
        assistance=0,
        context="delayed",
    )

    args = captured["args"]
    assert "--idk" in args
    assert args[args.index("--correct") + 1] == "0"
    assert "--session" not in args


def test_record_answer_adds_evaluation_method_when_cli_supports_it(monkeypatch):
    captured = {}
    monkeypatch.setattr(cli_bridge, "_run_cli", lambda args: captured.setdefault("args", args))
    monkeypatch.setattr(cli_bridge, "_supports_evaluation_method", lambda: True)

    cli_bridge.record_answer(
        make_settings(),
        session_id="s_1",
        item_id="i_1",
        response="B",
        correct=True,
        context="delayed",
    )

    args = captured["args"]
    assert "--evaluation-method" in args
    assert args[args.index("--evaluation-method") + 1] == "human"
