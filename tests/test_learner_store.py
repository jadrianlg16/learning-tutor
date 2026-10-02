"""Schema, migrations, and the append-only guarantee."""

from __future__ import annotations

import sqlite3

import pytest

from learning_tutor.config import get_settings
from learning_tutor.learner import events as events_mod
from learning_tutor.learner.store import MIGRATIONS, Store, open_store


def test_migrations_apply_once_and_are_idempotent(settings):
    store = open_store(settings)
    assert store.schema_version() == MIGRATIONS[-1][0]
    store.close()

    again = open_store(settings)
    assert again.schema_version() == MIGRATIONS[-1][0]
    rows = again.query("SELECT version FROM schema_version")
    assert len(rows) == len(MIGRATIONS)
    again.close()


def test_wal_is_on(store):
    mode = store.one("PRAGMA journal_mode")[0]
    assert mode.lower() == "wal"


def test_expected_tables_exist(store):
    names = {
        r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type='table'")
    }
    for table in (
        "events",
        "items",
        "item_versions",
        "nodes",
        "node_aliases",
        "edges",
        "graph_versions",
        "misconceptions",
        "disputes",
        "sessions",
        "goals",
        "holdouts",
        "schema_version",
    ):
        assert table in names, table


def test_events_are_append_only(store):
    event = events_mod.append(store, kind="note", response="hello")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("UPDATE events SET response = 'tampered' WHERE event_id = ?", (event.event_id,))
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store.execute("DELETE FROM events WHERE event_id = ?", (event.event_id,))
    assert store.one("SELECT response FROM events WHERE event_id = ?", (event.event_id,))[
        "response"
    ] == "hello"


def test_event_validation(store):
    with pytest.raises(Exception, match="unknown event kind"):
        events_mod.append(store, kind="nonsense")
    with pytest.raises(Exception, match="assistance"):
        events_mod.append(store, kind="note", assistance_level=9)
    with pytest.raises(Exception, match="confidence"):
        events_mod.append(store, kind="note", confidence=7)


def test_data_dir_comes_from_env_not_a_hardcoded_path(monkeypatch, tmp_path):
    monkeypatch.setenv("LT_DATA_DIR", str(tmp_path / "elsewhere"))
    settings = get_settings()
    assert settings.data_dir == tmp_path / "elsewhere"
    store = Store(settings)
    assert store.db_path.exists()
    store.close()


def test_event_ids_sort_chronologically(store):
    ids = [events_mod.append(store, kind="note").event_id for _ in range(5)]
    assert ids == sorted(ids)


def test_a_store_opened_on_one_thread_can_be_used_and_closed_on_another(settings):
    """learner-svc's per-request dependency does exactly this under concurrent requests."""

    import threading

    from learning_tutor.learner.store import open_store

    store = open_store(settings)
    errors: list[BaseException] = []

    def elsewhere() -> None:
        try:
            store.one("SELECT 1")
            store.close()
        except BaseException as exc:  # pragma: no cover - the failure being guarded
            errors.append(exc)

    worker = threading.Thread(target=elsewhere)
    worker.start()
    worker.join()
    assert errors == []
