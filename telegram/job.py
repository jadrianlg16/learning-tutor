"""The scheduler entrypoint: ``python -m telegram.job tick|poll``.

``tick`` decides whether to push one retrieval question and sends it. ``poll`` long-polls
``getUpdates``, maps a tap back to ``learner record answer`` (or to state-only for "not
now"), and edits the original message with 1-3 lines of feedback — the only place content
appears, and only after an attempt (CONTRACTS.md hard rule 1; IDEA.md "push retrieval,
never content").

Both commands are safe to run under ``LT_TG_DRY_RUN=1``: ``tick`` prints the exact JSON it
would have POSTed instead of calling Telegram; ``poll`` is a documented no-op in dry-run
(there is no bot to long-poll against without a token) — use :func:`handle_callback`
directly, as the tests do, to exercise tap-mapping without a network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import cli_bridge, formatting, policy, state
from . import telegram_api as tg
from .config import TelegramSettings, get_settings, load_dotenv_if_present
from .config import data_dir as _data_dir
from .state import PushHistory, PushRecord


def now_for(settings: TelegramSettings) -> datetime:
    """Wall-clock time the window/budget checks are evaluated against.

    ``LT_TG_TZ`` overrides; otherwise the host's local timezone — the same box the cron
    ticker runs on, so "09:00-21:00" means the operator's own day, not UTC.
    """

    if settings.tz_name:
        return datetime.now(ZoneInfo(settings.tz_name))
    return datetime.now().astimezone()


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# --------------------------------------------------------------------------- tick
def tick(now: datetime | None = None) -> dict[str, Any]:
    settings = get_settings()
    now = now or now_for(settings)
    data_dir = _data_dir()
    history = state.load(data_dir)
    dirty = _expire_stale_pending(history, now, settings)

    if history.paused:
        eligible, reason = False, f"paused: {history.paused_reason or 'ignored too long'}"
    else:
        eligible, reason = policy.should_send(now, history, settings)
        if not eligible and reason.endswith("days ignored"):
            history.paused = True
            history.paused_reason = reason
            dirty = True
            _send_pause_notice(settings, reason)

    if dirty:
        state.save(data_dir, history)

    if not eligible:
        return {"sent": False, "reason": reason}

    candidates = cli_bridge.get_candidates(settings)
    pick = policy.pick_next(candidates, history, now, settings)
    if pick is None:
        return {"sent": False, "reason": "nothing due"}

    item_version = None
    if pick.get("item_version_id"):
        item_version = cli_bridge.read_item_version(pick["item_version_id"])

    token = uuid.uuid4().hex[:12]
    message = formatting.build_question_message(pick, token)
    resp = tg.send_message(settings, text=message["text"], reply_markup=message["reply_markup"])

    message_id = None
    if not settings.dry_run:
        if not resp.get("ok"):
            raise tg.TelegramError(f"sendMessage was not ok: {resp}")
        message_id = resp["result"]["message_id"]

    context = "transfer" if item_version and item_version.get("surface_form") else "delayed"
    options = list(pick.get("options") or [])
    # Normalized to the same positional letter the keyboard uses, regardless of whether the
    # item's own `answer` field stores a letter or the full option text — see
    # formatting.resolve_answer_letter.
    answer_key = (
        formatting.resolve_answer_letter(options, item_version["answer"])
        if item_version and item_version.get("answer")
        else None
    )
    record = PushRecord(
        token=token,
        item_id=pick["item_id"],
        item_version_id=pick.get("item_version_id"),
        node_id=pick.get("node_id"),
        node_title=pick.get("node_title"),
        session_id=None,
        sent_at=_iso(now),
        stem=pick.get("stem"),
        answer_key=answer_key,
        options=options,
        context=context,
        status=state.PENDING,
        message_id=message_id,
    )
    history.pushes.append(record)
    state.save(data_dir, history)

    result: dict[str, Any] = {
        "sent": True,
        "token": token,
        "item_id": pick["item_id"],
        "node_title": pick.get("node_title"),
        "context": context,
    }
    if settings.dry_run:
        result["dry_run_payload"] = resp["payload"]
    return result


def _expire_stale_pending(history: PushHistory, now: datetime, settings: TelegramSettings) -> bool:
    pending = history.pending()
    if pending is None:
        return False
    if now - _parse(pending.sent_at) < timedelta(hours=settings.pending_expiry_hours):
        return False
    pending.status = state.EXPIRED
    pending.resolved_at = _iso(now)
    history.unanswered_streak += 1
    return True


def _send_pause_notice(settings: TelegramSettings, reason: str) -> None:
    text = (
        f"No response in {reason.split()[0]} days — pausing review pushes.\n\n"
        "Tap to keep getting them."
    )
    keyboard = {"inline_keyboard": [[{"text": "Resume reviews", "callback_data": "lt:resume:_"}]]}
    tg.send_message(settings, text=text, reply_markup=keyboard)


# --------------------------------------------------------------------------- poll
def poll(now: datetime | None = None) -> dict[str, Any]:
    settings = get_settings()
    now = now or now_for(settings)
    data_dir = _data_dir()
    history = state.load(data_dir)

    if settings.dry_run:
        return {
            "polled": 0,
            "note": "dry-run: no network poll; call handle_callback() directly to test taps",
        }

    updates = tg.get_updates(settings, offset=history.update_offset)
    handled = 0
    next_offset = history.update_offset
    for update in updates:
        next_offset = update["update_id"] + 1
        query = update.get("callback_query")
        if not query:
            continue
        handle_callback(settings, history, query, now)
        handled += 1
    history.update_offset = next_offset
    state.save(data_dir, history)
    return {"polled": handled}


def handle_callback(
    settings: TelegramSettings, history: PushHistory, query: dict[str, Any], now: datetime
) -> dict[str, Any]:
    """Map one Telegram callback_query to a learner record (or state-only for not-now)."""

    data = query.get("data", "")
    try:
        parsed = formatting.parse_callback_data(data)
    except ValueError:
        return {"handled": False, "reason": "not a micro-review callback"}

    kind, token = parsed["kind"], parsed["token"]

    if kind == "resume":
        history.paused = False
        history.paused_reason = None
        history.unanswered_streak = 0
        tg.answer_callback_query(settings, callback_query_id=query.get("id", ""), text="Resumed.")
        return {"handled": True, "kind": "resume"}

    push = history.find(token)
    if push is None or push.status != state.PENDING:
        tg.answer_callback_query(
            settings, callback_query_id=query.get("id", ""), text="This question has expired."
        )
        return {"handled": False, "reason": "unknown or already-resolved token"}

    if kind == "notnow":
        push.status = state.NOT_NOW
        push.resolved_at = _iso(now)
        history.unanswered_streak = 0  # a tap, even a decline, is a response
        text = formatting.build_not_now_message(push.node_title, push.stem)
        if push.message_id is not None:
            tg.edit_message(settings, message_id=push.message_id, text=text)
        tg.answer_callback_query(settings, callback_query_id=query.get("id", ""))
        return {"handled": True, "kind": "notnow", "token": token}

    idk = kind == "idk"
    picked_letter = parsed.get("letter")
    correct = (not idk) and picked_letter == push.answer_key
    picked_text = None if idk else formatting.option_by_letter(push.options, picked_letter or "")

    item_version = None
    if push.item_version_id:
        item_version = cli_bridge.read_item_version(push.item_version_id)
    distractors = (item_version or {}).get("distractor_misconceptions") or {}

    cli_bridge.record_answer(
        settings,
        session_id=push.session_id,
        item_id=push.item_id,
        response=picked_text or picked_letter,
        correct=correct,
        idk=idk,
        assistance=0,
        context=push.context,
        idempotency_key=f"telegram-{token}",
    )

    push.status = state.ANSWERED
    push.resolved_at = _iso(now)
    push.correct = correct
    push.idk = idk
    history.unanswered_streak = 0

    text = formatting.build_feedback_message(
        node_title=push.node_title,
        stem=push.stem,
        correct=correct,
        idk=idk,
        answer_key=push.answer_key or "",
        options=push.options,
        distractor_misconceptions=distractors,
        picked_letter=picked_letter,
    )
    if push.message_id is not None:
        tg.edit_message(settings, message_id=push.message_id, text=text)
    tg.answer_callback_query(settings, callback_query_id=query.get("id", ""))
    return {"handled": True, "kind": kind, "token": token, "correct": correct}


def _data_dir_repo_env() -> str:
    repo_dir = os.environ.get("LT_REPO_DIR")
    return str(Path(repo_dir) / ".env") if repo_dir else ".env"


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m telegram.job")
    parser.add_argument("command", choices=["tick", "poll"])
    args = parser.parse_args(argv)

    # Fill gaps from $LT_REPO_DIR/.env (or ./.env) without overriding real env vars — see
    # config.load_dotenv_if_present's docstring for why a cron script needs this at all.
    load_dotenv_if_present(_data_dir_repo_env())

    try:
        result = tick() if args.command == "tick" else poll()
    except (cli_bridge.LearnerError, tg.TelegramError) as exc:
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
