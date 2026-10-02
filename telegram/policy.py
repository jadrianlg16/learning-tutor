"""Pure policy functions for the Telegram micro-review push.

Nothing here touches the network, the filesystem, or the ``learner`` CLI — every function
takes a :class:`~telegram.state.PushHistory` snapshot and a ``now`` and returns a decision.
That is what makes them fully unit-testable and, per ``CONTRACTS.md`` hard rule 6, keeps the
"push retrieval, never content" rule enforceable in one place.

Implements IDEA.md *Downtime retrieval over Telegram*:

    priorities: due -> fragile -> misconception -> transfer -> priming
    defaults: 3 pushes/day, 09:00-21:00, >=90 min apart, halve on a live-session day,
    "not now" is data, 3 unanswered -> 1/day, 7 days ignored -> pause and ask.
    nothing due -> nothing sent.

A few numbers are judgement calls IDEA.md does not pin (the not-now/repeat cooldown, the
pending-expiry window, the minimum sample for the adaptive best-hours window). Each is a
named field on :class:`TelegramSettings` with its default documented in
``docs/modules/telegram.md`` as a HYPOTHESIS, exactly like the learner's own evidence
thresholds in ``docs/modules/learner.md``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from .config import TelegramSettings
from .state import ANSWERED, NOT_NOW, PENDING, PushHistory

# Response kinds that count as genuine engagement for the adaptive best-hours signal.
# "not now" is excluded on purpose: it is data about bad timing, not a good one.
_ENGAGED_STATUSES = {ANSWERED}
_MIN_SAMPLES_PER_HOUR = 3
_BEST_HOURS_COUNT = 3


# --------------------------------------------------------------------------- should_send
def should_send(
    now: datetime, history: PushHistory, settings: TelegramSettings
) -> tuple[bool, str]:
    """Whether *any* push may go out right now. Says nothing about content."""

    if history.paused:
        return False, f"paused: {history.paused_reason or 'ignored too long'}"

    pause_reason = _pause_reason(now, history, settings)
    if pause_reason:
        return False, pause_reason

    pending = history.pending()
    if pending is not None and not _pending_is_stale(pending, now, settings):
        return False, "previous question still pending"

    if not _in_window(now, history, settings):
        return False, "outside window"

    budget = _effective_budget(now, history, settings)
    today = now.date().isoformat()
    sent_today = len(history.sent_on(today))
    if sent_today >= budget:
        return False, f"budget exhausted for today ({sent_today}/{budget})"

    last_sent = history.last_sent_at()
    if last_sent is not None:
        gap = now - last_sent
        if gap < timedelta(minutes=settings.min_gap_min):
            remaining = settings.min_gap_min - gap.total_seconds() / 60
            return False, f"too soon since last push ({remaining:.0f} min left)"

    return True, "ok"


def _pending_is_stale(pending, now: datetime, settings: TelegramSettings) -> bool:
    sent_at = datetime.fromisoformat(pending.sent_at.replace("Z", "+00:00"))
    return now - sent_at >= timedelta(hours=settings.pending_expiry_hours)


def _pause_reason(now: datetime, history: PushHistory, settings: TelegramSettings) -> str | None:
    """7 days ignored -> pause and ask. Only fires once at least one push has gone out."""

    last_sent = history.last_sent_at()
    if last_sent is None:
        return None
    last_response = history.last_response_at()
    reference = last_response or last_sent
    # A response resets the clock; silence since the reference point is what pauses.
    since_engagement = now - reference
    if since_engagement >= timedelta(days=settings.pause_after_days):
        days = since_engagement.days
        return f"{days} days ignored"
    return None


def _in_window(now: datetime, history: PushHistory, settings: TelegramSettings) -> bool:
    if not (settings.window_start <= now.time() <= settings.window_end):
        return False
    best_hours = _best_hours(history, settings)
    if best_hours is not None and now.hour not in best_hours:
        return False
    return True


def _effective_budget(now: datetime, history: PushHistory, settings: TelegramSettings) -> int:
    budget = settings.budget
    if now.date().isoformat() in history.live_session_dates:
        budget = max(1, math.ceil(budget / 2))
    if history.unanswered_streak >= settings.backoff_after_unanswered:
        budget = min(budget, settings.backoff_budget)
    return max(1, budget)


def _best_hours(history: PushHistory, settings: TelegramSettings) -> set[int] | None:
    """The 3 hours with the best historical response rate, once >=2 weeks of data exist.

    Returns ``None`` (no restriction) until the push history spans at least
    ``settings.adapt_after_days`` distinct calendar days, or until fewer than
    :data:`_BEST_HOURS_COUNT` hour buckets have enough samples to be meaningful.
    """

    resolved = [p for p in history.pushes if p.status in (ANSWERED, NOT_NOW)]
    if not resolved:
        return None
    days_spanned = len({p.sent_at[:10] for p in resolved})
    if days_spanned < settings.adapt_after_days:
        return None

    by_hour: dict[int, list[bool]] = {}
    for push in resolved:
        hour = datetime.fromisoformat(push.sent_at.replace("Z", "+00:00")).hour
        by_hour.setdefault(hour, []).append(push.status in _ENGAGED_STATUSES)

    rates = [
        (hour, sum(hits) / len(hits))
        for hour, hits in by_hour.items()
        if len(hits) >= _MIN_SAMPLES_PER_HOUR
    ]
    if len(rates) < _BEST_HOURS_COUNT:
        return None
    rates.sort(key=lambda pair: (-pair[1], pair[0]))
    return {hour for hour, _rate in rates[:_BEST_HOURS_COUNT]}


# --------------------------------------------------------------------------- pick_next
def pick_next(
    candidates: list[dict[str, Any]],
    history: PushHistory,
    now: datetime,
    settings: TelegramSettings,
) -> dict[str, Any] | None:
    """Pick one candidate to push, or ``None`` — silence is a feature.

    ``candidates`` is the JSON ``picks`` list from ``learner next --mode review --n 5``
    (or the ``/v1/next`` equivalent), already in the priority order docs/modules/learner.md
    names as shared with the Telegram push: FSRS-due, fragile, misconception, transfer.
    This function only filters (no validated item yet; recently sent/declined) and takes
    the first survivor — it does not re-rank.
    """

    cooldown = timedelta(minutes=settings.repeat_cooldown_min)
    recent_item_ids: set[str] = set()
    for push in history.pushes:
        if not push.item_id:
            continue
        if push.status == PENDING:
            recent_item_ids.add(push.item_id)  # already waiting on a tap for this item
            continue
        if push.resolved_at:
            resolved_dt = datetime.fromisoformat(push.resolved_at.replace("Z", "+00:00"))
            if now - resolved_dt < cooldown:
                recent_item_ids.add(push.item_id)

    for candidate in candidates:
        item_id = candidate.get("item_id")
        if not item_id:
            continue  # "no validated item yet" — nothing to send
        if item_id in recent_item_ids:
            continue
        return candidate
    return None


def _sent_dt(push) -> datetime:
    return datetime.fromisoformat(push.sent_at.replace("Z", "+00:00"))


# --------------------------------------------------------------------------- kill_switch
DEFAULT_KILL_RATE = 0.30
DEFAULT_KILL_WINDOW_DAYS = 14
_PENDING_GRACE_HOURS = 6  # a pending push younger than this is "still deciding", not ignored


def kill_switch(
    history: PushHistory,
    *,
    now: datetime | None = None,
    rate: float = DEFAULT_KILL_RATE,
    window_days: int = DEFAULT_KILL_WINDOW_DAYS,
) -> bool:
    """True when the response rate over the trailing window is below ``rate``.

    IDEA.md: "response rate below 30% after two weeks -> cut the cadence, then cut the
    feature." A response is any tap — option, IDK, or *not now* — because all three prove
    the channel is alive; only silence counts against the feature. Returns ``False``
    (never kill) when there isn't at least one *resolved* push in the window, since a rate
    over an empty or all-pending denominator is not evidence either way.
    """

    now = now or datetime.now(UTC)
    cutoff = now - timedelta(days=window_days)
    considered = []
    for push in history.pushes:
        sent_at = _sent_dt(push)
        if sent_at < cutoff:
            continue
        if push.status == PENDING and now - sent_at < timedelta(hours=_PENDING_GRACE_HOURS):
            continue  # too soon to call it ignored
        considered.append(push)

    if not considered:
        return False

    responded = sum(1 for p in considered if p.status in (ANSWERED, NOT_NOW))
    return (responded / len(considered)) < rate


__all__ = [
    "should_send",
    "pick_next",
    "kill_switch",
    "DEFAULT_KILL_RATE",
    "DEFAULT_KILL_WINDOW_DAYS",
]
