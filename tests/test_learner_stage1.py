"""The Stage 1 contract additions in the core: migration, idempotency, evaluation method.

These are the rules CONTRACTS.md added on 2026-09-05 (*Additions decided 2026-09-05* and
*Adopted from the Tutor MCP audit*), tested at the level they are implemented — the
transport-free service layer — so HTTP and MCP inherit them rather than re-implement them.
"""

from __future__ import annotations

import json
import sqlite3

import pytest
from conftest import ago, make_item

from learning_tutor.learner import api, views
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner import items as items_mod
from learning_tutor.learner.store import (
    CURRENT_SCHEMA_VERSION,
    SCHEMA_PATH,
    IdempotencyConflict,
    LearnerError,
    Store,
)


def answer(store, item, *, method, correct=True, assistance=0, context="in-session", days=0):
    return api.record_answer(
        store,
        session_id=None,
        item_id=item.item_id,
        response="a covector" if correct else "a vector",
        correct=correct,
        assistance_level=assistance,
        context=context,
        evaluation_method=method,
        ts=ago(days=days),
    )


# ------------------------------------------------------------------ migration v1 -> v2
def test_a_stage0_database_migrates_forward_without_losing_events(tmp_path, settings):
    """Create a database with migration 1 only, then let the code migrate it."""

    db = settings.db_path
    db.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(db)
    raw.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw.execute(
        "INSERT INTO schema_version(version, applied_at) VALUES (1, '2026-09-01T00:00:00Z')"
    )
    raw.execute(
        "INSERT INTO events(event_id, ts, kind, node_id, correct, assistance_level, "
        "channel, context) VALUES ('e_old', '2026-09-01T00:00:00Z', 'answer', "
        "'n_old_0000', 1, 0, 'claude-code', 'in-session')"
    )
    raw.commit()
    columns = {row[1] for row in raw.execute("PRAGMA table_info(events)")}
    assert "evaluation_method" not in columns, "precondition: this is a v1 database"
    raw.close()

    with Store(settings) as store:
        assert store.schema_version() == CURRENT_SCHEMA_VERSION >= 2
        columns = {row["name"] for row in store.query("PRAGMA table_info(events)")}
        assert "evaluation_method" in columns
        goal_columns = {row["name"] for row in store.query("PRAGMA table_info(goals)")}
        assert {"assessment", "source_priority"} <= goal_columns
        assert store.one("SELECT name FROM sqlite_master WHERE name = 'idempotency'")

        old = store.one("SELECT * FROM events WHERE event_id = 'e_old'")
        assert old["kind"] == "answer" and old["correct"] == 1
        assert old["evaluation_method"] is None, "a migration invents no history"


def test_migrating_twice_is_a_no_op(settings):
    with Store(settings) as store:
        assert store.migrate() == CURRENT_SCHEMA_VERSION
        assert store.migrate() == CURRENT_SCHEMA_VERSION


