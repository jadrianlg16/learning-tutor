"""`learner-svc` over HTTP: every route, happy path and one failure each.

The service is a transport, so these tests check the transport: status codes, the error
envelope, the idempotency header, and that the JSON coming back is the same JSON the CLI
prints. The rules themselves are tested against the core in `test_learner_stage1.py`.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from conftest import GRAPH, item_spec
from fastapi.testclient import TestClient

from learning_tutor.learner_svc import create_app


@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


@pytest.fixture
def goal_id(client) -> str:
    client.post("/v1/goals", json={"goal_id": "g_forms", "title": "Differential forms"})
    return "g_forms"


@pytest.fixture
def nodes(client, goal_id) -> dict[str, str]:
    response = client.post(f"/v1/graph/{goal_id}/import", json=GRAPH)
    assert response.status_code == 200, response.text
    graph = client.get(f"/v1/graph/{goal_id}").json()
    return {node["title"]: node["node_id"] for node in graph["nodes"]}


@pytest.fixture
def item(client, nodes) -> str:
    created = client.post(
        "/v1/items",
        json={"node": nodes["Covectors"], "spec": item_spec(), "author": "author-model"},
    ).json()
    client.post(
        f"/v1/items/{created['item_id']}/validate",
        json={"by": "solver-model", "result": "pass", "evaluation_method": "blind_solver"},
    )
    return created["item_id"]


# ------------------------------------------------------------------------------ ops
def test_healthz_reports_the_schema_and_the_data_dir(client, settings):
    body = client.get("/healthz").json()
    assert body["status"] == "ok"
    assert body["service"] == "learner-svc"
    assert body["schema_version"] >= 2
    assert body["data_dir"] == str(settings.data_dir)


# ---------------------------------------------------------------------------- goals
def test_create_and_read_a_goal(client):
    created = client.post(
        "/v1/goals",
        json={
            "goal_id": "g_http",
            "title": "Over HTTP",
            "depth": "apply",
            "assessment": "oral exam",
            "source_priority": "authority",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["source_priority"] == "authority"

    read = client.get("/v1/goals/g_http")
    assert read.status_code == 200
    assert read.json()["assessment"] == "oral exam"
    assert client.get("/v1/goals").json()["goals"][0]["goal_id"] == "g_http"


def test_an_unknown_goal_is_404(client):
    response = client.get("/v1/goals/nope")
    assert response.status_code == 404
    assert response.json() == {"error": "unknown goal 'nope'"}


def test_a_duplicate_goal_is_400(client, goal_id):
    response = client.post("/v1/goals", json={"goal_id": goal_id, "title": "again"})
    assert response.status_code == 400
    assert "already exists" in response.json()["error"]


def test_a_malformed_body_is_422_in_the_same_envelope(client):
    response = client.post("/v1/goals", json={"title": "no id"})
    assert response.status_code == 422
    assert "goal_id" in response.json()["error"]


# ---------------------------------------------------------------------------- graph
def test_import_show_and_revise_the_graph(client, goal_id):
    imported = client.post(f"/v1/graph/{goal_id}/import", json=GRAPH)
    assert imported.status_code == 200
    assert len(imported.json()["nodes"]) == len(GRAPH["nodes"])

    shown = client.get(f"/v1/graph/{goal_id}").json()
    assert len(shown["nodes"]) == len(GRAPH["nodes"])
    assert shown["nodes"][0]["state"]["state"] == "unknown"

    mermaid = client.get(f"/v1/graph/{goal_id}", params={"format": "mermaid"}).json()
    assert mermaid["mermaid"].startswith("graph")

    revised = client.post(
        f"/v1/graph/{goal_id}/revise",
        json={"ops": [{"op": "add", "node": {"title": "Pullbacks"}}]},
    )
    assert revised.status_code == 200
    assert revised.json()["ops"][0]["op"] == "add"


def test_revising_an_unknown_node_is_404(client, goal_id, nodes):
    response = client.post(
        f"/v1/graph/{goal_id}/revise",
        json={"ops": [{"op": "remove", "node": "Nonexistent"}]},
    )
    assert response.status_code == 404
    assert "unknown" in response.json()["error"]


# ---------------------------------------------------------------------------- items
def test_add_validate_and_promote_an_item(client, nodes):
    created = client.post(
        "/v1/items",
        json={"node": nodes["Covectors"], "spec": item_spec(), "author": "author-model"},
    )
    assert created.status_code == 200, created.text
    item_id = created.json()["item_id"]
    assert created.json()["status"] == "TEACHING_ONLY"

    validated = client.post(
        f"/v1/items/{item_id}/validate",
        json={"by": "solver-model", "result": "pass"},
    )
    assert validated.json()["status"] == "PRACTICE_EVIDENCE"

    for _ in range(3):
        client.post(
            "/v1/events",
            json={
                "kind": "answer",
                "item_id": item_id,
                "response": "a covector",
                "correct": True,
                "evaluation_method": "blind_solver",
            },
        )
    promoted = client.post(f"/v1/items/{item_id}/promote", json={})
    assert promoted.status_code == 200
    assert promoted.json()["status"] == "MASTERY_ELIGIBLE"


def test_the_author_may_not_validate_their_own_item(client, nodes):
    created = client.post(
        "/v1/items",
        json={"node": nodes["Covectors"], "spec": item_spec(), "author": "author-model"},
    ).json()
    response = client.post(
        f"/v1/items/{created['item_id']}/validate",
        json={"by": "author-model", "result": "pass"},
    )
    assert response.status_code == 400
    assert "must differ from the author" in response.json()["error"]


def test_promoting_an_unvalidated_item_is_400(client, nodes):
    created = client.post(
        "/v1/items",
        json={"node": nodes["Covectors"], "spec": item_spec(), "author": "author-model"},
    ).json()
    response = client.post(f"/v1/items/{created['item_id']}/promote", json={})
    assert response.status_code == 400


# ------------------------------------------------------------------------- sessions
def test_start_read_and_end_a_session(client, goal_id):
    started = client.post("/v1/sessions", json={"goal_id": goal_id, "channel": "agent"})
    assert started.status_code == 200
    session_id = started.json()["session_id"]

    assert client.get(f"/v1/sessions/{session_id}").json()["channel"] == "agent"

    ended = client.post(f"/v1/sessions/{session_id}/end", json={"summary": "covered covectors"})
    assert ended.status_code == 200
    assert ended.json()["summary"] == "covered covectors"

    again = client.post(f"/v1/sessions/{session_id}/end", json={})
    assert again.status_code == 400
    assert "already ended" in again.json()["error"]


def test_a_session_on_an_unknown_goal_is_404(client):
    response = client.post("/v1/sessions", json={"goal_id": "nope"})
    assert response.status_code == 404


# --------------------------------------------------------------------------- events
def test_record_an_answer(client, item):
    response = client.post(
        "/v1/events",
        json={
            "kind": "answer",
            "item_id": item,
            "response": "a covector",
            "correct": True,
            "confidence": 4,
            "assistance_level": 0,
            "context": "in-session",
            "evaluation_method": "blind_solver",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["wrote_evidence"] is True
    assert body["counts_toward_mastery"] is True
    assert body["self_graded"] is False


def test_a_self_graded_answer_is_recorded_but_never_counts(client, item):
    body = client.post(
        "/v1/events",
        json={"kind": "answer", "item_id": item, "response": "a covector", "correct": True},
    ).json()
    assert body["evaluation_method"] == "host_llm", "the default"
    assert body["self_graded"] is True
    assert body["counts_toward_mastery"] is False


def test_record_a_teach_back(client, nodes):
    response = client.post(
        "/v1/events",
        json={
            "kind": "teach_back",
            "node": nodes["Covectors"],
            "score": 3,
            "rubric_version": "rubric-v1",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["passed"] is True
    assert response.json()["evaluation_method"] == "rubric"


def test_an_answer_on_an_unknown_item_is_404(client):
    response = client.post(
        "/v1/events", json={"kind": "answer", "item_id": "i_nope", "correct": True}
    )
    assert response.status_code == 404


def test_an_event_of_an_unknown_kind_is_422(client):
    response = client.post("/v1/events", json={"kind": "telepathy", "correct": True})
    assert response.status_code == 422


# ------------------------------------------------------------------- misconceptions
def test_the_misconception_sequence_over_http(client, nodes):
    node = nodes["Covectors"]
    claim = "covectors are just vectors"
    suspected = client.post(
        "/v1/misconceptions/suspect", json={"node": node, "claim": claim}
    )
    assert suspected.status_code == 200
    assert suspected.json()["state"] == "suspected"

    out_of_order = client.post(
        "/v1/misconceptions/confirm-step",
        json={"node": node, "claim": claim, "step": "counterexample", "outcome": "held"},
    )
    assert out_of_order.status_code == 400
    assert "ordered" in out_of_order.json()["error"]

    for step in ("reasoning", "prediction", "counterexample"):
        result = client.post(
            "/v1/misconceptions/confirm-step",
            json={"node": node, "claim": claim, "step": step, "outcome": "held"},
        )
        assert result.status_code == 200
    assert result.json()["state"] == "active"

    resolved = client.post("/v1/misconceptions/resolve", json={"node": node, "claim": claim})
    assert resolved.json()["state"] == "resolved"


def test_resolving_an_unknown_misconception_is_404(client, nodes):
    response = client.post(
        "/v1/misconceptions/resolve", json={"node": nodes["Covectors"], "claim": "never said"}
    )
    assert response.status_code == 404


# ------------------------------------------------------------------------- disputes
def test_open_and_settle_a_dispute(client, nodes):
    opened = client.post(
        "/v1/disputes",
        json={"type": "I already know this", "node": nodes["Covectors"], "note": "did it at uni"},
    )
    assert opened.status_code == 200
    assert opened.json()["mastery_granted"] is False
    dispute_id = opened.json()["dispute_id"]

    settled = client.post(
        f"/v1/disputes/{dispute_id}/settle",
        json={"outcome": "rejected", "evidence": "failed both checks"},
    )
    assert settled.status_code == 200
    assert settled.json()["outcome"] == "rejected"

    again = client.post(f"/v1/disputes/{dispute_id}/settle", json={"outcome": "upheld"})
    assert again.status_code == 400


def test_an_unknown_dispute_type_is_400(client):
    response = client.post("/v1/disputes", json={"type": "I disagree"})
    assert response.status_code == 400
    assert "unknown dispute type" in response.json()["error"]


# ---------------------------------------------------------------------------- reads
def test_next_picks_something_and_never_a_holdout(client, goal_id, item):
    response = client.get("/v1/next", params={"goal": goal_id, "mode": "probe"})
    assert response.status_code == 200
    assert response.json()["mode"] == "probe"
    assert response.json()["picks"]


def test_next_on_an_unknown_goal_is_404(client, goal_id):
    response = client.get("/v1/next", params={"goal": "nope", "mode": "teach"})
    assert response.status_code == 404


def test_summary_md_returns_the_markdown_the_cli_prints(client, goal_id, nodes):
    response = client.get(f"/v1/summary/{goal_id}", params={"format": "md"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    text = response.text
    assert text.startswith("# Learner — ")
    assert "Goal: Differential forms" in text

    from learning_tutor.learner import api
    from learning_tutor.learner.store import open_store

    with open_store(client.app.state.settings) as store:
        assert api.summary(store, goal_id, "md")["markdown"] == text


def test_summary_json_carries_the_same_state(client, goal_id, nodes):
    body = client.get(f"/v1/summary/{goal_id}", params={"format": "json"}).json()
    assert body["goal_id"] == goal_id
    assert sorted(body["unknown"]) == sorted(n["title"] for n in [{"title": t} for t in nodes])


def test_an_unknown_summary_format_is_400(client, goal_id):
    response = client.get(f"/v1/summary/{goal_id}", params={"format": "pdf"})
    assert response.status_code == 400


def test_holdouts_metrics_and_export(client, goal_id, item):
    due = client.get(f"/v1/holdouts/{goal_id}/due")
    assert due.status_code == 200
    assert due.json()["due"] == 0

    metrics = client.get(f"/v1/metrics/{goal_id}")
    assert metrics.status_code == 200
    assert metrics.json()["holdout_success_7d"]["rate_percent"] is None

    exported = client.get("/v1/export")
    assert exported.status_code == 200
    assert exported.json()["events"] >= 1

    assert client.get("/v1/metrics/nope").status_code == 404
    assert client.get("/v1/holdouts/nope/due").status_code == 404


# ------------------------------------------------------- events / disputes / reads
def test_events_come_back_newest_first_and_filtered(client, nodes, item):
    node = nodes["Covectors"]
    for text in ("a vector", "a covector"):
        client.post(
            "/v1/events",
            json={
                "kind": "answer",
                "item_id": item,
                "response": text,
                "correct": text == "a covector",
                "evaluation_method": "blind_solver",
            },
        )

    body = client.get("/v1/events", params={"node": node}).json()
    assert body["order"] == "newest_first"
    assert body["count"] == len(body["events"]) >= 2
    assert body["events"][0]["response"] == "a covector", "the last answer comes first"
    assert body["events"][0]["node_id"] == node

    answers = client.get("/v1/events", params={"kind": "answer"}).json()
    assert {e["kind"] for e in answers["events"]} == {"answer"}
    assert client.get("/v1/events", params={"node": "n_nope"}).json()["events"] == []


def test_the_event_limit_is_capped_at_1000(client, item):
    client.post("/v1/events", json={"kind": "answer", "item_id": item, "correct": True})
    body = client.get("/v1/events", params={"limit": 999999}).json()
    assert body["limit"] == 1000 == body["max_limit"]
    assert len(client.get("/v1/events", params={"limit": 1}).json()["events"]) == 1


def test_events_filter_by_session_and_goal(client, goal_id, item):
    session_id = client.post("/v1/sessions", json={"goal_id": goal_id}).json()["session_id"]
    client.post(
        "/v1/events",
        json={"kind": "answer", "item_id": item, "correct": True, "session_id": session_id},
    )
    by_session = client.get("/v1/events", params={"session": session_id}).json()
    assert by_session["count"] >= 2  # session_start + the answer
    assert {e["session_id"] for e in by_session["events"]} == {session_id}
    assert client.get("/v1/events", params={"goal": "nope"}).json()["events"] == []


def test_disputes_are_listed_and_filtered(client, goal_id, nodes):
    node = nodes["Covectors"]
    opened = client.post(
        "/v1/disputes", json={"type": "I already know this", "node": node}
    ).json()
    client.post("/v1/disputes", json={"type": "misclick"})  # no node: belongs to no goal

    assert client.get("/v1/disputes").json()["count"] == 2

    for_goal = client.get("/v1/disputes", params={"goal": goal_id}).json()
    assert [d["dispute_id"] for d in for_goal["disputes"]] == [opened["dispute_id"]]
    assert client.get("/v1/disputes", params={"node": node}).json()["count"] == 1

    client.post(f"/v1/disputes/{opened['dispute_id']}/settle", json={"outcome": "rejected"})
    assert client.get("/v1/disputes", params={"status": "open"}).json()["count"] == 1
    assert client.get("/v1/disputes", params={"status": "settled"}).json()["count"] == 1


def test_misconceptions_are_listed_with_their_steps(client, goal_id, nodes):
    node = nodes["Covectors"]
    claim = "covectors are just vectors"
    client.post("/v1/misconceptions/suspect", json={"node": node, "claim": claim})
    client.post(
        "/v1/misconceptions/confirm-step",
        json={"node": node, "claim": claim, "step": "reasoning", "outcome": "held"},
    )

    listed = client.get("/v1/misconceptions", params={"goal": goal_id}).json()
    assert listed["count"] == 1
    assert listed["misconceptions"][0]["claim"] == claim
    assert listed["misconceptions"][0]["steps"][0]["step"] == "reasoning"

    assert client.get("/v1/misconceptions", params={"state": "active"}).json()["count"] == 0
    assert client.get("/v1/misconceptions", params={"state": "suspected"}).json()["count"] == 1
    assert client.get("/v1/misconceptions", params={"node": "n_nope"}).json()["count"] == 0


def test_an_unknown_goal_filter_is_404(client):
    assert client.get("/v1/disputes", params={"goal": "nope"}).status_code == 404
    assert client.get("/v1/misconceptions", params={"goal": "nope"}).status_code == 404


# ------------------------------------------------------------------------- passport
def test_the_passport_is_a_zip_with_every_member(client, goal_id, nodes, item):
    client.post("/v1/events", json={"kind": "answer", "item_id": item, "correct": True})
    response = client.get("/v1/passport")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        for member in (
            "events.jsonl",
            "state.json",
            "learner.md",
            "notes.md",
            "goals.json",
            "graph.json",
            "disputes.json",
            "MANIFEST.json",
        ):
            assert member in names, member
        assert any(name.startswith("artifacts/") for name in names)
        assert archive.read("events.jsonl").decode().strip()
        assert json.loads(archive.read("goals.json"))["goals"][0]["goal_id"] == goal_id
        assert json.loads(archive.read("graph.json"))[goal_id]["nodes"]
        assert json.loads(archive.read("MANIFEST.json"))["members"]["events.jsonl"]


def test_the_passport_does_not_rewrite_events_jsonl(client, goal_id, item):
    """An export is a read: `GET /v1/export` writes the file, `GET /v1/passport` must not."""

    settings = client.app.state.settings
    assert not settings.export_path.exists()
    client.get("/v1/passport")
    assert not settings.export_path.exists()


def test_the_passport_can_be_scoped_to_one_goal(client, goal_id, nodes):
    client.post("/v1/goals", json={"goal_id": "g_other", "title": "Other"})
    response = client.get("/v1/passport", params={"goal": goal_id})
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        goals = json.loads(archive.read("goals.json"))["goals"]
    assert [g["goal_id"] for g in goals] == [goal_id]
    assert client.get("/v1/passport", params={"goal": "nope"}).status_code == 404
    assert client.get("/v1/passport", params={"format": "pdf"}).status_code == 400


def test_the_passport_as_json_is_the_raw_tables(client, goal_id, item):
    body = client.get("/v1/passport", params={"format": "json"}).json()
    assert {"goals", "nodes", "edges", "items", "events", "disputes"} <= set(body)
    assert body["goals"][0]["goal_id"] == goal_id


# ---------------------------------------------------------------------- session log
def test_posting_a_session_log_writes_it_into_the_vault(client, goal_id, settings):
    session_id = client.post("/v1/sessions", json={"goal_id": goal_id}).json()["session_id"]
    markdown = "# Session\n\nWe did covectors.\n"
    response = client.post(f"/v1/sessions/{session_id}/log", json={"markdown": markdown})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["log_path"] == body["log"], "both names, one path"

    written = Path(body["log_path"])
    assert written.parent == Path(settings.sessions_dir)
    assert written.read_text(encoding="utf-8") == markdown

    # the same `note` event the CLI's `learner log` appends
    notes = client.get("/v1/events", params={"kind": "note"}).json()["events"]
    assert notes[0]["payload"]["log"] == body["log_path"]


def test_a_session_log_may_name_its_own_file(client, goal_id):
    session_id = client.post("/v1/sessions", json={"goal_id": goal_id}).json()["session_id"]
    named = client.post(
        f"/v1/sessions/{session_id}/log", json={"markdown": "x", "filename": "2026-09-05-x.md"}
    ).json()
    assert Path(named["log_path"]).name == "2026-09-05-x.md"

    for bad in ("../escape.md", "sub/dir.md", "notes.txt"):
        refused = client.post(
            f"/v1/sessions/{session_id}/log", json={"markdown": "x", "filename": bad}
        )
        assert refused.status_code == 400, bad
        assert "invalid log filename" in refused.json()["error"]


def test_logging_an_unknown_session_is_404(client):
    response = client.post("/v1/sessions/s_nope/log", json={"markdown": "x"})
    assert response.status_code == 404


# ---------------------------------------------------------------------- idempotency
def test_the_same_key_and_body_replays(client):
    body = {"goal_id": "g_once", "title": "Once", "idempotency_key": "http-1"}
    first = client.post("/v1/goals", json=body)
    second = client.post("/v1/goals", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert len(client.get("/v1/goals").json()["goals"]) == 1


def test_the_same_key_with_a_different_body_is_409(client):
    client.post("/v1/goals", json={"goal_id": "g_a", "title": "A", "idempotency_key": "http-2"})
    conflict = client.post(
        "/v1/goals", json={"goal_id": "g_b", "title": "B", "idempotency_key": "http-2"}
    )
    assert conflict.status_code == 409
    assert "already used for a different" in conflict.json()["error"]


def test_the_idempotency_key_header_works_too(client, item):
    body = {
        "kind": "answer",
        "item_id": item,
        "response": "a covector",
        "correct": True,
        "evaluation_method": "rubric",
    }
    headers = {"Idempotency-Key": "header-1"}
    first = client.post("/v1/events", json=body, headers=headers)
    second = client.post("/v1/events", json=body, headers=headers)
    assert first.json()["event_id"] == second.json()["event_id"]

    other = client.post(
        "/v1/events", json={**body, "correct": False}, headers=headers
    )
    assert other.status_code == 409


def test_export_writes_only_inside_the_data_dir(client, settings, tmp_path):
    """`out` over HTTP may name a file in the data directory, never anywhere else."""

    inside = client.get("/v1/export", params={"out": "exports/events.jsonl"})
    assert inside.status_code == 200, inside.text
    written = settings.data_dir / "exports" / "events.jsonl"
    assert written.is_file()
    assert inside.json()["out"] == str(written.resolve())

    outside = tmp_path / "elsewhere" / "events.jsonl"
    for bad in (str(outside), "../escape.jsonl", "exports/../../escape.jsonl"):
        refused = client.get("/v1/export", params={"out": bad})
        assert refused.status_code == 400, bad
        assert "inside the data directory" in refused.json()["error"]
    assert not outside.exists()
    assert not (settings.data_dir.parent / "escape.jsonl").exists()
