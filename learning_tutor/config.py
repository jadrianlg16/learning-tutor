"""Settings for the Learning Tutor.

Everything configurable comes from the environment with a default. No machine-specific
values live in this file: paths are relative to the process working directory unless the
environment says otherwise.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

_TRUE = {"1", "true", "yes", "on"}


def _env_str(name: str, default: str) -> str:
    value = os.environ.get(name)
    return default if value is None or value == "" else value


def _env_opt(name: str) -> str | None:
    value = os.environ.get(name)
    return None if value is None or value == "" else value


def _env_float(name: str, default: float) -> float:
    raw = _env_opt(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = _env_opt(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class Settings:
    """Resolved runtime settings. Build with :func:`get_settings`."""

    data_dir: Path
    vault_dir: Path
    vault_dir_explicit: bool
    holdout_fraction: float
    probe_budget: int
    known_threshold: float
    unknown_threshold: float
    evidence_model: str
    promote_min_uses: int
    holdout_delay_days: int
    minutes_per_node: int
    sessions_per_week: int
    desired_retention: float
    session_item_cap: int
    #: study tools (CONTRACTS.md, *Study tools*): new questions / new flashcards per day
    practice_new_per_day: int = 20
    cards_new_per_day: int = 20
    #: an answer on an item last answered at least this long ago is a delayed retrieval
    delayed_min_hours: int = 20
    #: mock exam time per question; 2.9 = the ISOFT Disciplinar timetable, 420 min / 143
    mock_minutes_per_item: float = 2.9

    # --- derived paths -------------------------------------------------
    @property
    def learner_dir(self) -> Path:
        return self.data_dir / "learner"

    @property
    def db_path(self) -> Path:
        return self.learner_dir / "events.db"

    @property
    def state_path(self) -> Path:
        return self.learner_dir / "state.json"

    @property
    def notes_path(self) -> Path:
        return self.learner_dir / "notes.md"

    @property
    def export_path(self) -> Path:
        return self.learner_dir / "events.jsonl"

    @property
    def sources_dir(self) -> Path:
        return self.data_dir / "sources"

    @property
    def learner_md_path(self) -> Path:
        return self.vault_dir / "learner.md"

    @property
    def sessions_dir(self) -> Path:
        return self.vault_dir / "sessions"

    def ensure_dirs(self) -> None:
        for path in (self.learner_dir, self.sources_dir, self.vault_dir, self.sessions_dir):
            path.mkdir(parents=True, exist_ok=True)


def get_settings(data_dir: str | os.PathLike[str] | None = None) -> Settings:
    """Read settings from the environment.

    ``data_dir`` (usually the CLI's ``--data-dir``) overrides ``LT_DATA_DIR``.
    """

    resolved_data = Path(data_dir) if data_dir else Path(_env_str("LT_DATA_DIR", "./data"))
    resolved_data = resolved_data.expanduser()
    vault_env = _env_opt("LT_VAULT_DIR")
    vault = Path(vault_env).expanduser() if vault_env else resolved_data / "vault"
    return Settings(
        data_dir=resolved_data,
        vault_dir=vault,
        vault_dir_explicit=vault_env is not None,
        holdout_fraction=_env_float("LT_HOLDOUT_FRACTION", 0.2),
        probe_budget=_env_int("LT_PROBE_BUDGET", 12),
        known_threshold=_env_float("LT_KNOWN_THRESHOLD", 0.85),
        unknown_threshold=_env_float("LT_UNKNOWN_THRESHOLD", 0.15),
        evidence_model=_env_str("LT_EVIDENCE_MODEL", "rules"),
        promote_min_uses=_env_int("LT_PROMOTE_MIN_USES", 3),
        holdout_delay_days=_env_int("LT_HOLDOUT_DELAY_DAYS", 7),
        minutes_per_node=_env_int("LT_MINUTES_PER_NODE", 15),
        sessions_per_week=_env_int("LT_SESSIONS_PER_WEEK", 3),
        desired_retention=_env_float("LT_DESIRED_RETENTION", 0.9),
        session_item_cap=_env_int("LT_SESSION_ITEM_CAP", 20),
        practice_new_per_day=_env_int("LT_PRACTICE_NEW_PER_DAY", 20),
        cards_new_per_day=_env_int("LT_CARDS_NEW_PER_DAY", 20),
        delayed_min_hours=_env_int("LT_DELAYED_MIN_HOURS", 20),
        mock_minutes_per_item=_env_float("LT_MOCK_MINUTES_PER_ITEM", 2.9),
    )
