"""Settings for the Telegram micro-review job — env in, dataclass out.

Every default below is the default named in ``IDEA.md`` *Downtime retrieval over Telegram*.
Nothing here is machine-specific: nothing is hardcoded except the defaults IDEA.md pins.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def load_dotenv_if_present(path: str | os.PathLike[str] = ".env") -> None:
    """Fill gaps in ``os.environ`` from a ``KEY=VALUE`` file, without overriding anything
    already set.

    Schedulers usually run a job with a minimal environment rather than a login shell, so a
    bot token exported only in the operator's interactive shell never reaches the ticker.
    Rather than add ``python-dotenv`` as a dependency, this is a deliberately tiny parser:
    one ``KEY=VALUE`` per line, ``#`` comments and blank lines skipped, no
    quoting/escaping/multiline support. Called once from :func:`telegram.job.main` against
    ``$LT_REPO_DIR/.env`` (or ``./.env``) — see ``docs/modules/telegram.md``.
    """

    file = Path(path)
    if not file.is_file():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_str(name: str, default: str | None) -> str | None:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in _TRUE


def _parse_window(raw: str) -> tuple[time, time]:
    start_s, _, end_s = raw.partition("-")
    start_h, start_m = (int(part) for part in start_s.strip().split(":"))
    end_h, end_m = (int(part) for part in end_s.strip().split(":"))
    return time(start_h, start_m), time(end_h, end_m)


@dataclass(frozen=True)
class TelegramSettings:
    """Resolved policy + transport settings. Build with :func:`get_settings`."""

    # --- policy (defaults from IDEA.md "Downtime retrieval over Telegram") ---
    budget: int = 3
    window_start: time = time(9, 0)
    window_end: time = time(21, 0)
    min_gap_min: int = 90
    backoff_after_unanswered: int = 3
    backoff_budget: int = 1
    pause_after_days: int = 7
    kill_rate: float = 0.30
    kill_window_days: int = 14
    adapt_after_days: int = 14
    # --- judgement calls not named by IDEA.md (documented as such in docs/modules/telegram.md) ---
    repeat_cooldown_min: int = 90
    pending_expiry_hours: int = 6
    # --- transport / wiring ---
    dry_run: bool = False
    goal: str | None = None
    learner_url: str | None = None
    bot_token: str | None = None
    chat_id: str | None = None
    tz_name: str | None = None


def data_dir() -> str:
    """``LT_DATA_DIR`` (the same variable the ``learner`` CLI reads), default ``./data``."""

    return _env_str("LT_DATA_DIR", "./data") or "./data"


def get_settings() -> TelegramSettings:
    """Read settings from the environment. No machine-specific value is hardcoded."""

    window_raw = _env_str("LT_TG_WINDOW", "09:00-21:00")
    window_start, window_end = _parse_window(window_raw)
    return TelegramSettings(
        budget=_env_int("LT_TG_BUDGET", 3),
        window_start=window_start,
        window_end=window_end,
        min_gap_min=_env_int("LT_TG_MIN_GAP_MIN", 90),
        backoff_after_unanswered=_env_int("LT_TG_BACKOFF_AFTER_UNANSWERED", 3),
        backoff_budget=_env_int("LT_TG_BACKOFF_BUDGET", 1),
        pause_after_days=_env_int("LT_TG_PAUSE_AFTER_DAYS", 7),
        kill_rate=_env_float("LT_TG_KILL_RATE", 0.30),
        kill_window_days=_env_int("LT_TG_KILL_WINDOW_DAYS", 14),
        adapt_after_days=_env_int("LT_TG_ADAPT_AFTER_DAYS", 14),
        repeat_cooldown_min=_env_int("LT_TG_REPEAT_COOLDOWN_MIN", 90),
        pending_expiry_hours=_env_int("LT_TG_PENDING_EXPIRY_HOURS", 6),
        dry_run=_env_bool("LT_TG_DRY_RUN", False),
        goal=_env_str("LT_TG_GOAL", None),
        learner_url=_env_str("LT_LEARNER_URL", None),
        bot_token=_env_str("TELEGRAM_BOT_TOKEN", None),
        chat_id=_env_str("TELEGRAM_CHAT_ID", None),
        tz_name=_env_str("LT_TG_TZ", None),
    )