def test_a_stage0_history_still_derives_the_same_state_after_migrating(settings):
    """A migration must not retroactively invalidate a history it knows nothing about.

    A whole Stage 0 history — nodes, an item, three passing answers, none of them
    carrying an evaluation method because the column did not exist — has to keep deriving
    ``known`` after migration 2 adds the column.
    """

    settings.db_path.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(settings.db_path)
    raw.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    raw.execute(
        "INSERT INTO schema_version(version, applied_at) VALUES (1, '2026-09-01T00:00:00Z')"
    )
    raw.execute(
        "INSERT INTO nodes(node_id, title, slug, created_at, graph_version) "
        "VALUES ('n_legacy_0001', 'Legacy node', 'legacy-node', '2026-09-01T00:00:00Z', 1)"
    )
    raw.execute(
        "INSERT INTO items(item_id, node_id, status, current_version_id, author, created_at) "
        "VALUES ('i_legacy', 'n_legacy_0001', 'PRACTICE_EVIDENCE', 'iv_legacy', "
        "'author-model', '2026-09-01T00:00:00Z')"
    )
    raw.execute(
        "INSERT INTO item_versions(item_version_id, item_id, version, stem, options, answer, "
        "distractor_misconceptions, kind, components, author, created_at) "
        "VALUES ('iv_legacy', 'i_legacy', 1, 'legacy stem', '[\"a\",\"b\"]', 'a', '{}', 'mc', "
        "'[]', 'author-model', '2026-09-01T00:00:00Z')"
    )
    for n, (ts, context) in enumerate(
        (
            ("2026-09-01T01:00:00Z", "in-session"),
            ("2026-09-01T02:00:00Z", "in-session"),
            ("2026-09-02T02:00:00Z", "delayed"),
        )
    ):
        raw.execute(
            "INSERT INTO events(event_id, ts, node_id, item_version_id, kind, correct, "
            "idk, assistance_level, channel, context) VALUES (?, ?, 'n_legacy_0001', "
            "'iv_legacy', 'answer', 1, 0, 0, 'claude-code', ?)",
            (f"e_legacy_{n}", ts, context),
        )
    raw.commit()
    raw.close()

    with Store(settings) as store:
        assert store.schema_version() == CURRENT_SCHEMA_VERSION
        rows = store.query("SELECT evaluation_method FROM events")
        assert all(row["evaluation_method"] is None for row in rows)
        assert evidence_mod.derive(store, "n_legacy_0001").state == "known"


