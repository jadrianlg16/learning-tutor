"""End-to-end dry-run ``tick`` against a real (temp) learner data dir.

Seeds a goal + graph + one validated item via the actual ``learner`` core (the same
fixtures ``tests/test_learner_*.py`` use), then runs ``telegram.job.tick`` in
``LT_TG_DRY_RUN`` mode, which shells out to the real ``learner`` CLI as a subprocess against
``LT_DATA_DIR`` (set by the shared ``clean_env`` fixture) but never touches the network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from telegram import job, state
from telegram.config import TelegramSettings

pytestmark = pytest.mark.usefixtures("clean_env")


def _set_bot_env(monkeypatch):
    monkeypatch.setenv("LT_TG_DRY_RUN", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setenv("LT_TG_WINDOW", "00:00-23:59")  # avoid flaking on the wall clock


IN_WINDOW_NOON = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


def test_tick_sends_nothing_when_nothing_is_due(monkeypatch, clean_env, goal, graph):
    _set_bot_env(monkeypatch)
    result = job.tick(now=IN_WINDOW_NOON)
    assert result["sent"] is False
    assert result["reason"] == "nothing due"

    history = state.load(str(clean_env / "data"))
    assert history.pushes == []


def test_tick_sends_the_dry_run_payload_for_a_fragile_item(monkeypatch, clean_env, store, goal, graph):
    _set_bot_env(monkeypatch)
    from tests.conftest import make_item

    node_id = graph["Covectors"]
    make_item(store, node_id, validate=True, uses=1)  # one independent pass -> fragile, not known

    result = job.tick(now=IN_WINDOW_NOON)

    assert result["sent"] is True
    assert result["node_title"] == "Covectors"
    payload = result["dry_run_payload"]
    assert payload["chat_id"] == "123456"
    assert "covector" in payload["text"].lower()
    keyboard = payload["reply_markup"]["inline_keyboard"]
    all_callbacks = [b["callback_data"] for row in keyboard for b in row]
    assert any(cb.startswith(f"lt:opt:{result['token']}:") for cb in all_callbacks)
    assert f"lt:idk:{result['token']}" in all_callbacks
    assert f"lt:notnow:{result['token']}" in all_callbacks

    # The pending push is now durable state, and it carries the answer key for grading —
    # but the payload sent to Telegram (asserted above) never does.
    history = state.load(str(clean_env / "data"))
    assert len(history.pushes) == 1
    pending = history.pushes[0]
    assert pending.status == state.PENDING
    # The fixture item's answer field is the full option text ("a covector"), normalized to
    # its position in the keyboard (index 0 -> "A") — see formatting.resolve_answer_letter.
    assert pending.answer_key == "A"
    assert pending.item_id


def test_tick_does_not_send_a_second_question_while_one_is_pending(
    monkeypatch, clean_env, store, goal, graph
):
    _set_bot_env(monkeypatch)
    from tests.conftest import make_item

    node_id = graph["Covectors"]
    make_item(store, node_id, validate=True, uses=1)

    first = job.tick(now=IN_WINDOW_NOON)
    assert first["sent"] is True

    second = job.tick(now=IN_WINDOW_NOON)
    assert second["sent"] is False
    assert "pending" in second["reason"]


def test_tick_respects_outside_window(monkeypatch, clean_env, store, goal, graph):
    monkeypatch.setenv("LT_TG_DRY_RUN", "1")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "dummy-token")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "123456")
    monkeypatch.setenv("LT_TG_WINDOW", "09:00-21:00")
    from tests.conftest import make_item

    node_id = graph["Covectors"]
    make_item(store, node_id, validate=True, uses=1)

    result = job.tick(now=datetime(2026, 9, 5, 3, 0, tzinfo=UTC))
    assert result == {"sent": False, "reason": "outside window"}


def test_full_round_trip_tick_then_tap_records_through_the_real_cli(
    monkeypatch, clean_env, store, goal, graph
):
    """tick() sends via the CLI's own picks; handle_callback() grades and records back
    through the same CLI — proving the answer key read out-of-band matches what the item
    bank actually holds, and that a correct tap becomes trusted evidence.

    This repo's ``learner record answer`` takes ``--evaluation-method``; ``cli_bridge.record_answer``
    feature-detects it and passes ``human`` when present (see
    ``cli_bridge._supports_evaluation_method``) — a Telegram tap is graded by exact string
    comparison against the stored answer key, which is genuinely human-grade evidence, not
    the tutoring model grading itself (``host_llm``, the CLI's default when the flag is
    omitted, which the fixture's own direct ``api.record_answer`` calls below hit and which
    is why ``before`` already shows a self-graded, uncounted pass from the fixture setup).
    """

    _set_bot_env(monkeypatch)
    from learning_tutor.learner import evidence as evidence_mod
    from tests.conftest import make_item

    node_id = graph["Covectors"]
    make_item(store, node_id, validate=True, uses=1)
    before = evidence_mod.derive_all(store, [node_id])[node_id]

    sent = job.tick(now=IN_WINDOW_NOON)
    assert sent["sent"] is True

    history = state.load(str(clean_env / "data"))
    push = history.pushes[0]
    settings = TelegramSettings(dry_run=True, bot_token="dummy-token", chat_id="123456")
    # push.answer_key is already the positional letter tick() resolved (see
    # formatting.resolve_answer_letter) — tapping it is tapping the correct option.
    query = {"id": "cbq1", "data": f"lt:opt:{push.token}:{push.answer_key}"}

    tap_result = job.handle_callback(settings, history, query, IN_WINDOW_NOON)
    state.save(str(clean_env / "data"), history)

    assert tap_result["handled"] is True
    assert tap_result["correct"] is True

    after = evidence_mod.derive_all(store, [node_id])[node_id]
    assert after.independent_passes == before.independent_passes + 1
    assert after.self_graded_passes == before.self_graded_passes  # unchanged by the tap
