"""Unit tests for telegram/policy.py — pure functions, no filesystem, no network.

Covers IDEA.md's *Downtime retrieval over Telegram* policy: budget, window, spacing,
live-session halving, back-off after unanswered pushes, pause after being ignored, the
kill switch, priority pass-through, and "nothing due -> None".
"""

from __future__ import annotations

from datetime import UTC, datetime

from telegram.config import TelegramSettings
from telegram.policy import kill_switch, pick_next, should_send
from telegram.state import PushHistory, PushRecord

TZ = UTC


def dt(day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=TZ)


def push(
    token: str,
    *,
    sent: datetime,
    status: str = "pending",
    resolved: datetime | None = None,
    item_id: str = "i_1",
) -> PushRecord:
    return PushRecord(
        token=token,
        item_id=item_id,
        item_version_id="iv_1",
        node_id="n_1",
        node_title="Node",
        session_id=None,
        sent_at=sent.isoformat(),
        stem="stem",
        answer_key="A",
        options=["A. one", "B. two"],
        status=status,
        resolved_at=resolved.isoformat() if resolved else None,
    )


def settings(**over) -> TelegramSettings:
    base = TelegramSettings()
    return TelegramSettings(**{**base.__dict__, **over})


# --------------------------------------------------------------------------- should_send
def test_should_send_ok_with_empty_history():
    ok, reason = should_send(dt(5, 10), PushHistory(), settings())
    assert ok is True
    assert reason == "ok"


def test_should_send_outside_window():
    ok, reason = should_send(dt(5, 7), PushHistory(), settings())
    assert ok is False
    assert reason == "outside window"

    ok, reason = should_send(dt(5, 22), PushHistory(), settings())
    assert ok is False
    assert reason == "outside window"


def test_should_send_budget_exhausted():
    history = PushHistory(
        pushes=[
            push("t1", sent=dt(5, 9), status="answered", resolved=dt(5, 9, 5)),
            push("t2", sent=dt(5, 12), status="answered", resolved=dt(5, 12, 5)),
            push("t3", sent=dt(5, 15), status="answered", resolved=dt(5, 15, 5)),
        ]
    )
    ok, reason = should_send(dt(5, 18), history, settings())
    assert ok is False
    assert "budget exhausted" in reason


def test_should_send_too_soon_since_last_push():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 10), status="answered", resolved=dt(5, 10, 1))])
    ok, reason = should_send(dt(5, 10, 30), history, settings(min_gap_min=90))
    assert ok is False
    assert "too soon" in reason


def test_should_send_gap_satisfied_after_min_gap():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 10), status="answered", resolved=dt(5, 10, 1))])
    ok, reason = should_send(dt(5, 11, 31), history, settings(min_gap_min=90))
    assert ok is True
    assert reason == "ok"


def test_should_send_pending_question_blocks_a_new_one():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 9))])  # still pending
    ok, reason = should_send(dt(5, 9, 30), history, settings())
    assert ok is False
    assert "still pending" in reason


def test_should_send_stale_pending_does_not_block():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 9))])  # pending, but old
    ok, reason = should_send(dt(5, 20), history, settings(pending_expiry_hours=6))
    # 11 hours later: no longer "still pending" — some other gate (window/gap) may still say no,
    # but it must not be the pending gate.
    assert reason != "previous question still pending"


def test_should_send_live_session_halves_budget():
    live_day = "2026-09-05"
    history = PushHistory(
        pushes=[
            push("t1", sent=dt(5, 9), status="answered", resolved=dt(5, 9, 5)),
            push("t2", sent=dt(5, 12), status="answered", resolved=dt(5, 12, 5)),
        ],
        live_session_dates=[live_day],
    )
    # budget=4 halves to 2 on a live-session day; 2 already sent today -> exhausted.
    ok, reason = should_send(dt(5, 18), history, settings(budget=4))
    assert ok is False
    assert "budget exhausted" in reason
    assert "2/2" in reason


def test_should_send_no_live_session_uses_full_budget():
    history = PushHistory(
        pushes=[
            push("t1", sent=dt(5, 9), status="answered", resolved=dt(5, 9, 5)),
            push("t2", sent=dt(5, 12), status="answered", resolved=dt(5, 12, 5)),
        ]
    )
    ok, reason = should_send(dt(5, 18), history, settings(budget=4))
    assert ok is True


def test_should_send_backoff_after_unanswered_streak():
    history = PushHistory(
        pushes=[push("t1", sent=dt(5, 9), status="answered", resolved=dt(5, 9, 5))],
        unanswered_streak=3,
    )
    ok, reason = should_send(dt(5, 18), history, settings(budget=3, backoff_after_unanswered=3))
    assert ok is False
    assert "1/1" in reason  # dropped to 1/day, and the one push already sent counts


def test_should_send_paused_short_circuits_everything():
    history = PushHistory(paused=True, paused_reason="7 days ignored")
    ok, reason = should_send(dt(5, 10), history, settings())
    assert ok is False
    assert "paused" in reason