# ---------------------------------------------------------------- evaluation_method
def test_host_llm_passes_never_reach_known(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    for days, context in ((9, "in-session"), (8, "in-session"), (2, "delayed")):
        result = answer(store, item, method="host_llm", days=days, context=context)
        assert result["wrote_evidence"] is True
        assert result["self_graded"] is True
        assert result["counts_toward_mastery"] is False
        assert "self-graded" in result["note"]

    state = evidence_mod.derive(store, node)
    assert state.state == "unknown"
    assert state.independent_passes == 0
    assert state.self_graded_passes == 3
    assert state.fails == 0, "a self-graded pass is not a failure, it is simply not a pass"


def test_the_same_evidence_judged_independently_does_reach_known(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node, stem="independently judged")
    for days, context in ((9, "in-session"), (8, "in-session"), (2, "delayed")):
        answer(store, item, method="blind_solver", days=days, context=context)
    assert evidence_mod.derive(store, node).state == "known"


@pytest.mark.parametrize("method", ["blind_solver", "rubric", "human"])
def test_every_trusted_method_counts(store, graph, method):
    node = graph["Covectors"]
    item = make_item(store, node, stem=f"stem for {method}")
    for days, context in ((9, "in-session"), (8, "in-session"), (2, "delayed")):
        answer(store, item, method=method, days=days, context=context)
    assert evidence_mod.derive(store, node).state == "known"


def test_host_llm_is_the_default(store, graph):
    item = make_item(store, graph["Covectors"])
    result = api.record_answer(
        store, session_id=None, item_id=item.item_id, response="a covector", correct=True
    )
    assert result["evaluation_method"] == "host_llm"
    row = store.one("SELECT evaluation_method FROM events WHERE event_id = ?", (result["event_id"],))
    assert row["evaluation_method"] == "host_llm"


def test_a_teach_back_is_recorded_as_rubric_graded(store, graph):
    result = api.record_teach_back(
        store,
        session_id=None,
        node=graph["Covectors"],
        score=3,
        rubric_version="rubric-v1",
    )
    assert result["evaluation_method"] == "rubric"
    row = store.one("SELECT * FROM events WHERE event_id = ?", (result["event_id"],))
    assert row["evaluation_method"] == "rubric"
    assert row["grader_version"] == "rubric-v1", "the rubric is the grader"
    assert row["prompt_version"] == "teach/v1"


def test_an_unknown_evaluation_method_is_rejected(store, graph):
    item = make_item(store, graph["Covectors"])
    with pytest.raises(LearnerError, match="unknown evaluation method"):
        api.record_answer(
            store,
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            evaluation_method="vibes",
        )


def test_a_host_llm_validation_does_not_promote_the_item(store, graph):
    record = items_mod.add(store, graph["Covectors"], {"stem": "s", "options": ["a", "b"], "answer": "a"}, author="author-model")
    result = api.item_validate(
        store, record.item_id, by="another-model", result="pass", evaluation_method="host_llm"
    )
    assert result["status"] == "TEACHING_ONLY"
    assert "does not promote" in result["note"]

    promoted = api.item_validate(
        store, record.item_id, by="solver-model", result="pass", evaluation_method="blind_solver"
    )
    assert promoted["status"] == "PRACTICE_EVIDENCE"


def test_learner_md_shows_self_graded_passes(store, graph, goal):
    item = make_item(store, graph["Covectors"], stem="self graded stem")
    for days in (9, 8):
        answer(store, item, method="host_llm", days=days)
    _path, text = views.write_learner_md(store, goal)
    assert "Self-graded: Covectors" in text
    assert "none counted" in text
    assert not views.has_decimals(text)


# ---------------------------------------------------------------------- idempotency
def test_the_same_key_and_body_replays_the_first_response(store, graph):
    first = api.goal_add(
        store, goal_id="g_idem", title="Once", idempotency_key="key-1"
    )
    second = api.goal_add(
        store, goal_id="g_idem", title="Once", idempotency_key="key-1"
    )
    assert first == second
    assert len(store.query("SELECT 1 FROM goals WHERE goal_id = 'g_idem'")) == 1


def test_the_same_key_with_a_different_body_is_a_conflict(store):
    api.goal_add(store, goal_id="g_a", title="A", idempotency_key="key-2")
    with pytest.raises(IdempotencyConflict, match="already used for a different"):
        api.goal_add(store, goal_id="g_b", title="B", idempotency_key="key-2")


def test_a_replayed_answer_writes_no_second_event(store, graph):
    item = make_item(store, graph["Covectors"])
    kwargs = dict(
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        evaluation_method="rubric",
        idempotency_key="retry-1",
    )
    first = api.record_answer(store, **kwargs)
    second = api.record_answer(store, **kwargs)
    assert first["event_id"] == second["event_id"]
    rows = store.query("SELECT * FROM events WHERE kind IN ('answer','probe_answer')")
    assert len(rows) == 1, "a retried push must not double-count as a second attempt"


def test_no_key_means_no_replay(store, graph):
    item = make_item(store, graph["Covectors"])
    kwargs = dict(
        session_id=None, item_id=item.item_id, response="a covector", correct=True
    )
    first = api.record_answer(store, **kwargs)
    second = api.record_answer(store, **kwargs)
    assert first["event_id"] != second["event_id"]


def test_a_failed_call_stores_nothing_so_a_retry_runs_again(store, graph):
    with pytest.raises(LearnerError):
        api.goal_add(store, goal_id="g_bad", title="T", depth="telepathy", idempotency_key="k")
    assert store.one("SELECT 1 FROM idempotency WHERE key = 'k'") is None
    ok = api.goal_add(store, goal_id="g_bad", title="T", idempotency_key="k")
    assert ok["goal_id"] == "g_bad"


def test_the_stored_response_is_the_response(store):
    api.goal_add(store, goal_id="g_store", title="Stored", idempotency_key="key-3")
    row = store.one("SELECT * FROM idempotency WHERE key = 'key-3'")
    assert row["operation"] == "goal_add"
    assert len(row["body_sha256"]) == 64
    assert json.loads(row["response"])["goal_id"] == "g_store"


# ---------------------------------------------------------------------- goal fields
def test_a_goal_carries_its_assessment_and_source_priority(store):
    result = api.goal_add(
        store,
        goal_id="g_exam",
        title="Exam goal",
        assessment="closed-book exam, 3 questions on Stokes",
        source_priority="alignment",
    )
    assert result["assessment"].startswith("closed-book")
    assert result["source_priority"] == "alignment"
    row = api.goal_get(store, "g_exam")
    assert row["assessment"] == result["assessment"]
    assert row["source_priority"] == "alignment"


def test_an_unknown_source_priority_is_rejected(store):
    with pytest.raises(LearnerError, match="source priority"):
        api.goal_add(store, goal_id="g_nope", title="T", source_priority="loudest")
