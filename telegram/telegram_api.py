"""A thin Telegram Bot API client, built on ``httpx`` — a real dependency of this repo
(``pyproject.toml``). Every function accepts ``dry_run``: when true, no network call is
made and the exact JSON payload that *would* have been POSTed is returned under
``{"dry_run": True, "payload": ...}`` — see IDEA.md's requirement that this job be testable
without a bot token.
"""

from __future__ import annotations

from typing import Any

import httpx

from .config import TelegramSettings

API_ROOT = "https://api.telegram.org"
_TIMEOUT = 15.0
_POLL_TIMEOUT = 35.0  # getUpdates long-polls up to `timeout` seconds server-side


class TelegramError(RuntimeError):
    """The Telegram Bot API rejected a call."""


def _api_url(settings: TelegramSettings, method: str) -> str:
    if not settings.bot_token:
        raise TelegramError("TELEGRAM_BOT_TOKEN is not set")
    return f"{API_ROOT}/bot{settings.bot_token}/{method}"


def _post(settings: TelegramSettings, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        resp = httpx.post(_api_url(settings, method), json=payload, timeout=_TIMEOUT)
    except httpx.HTTPError as exc:
        raise TelegramError(f"{method} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TelegramError(f"{method} failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


def _get(
    settings: TelegramSettings, method: str, params: dict[str, Any], *, timeout: float = _TIMEOUT
) -> dict[str, Any]:
    clean = {k: v for k, v in params.items() if v is not None}
    try:
        resp = httpx.get(_api_url(settings, method), params=clean, timeout=timeout)
    except httpx.HTTPError as exc:
        raise TelegramError(f"{method} failed: {exc}") from exc
    if resp.status_code >= 400:
        raise TelegramError(f"{method} failed: HTTP {resp.status_code} {resp.text}")
    return resp.json()


def send_message(
    settings: TelegramSettings,
    *,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {"chat_id": settings.chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if settings.dry_run:
        return {"dry_run": True, "method": "sendMessage", "payload": payload}
    return _post(settings, "sendMessage", payload)


def edit_message(
    settings: TelegramSettings,
    *,
    message_id: int,
    text: str,
    reply_markup: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "chat_id": settings.chat_id,
        "message_id": message_id,
        "text": text,
    }
    payload["reply_markup"] = reply_markup or {"inline_keyboard": []}
    if settings.dry_run:
        return {"dry_run": True, "method": "editMessageText", "payload": payload}
    return _post(settings, "editMessageText", payload)


def answer_callback_query(
    settings: TelegramSettings, *, callback_query_id: str, text: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text
    if settings.dry_run:
        return {"dry_run": True, "method": "answerCallbackQuery", "payload": payload}
    return _post(settings, "answerCallbackQuery", payload)


def get_updates(
    settings: TelegramSettings, *, offset: int | None, timeout: int = 25
) -> list[dict[str, Any]]:
    if settings.dry_run:
        return []  # no bot token to poll against; dry-run poll is a documented no-op
    data = _get(
        settings,
        "getUpdates",
        {"offset": offset, "timeout": timeout},
        timeout=max(_POLL_TIMEOUT, timeout + 10),
    )
    if not data.get("ok"):
        raise TelegramError(f"getUpdates failed: {data}")
    return list(data.get("result") or [])


__all__ = [
    "TelegramError",
    "send_message",
    "edit_message",
    "answer_callback_query",
    "get_updates",
]
