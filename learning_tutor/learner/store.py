"""SQLite store: connection, WAL, forward migrations, small helpers.

Migrations are forward-only. Add a new ``(version, statements)`` entry to ``MIGRATIONS``;
never edit an applied one. ``schema.sql`` is migration 1 and is idempotent.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_V2_PATH = Path(__file__).with_name("schema_v2.sql")
SCHEMA_V3_PATH = Path(__file__).with_name("schema_v3.sql")
SCHEMA_V4_PATH = Path(__file__).with_name("schema_v4.sql")


class LearnerError(Exception):
    """Any expected failure. The CLI turns this into {"error": ...} + exit 1."""


class IdempotencyConflict(LearnerError):
    """Same idempotency key, different body. HTTP 409; CLI exit 1."""


class StateConflict(LearnerError):
    """The request is valid but the thing is not in a state that allows it (a mock
    already submitted, one already open). HTTP 409; CLI exit 1."""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_ts(value: str) -> datetime:
    text = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _sql(path: Path) -> str:
    return path.read_text(encoding="utf-8")


#: version -> list of SQL scripts. Forward-only: add an entry, never edit an applied one.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [_sql(SCHEMA_PATH)]),
    (2, [_sql(SCHEMA_V2_PATH)]),
    (3, [_sql(SCHEMA_V3_PATH)]),
    (4, [_sql(SCHEMA_V4_PATH)]),
]

#: the version this build of the code expects.
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1][0]


class Store:
    """Thin wrapper over a sqlite3 connection with WAL and migrations."""

    def __init__(self, settings: Settings | None = None, *, db_path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(db_path) if db_path else self.settings.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # One Store per request in learner-svc, used by one request at a time — but FastAPI
        # runs a sync dependency's setup and teardown on whichever threadpool worker is free,
        # so under concurrent requests the connection is opened on one thread and closed on
        # another. sqlite3's same-thread check turns that into a 500; the connection is never
        # shared between concurrent callers, so the check buys nothing here.
        self.conn = sqlite3.connect(self.db_path, isolation_level=None, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.migrate()

    # --- lifecycle -----------------------------------------------------
    def migrate(self) -> int:
        self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_version ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        applied = {row[0] for row in self.conn.execute("SELECT version FROM schema_version")}
        for version, scripts in MIGRATIONS:
            if version in applied:
                continue
            for script in scripts:
                self.conn.executescript(script)
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_version(version, applied_at) VALUES (?, ?)",
                (version, utcnow()),
            )
        return self.schema_version()

    def schema_version(self) -> int:
        row = self.conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
        return int(row["v"] or 0)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- query helpers -------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Cursor:
        return self.conn.execute(sql, tuple(params))

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[sqlite3.Row]:
        return list(self.conn.execute(sql, tuple(params)))

    def one(self, sql: str, params: Sequence[Any] = ()) -> sqlite3.Row | None:
        return self.conn.execute(sql, tuple(params)).fetchone()

    def insert(self, table: str, values: dict[str, Any]) -> None:
        cols = ", ".join(values)
        marks = ", ".join("?" for _ in values)
        self.conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(values.values()))

    def update(self, table: str, key: dict[str, Any], values: dict[str, Any]) -> None:
        sets = ", ".join(f"{c} = ?" for c in values)
        where = " AND ".join(f"{c} = ?" for c in key)
        self.conn.execute(
            f"UPDATE {table} SET {sets} WHERE {where}",
            (*values.values(), *key.values()),
        )

    def transaction(self) -> sqlite3.Connection:
        return self.conn


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def open_store(settings: Settings | None = None) -> Store:
    settings = settings or get_settings()
    settings.ensure_dirs()
    return Store(settings)
