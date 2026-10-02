"""SQLite store for the corpus: connection, WAL, forward-only migrations, row helpers.

The corpus database is ``LT_DATA_DIR/corpus/corpus.db``. It is a *second* store, separate
from the learner event store, because it holds material rather than evidence: deleting a
source must never touch a learner event.

Migrations are forward-only. Add a new ``(version, statements)`` entry to ``MIGRATIONS``;
never edit an applied one. ``schema.sql`` is migration 1 and is idempotent.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..config import Settings, get_settings

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


class CorpusError(Exception):
    """Any expected failure. HTTP turns this into a 400/404 with {"error": ...}."""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _schema_sql() -> str:
    return SCHEMA_PATH.read_text(encoding="utf-8")


#: version -> list of SQL scripts. Forward-only.
MIGRATIONS: list[tuple[int, list[str]]] = [
    (1, [_schema_sql()]),
]


def corpus_dir(settings: Settings | None = None) -> Path:
    """``LT_DATA_DIR/corpus/``. Derived here so config.py stays owned by the learner core."""

    settings = settings or get_settings()
    return settings.data_dir / "corpus"


def corpus_db_path(settings: Settings | None = None) -> Path:
    return corpus_dir(settings) / "corpus.db"


def goal_sources_dir(goal_id: str, settings: Settings | None = None) -> Path:
    """``LT_DATA_DIR/sources/<goal_id>/`` — uploads, sources.md, research output."""

    settings = settings or get_settings()
    return settings.sources_dir / goal_id


class CorpusStore:
    """Thin wrapper over a sqlite3 connection with WAL and migrations."""

    def __init__(self, settings: Settings | None = None, *, db_path: Path | None = None) -> None:
        self.settings = settings or get_settings()
        self.db_path = Path(db_path) if db_path else corpus_db_path(self.settings)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: under FastAPI one request hops between the event loop
        # and the threadpool (sync dependency → async endpoint → sync endpoint), and the
        # store is opened and closed per request, so exactly one thread uses it at a time.
        self.conn = sqlite3.connect(
            self.db_path, isolation_level=None, check_same_thread=False
        )
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

    def __enter__(self) -> CorpusStore:
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


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def loads(value: str | None, default: Any) -> Any:
    """JSON columns are written by us, but a hand-edited db should not crash a read."""

    if not value:
        return default
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return default


def open_store(settings: Settings | None = None) -> CorpusStore:
    settings = settings or get_settings()
    settings.ensure_dirs()
    corpus_dir(settings).mkdir(parents=True, exist_ok=True)
    return CorpusStore(settings)


# --- source / chunk reads used across the module -------------------------


def _source_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["meta"] = loads(data.get("meta"), {})
    return data


def chunk_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["locator"] = loads(data.get("locator"), {})
    data["flags"] = loads(data.get("flags"), [])
    return data


def get_source(store: CorpusStore, source_id: str) -> dict[str, Any] | None:
    row = store.one("SELECT * FROM sources WHERE source_id = ?", (source_id,))
    return _source_row(row) if row else None


def list_sources(
    store: CorpusStore, goal_id: str, *, role: str | None = None
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM sources WHERE goal_id = ?"
    params: list[Any] = [goal_id]
    if role:
        sql += " AND role = ?"
        params.append(role)
    # rowid, not ingested_at: two sources ingested in the same second must still
    # come back in insertion order.
    sql += " ORDER BY rowid"
    return [_source_row(row) for row in store.query(sql, params)]


def source_by_sha(store: CorpusStore, goal_id: str, sha256: str) -> dict[str, Any] | None:
    row = store.one(
        "SELECT * FROM sources WHERE goal_id = ? AND sha256 = ?",
        (goal_id, sha256),
    )
    return _source_row(row) if row else None


CHUNK_SELECT = (
    "SELECT c.*, s.goal_id, s.role, s.kind, s.title, s.path_or_url "
    "FROM chunks c JOIN sources s ON s.source_id = c.source_id"
)


def get_chunk(store: CorpusStore, chunk_id: str) -> dict[str, Any] | None:
    row = store.one(f"{CHUNK_SELECT} WHERE c.chunk_id = ?", (chunk_id,))
    return chunk_row(row) if row else None


def list_chunks(
    store: CorpusStore,
    goal_id: str,
    *,
    role: str | None = None,
    source_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    sql = f"{CHUNK_SELECT} WHERE s.goal_id = ?"
    params: list[Any] = [goal_id]
    if role:
        sql += " AND s.role = ?"
        params.append(role)
    if source_id:
        sql += " AND c.source_id = ?"
        params.append(source_id)
    sql += " ORDER BY s.rowid, c.ordinal"
    if limit:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [chunk_row(row) for row in store.query(sql, params)]


def get_structure(
    store: CorpusStore, goal_id: str, *, source_id: str | None = None
) -> list[dict[str, Any]]:
    sql = (
        "SELECT st.*, s.title AS source_title, s.role, s.kind "
        "FROM structure st JOIN sources s ON s.source_id = st.source_id WHERE s.goal_id = ?"
    )
    params: list[Any] = [goal_id]
    if source_id:
        sql += " AND st.source_id = ?"
        params.append(source_id)
    sql += " ORDER BY s.rowid, st.ordinal"
    out: list[dict[str, Any]] = []
    for row in store.query(sql, params):
        data = dict(row)
        data["locator"] = loads(data.get("locator"), {})
        out.append(data)
    return out


def delete_source(store: CorpusStore, source_id: str) -> bool:
    """Cascades to chunks (and, via the FTS triggers, to the index) and structure."""

    cur = store.execute("DELETE FROM sources WHERE source_id = ?", (source_id,))
    return cur.rowcount > 0