def test_should_send_pauses_after_days_ignored():
    old_push = push("t1", sent=dt(1, 10), status="not_now", resolved=dt(1, 10, 1))
    ok, reason = should_send(dt(9, 10), PushHistory(pushes=[old_push]), settings(pause_after_days=7))
    assert ok is False
    assert "days ignored" in reason


def test_should_send_recent_response_resets_the_ignore_clock():
    history = PushHistory(
        pushes=[
            push("t1", sent=dt(1, 10), status="answered", resolved=dt(1, 10, 1)),
            push("t2", sent=dt(8, 10), status="answered", resolved=dt(8, 10, 1)),
        ]
    )
    ok, reason = should_send(dt(9, 10), history, settings(pause_after_days=7))
    assert "days ignored" not in reason


# --------------------------------------------------------------------------- pick_next
def _candidate(item_id, **over):
    base = {
        "mode": "review",
        "reason": "fragile node",
        "node_id": "n_1",
        "node_title": "Node",
        "item_id": item_id,
        "item_version_id": "iv_1",
        "stem": "stem",
        "options": ["A. one", "B. two"],
        "context": "in-session",
    }
    base.update(over)
    return base


def test_pick_next_returns_none_when_nothing_due():
    assert pick_next([], PushHistory(), dt(5, 10), settings()) is None


def test_pick_next_skips_picks_with_no_validated_item():
    candidates = [_candidate(None), _candidate("i_2")]
    picked = pick_next(candidates, PushHistory(), dt(5, 10), settings())
    assert picked["item_id"] == "i_2"


def test_pick_next_preserves_priority_order_from_the_cli():
    candidates = [_candidate("i_fsrs_due"), _candidate("i_fragile")]
    picked = pick_next(candidates, PushHistory(), dt(5, 10), settings())
    assert picked["item_id"] == "i_fsrs_due"


def test_pick_next_skips_item_with_a_pending_push():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 9), item_id="i_1")])
    candidates = [_candidate("i_1"), _candidate("i_2")]
    picked = pick_next(candidates, history, dt(5, 10), settings())
    assert picked["item_id"] == "i_2"


def test_pick_next_skips_item_recently_resolved_within_cooldown():
    history = PushHistory(
        pushes=[push("t1", sent=dt(5, 8), status="not_now", resolved=dt(5, 8, 1), item_id="i_1")]
    )
    candidates = [_candidate("i_1"), _candidate("i_2")]
    picked = pick_next(candidates, history, dt(5, 8, 10), settings(repeat_cooldown_min=90))
    assert picked["item_id"] == "i_2"


def test_pick_next_reoffers_item_after_cooldown_elapses():
    history = PushHistory(
        pushes=[push("t1", sent=dt(5, 8), status="not_now", resolved=dt(5, 8, 1), item_id="i_1")]
    )
    candidates = [_candidate("i_1")]
    picked = pick_next(candidates, history, dt(5, 10), settings(repeat_cooldown_min=90))
    assert picked is not None
    assert picked["item_id"] == "i_1"


# --------------------------------------------------------------------------- kill_switch
def test_kill_switch_false_with_no_history():
    assert kill_switch(PushHistory(), now=dt(20, 10)) is False


def test_kill_switch_true_below_threshold():
    pushes = []
    for day in range(1, 15):
        status = "answered" if day <= 3 else "expired"
        pushes.append(
            push(f"t{day}", sent=dt(day, 10), status=status, resolved=dt(day, 10, 5) if status == "answered" else None)
        )
    history = PushHistory(pushes=pushes)
    assert kill_switch(history, now=dt(15, 10)) is True


def test_kill_switch_false_above_threshold():
    pushes = []
    for day in range(1, 15):
        status = "answered" if day <= 6 else "expired"
        pushes.append(
            push(f"t{day}", sent=dt(day, 10), status=status, resolved=dt(day, 10, 5) if status == "answered" else None)
        )
    history = PushHistory(pushes=pushes)
    # 6/14 ~ 0.43 >= 0.30
    assert kill_switch(history, now=dt(15, 10)) is False


def test_kill_switch_not_now_counts_as_a_response():
    pushes = [
        push("t1", sent=dt(1, 10), status="not_now", resolved=dt(1, 10, 5)),
        push("t2", sent=dt(2, 10), status="not_now", resolved=dt(2, 10, 5)),
        push("t3", sent=dt(3, 10), status="expired"),
    ]
    history = PushHistory(pushes=pushes)
    # 2/3 responded (not_now counts) - well above 30%.
    assert kill_switch(history, now=dt(4, 10)) is False


def test_kill_switch_ignores_window_outside_range():
    pushes = [push(f"t{d}", sent=dt(d, 10), status="expired") for d in range(1, 6)]
    # All 5 pushes are older than the 14-day window relative to "now" 60 days later.
    history = PushHistory(pushes=pushes)
    now = datetime(2026, 12, 1, 10, tzinfo=TZ)
    assert kill_switch(history, now=now, window_days=14) is False


def test_kill_switch_recent_pending_push_not_counted_as_ignored():
    history = PushHistory(pushes=[push("t1", sent=dt(5, 10))])  # pending, sent minutes ago
    assert kill_switch(history, now=dt(5, 10, 30)) is False
