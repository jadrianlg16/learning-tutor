"""Shared fixtures. Every test gets its own data directory; nothing touches ./data."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from learning_tutor.config import get_settings
from learning_tutor.learner import api
from learning_tutor.learner import graph as graph_mod
from learning_tutor.learner import items as items_mod
from learning_tutor.learner.store import open_store

LT_VARS = [
    "LT_DATA_DIR",
    "LT_VAULT_DIR",
    "LT_HOLDOUT_FRACTION",
    "LT_PROBE_BUDGET",
    "LT_KNOWN_THRESHOLD",
    "LT_UNKNOWN_THRESHOLD",
    "LT_EVIDENCE_MODEL",
    "LT_PROMOTE_MIN_USES",
    "LT_HOLDOUT_DELAY_DAYS",
    "LT_MINUTES_PER_NODE",
    "LT_SESSIONS_PER_WEEK",
    "LT_DESIRED_RETENTION",
]

GRAPH = {
    "nodes": [
        {"title": "Vectors"},
        {"title": "Covectors", "aliases": ["dual vectors", "one-forms"]},
        {"title": "Wedge product"},
        {"title": "k-forms"},
        {"title": "Exterior derivative"},
    ],
    "edges": [
        {"from": "Vectors", "to": "Covectors", "type": "strict_prerequisite", "provenance": "model"},
        {"from": "Covectors", "to": "Wedge product", "type": "strict_prerequisite", "provenance": "model"},
        {"from": "Wedge product", "to": "k-forms", "type": "strict_prerequisite", "provenance": "model"},
        {"from": "k-forms", "to": "Exterior derivative", "type": "strict_prerequisite", "provenance": "model"},
    ],
}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    for var in LT_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LT_DATA_DIR", str(tmp_path / "data"))
    return tmp_path


@pytest.fixture
def data_dir(clean_env) -> Path:
    return clean_env / "data"


@pytest.fixture
def settings(clean_env):
    settings = get_settings()
    settings.ensure_dirs()
    return settings


@pytest.fixture
def store(settings):
    with open_store(settings) as store:
        yield store


@pytest.fixture
def goal(store) -> str:
    api.goal_add(
        store,
        goal_id="g_forms",
        title="Differential forms",
        depth="apply",
        deadline=(datetime.now(UTC) + timedelta(days=30)).date().isoformat(),
        minutes_per_session=45,
    )
    return "g_forms"


@pytest.fixture
def graph(store, goal) -> dict[str, str]:
    api.graph_import(store, goal, GRAPH)
    return {node.title: node.node_id for node in graph_mod.get_nodes(store, goal)}


def item_spec(stem: str = "What eats a vector and returns a number?", **over: Any) -> dict[str, Any]:
    spec = {
        "stem": stem,
        "options": ["a covector", "a vector", "a scalar"],
        "answer": "a covector",
        "distractor_misconceptions": {"a vector": "vectors and covectors are the same thing"},
        "kind": "mc",
        "components": ["dual space"],
    }
    spec.update(over)
    return spec


def make_item(
    store,
    node_id: str,
    *,
    stem: str = "What eats a vector and returns a number?",
    author: str = "author-model",
    validate: bool = True,
    promote: bool = False,
    uses: int = 0,
    **over: Any,
):
    """Create an item and walk it as far along the lifecycle as asked."""

    record = items_mod.add(store, node_id, item_spec(stem, **over), author=author)
    if validate:
        items_mod.validate(store, record.item_id, by="solver-model", result="pass")
    for i in range(uses):
        api.record_answer(
            store,
            session_id=None,
            item_id=record.item_id,
            response="a covector",
            correct=True,
            assistance_level=0,
            context="in-session",
            ts=ago(days=30 - i),
        )
    if promote:
        items_mod.promote(store, record.item_id)
    return items_mod.get(store, record.item_id)


def force_holdout(store, item_id: str, *, assigned_days_ago: float = 30) -> None:
    """Make one item a holdout regardless of its id hash.

    Holdout membership is a hash of a random item id, so a test that needs *a* holdout
    must not gamble on it; the hash rule itself is tested directly in
    test_learner_items.py with fixed ids.
    """

    store.update("items", {"item_id": item_id}, {"holdout": 1, "status": "MASTERY_ELIGIBLE"})
    store.execute(
        "INSERT OR IGNORE INTO holdouts(item_id, assigned_at) VALUES (?, ?)",
        (item_id, ago(days=assigned_days_ago)),
    )


def ago(days: float = 0, minutes: float = 0) -> str:
    when = datetime.now(UTC) - timedelta(days=days, minutes=minutes)
    return when.isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json(path: Path, data: Any) -> str:
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)
