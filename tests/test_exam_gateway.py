"""Exam prep through the gateway, with a real in-process learner-svc and no model.

CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams* → *Gateway additions*:
the shapes web-ui builds against, ``channel: web`` on everything the browser records, a
mock that belongs to another goal is not found, and a sealed folder is invisible to the
browser's importer.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from test_exam import BLUEPRINT, bank_md, right_key
from test_gateway_routes import SyncASGITransport

from learning_tutor.gateway.app import create_app
from learning_tutor.learner import api
from learning_tutor.learner.store import open_store
from learning_tutor.learner_svc.app import create_app as create_learner_app

GOAL = "egel-exam"


@pytest.fixture(autouse=True)
def _gateway_env(monkeypatch):
    for var in ("LT_LEARNER_URL", "LT_RENDER_URL", "LT_WEB_DIR", "LT_PROMPT_PACK_DIR"):
        monkeypatch.delenv(var, raising=False)


@pytest.fixture
def client(settings):
    app = create_app(
        settings,
        learner_transport=SyncASGITransport(create_learner_app(settings)),
        learner_url="http://learner-svc",
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def goal(client, settings) -> str:
    response = client.post(
        "/api/goals",
        json={"goal_id": GOAL, "title": "EGEL", "deadline": "2026-12-04",
              "minutes_per_session": 60, "sessions_per_week": 7},
    )
    assert response.status_code == 200, response.text
    folder = settings.sources_dir / GOAL.replace("-", "_") / "materials"
    sealed = folder / "sealed-mock"
    sealed.mkdir(parents=True)
    md, key = bank_md(4, "Q")
    (folder / "bank.md").write_text(md, encoding="utf-8")
    (folder / "bank-key.md").write_text(key, encoding="utf-8")
    mock_md, mock_key = bank_md(2, "M")
    (sealed / "mock.md").write_text(mock_md, encoding="utf-8")
    (sealed / "mock-key.md").write_text(mock_key, encoding="utf-8")
    (sealed / "SEALED").write_text("mock-exam questions: do not open\n", encoding="utf-8")
    return GOAL


def test_the_browser_importer_cannot_see_or_read_a_sealed_folder(client, goal):
    listed = [f["path"] for f in client.get(f"/api/goals/{GOAL}/study").json()["importable"]]
    assert listed == ["egel_exam/materials/bank-key.md", "egel_exam/materials/bank.md"]
    refused = client.post(
        f"/api/goals/{GOAL}/study/import",
        json={"path": "egel_exam/materials/sealed-mock/mock.md"},
    )
    assert refused.status_code == 400 and "sealed" in refused.text


def test_blueprint_practice_progress_and_a_mock_through_the_gateway(client, goal, settings):
    assert client.get(f"/api/goals/{GOAL}/blueprint").status_code == 404
    imported = client.post(
        f"/api/goals/{GOAL}/study/import",
        json={"path": "egel_exam/materials/bank.md",
              "key_path": "egel_exam/materials/bank-key.md", "what": ["questions"]},
    )
    assert imported.status_code == 200, imported.text
    # the harness side: a blueprint and a sealed, checked mock set (CLI/MCP, not the browser)
    folder = settings.sources_dir / "egel_exam" / "materials" / "sealed-mock"
    with open_store(settings) as store:
        api.goal_blueprint_set(store, GOAL, BLUEPRINT)
        api.study_import(
            store, GOAL, (folder / "mock.md").read_text(encoding="utf-8"),
            key_markdown=(folder / "mock-key.md").read_text(encoding="utf-8"),
            author="author-model", pool="mock",
        )
        for row in store.query("SELECT item_id FROM items WHERE pool = 'mock'"):
            api.item_blind_check(store, row["item_id"], answer="A", by="solver-model")

    blueprint = client.get(f"/api/goals/{GOAL}/blueprint").json()
    assert blueprint["total_items"] == 12 and len(blueprint["areas"]) == 2
    study = client.get(f"/api/goals/{GOAL}/study").json()
    assert study["blueprint"] is True and study["mock"] == {"sealed_available": 6, "open": None}

    served = client.get(f"/api/goals/{GOAL}/practice/next", params={"n": 3}).json()
    assert served["counts"]["focus"]["kind"] == "mixed"
    q = served["questions"][0]
    answer = client.post(
        f"/api/goals/{GOAL}/practice/answer",
        json={"item_id": q["item_id"], "response": right_key(q), "order": q["order"]},
    ).json()
    assert answer["correct"] is True
    progress = client.get(f"/api/goals/{GOAL}/progress").json()
    assert progress["totals"]["first_attempts"] == 1 and progress["days_left"] is not None

    opened = client.post(f"/api/goals/{GOAL}/mocks", json={"n": 3})
    assert opened.status_code == 200, opened.text
    sid = opened.json()["session_id"]
    assert client.post(f"/api/goals/{GOAL}/mocks", json={"n": 3}).status_code == 409
    assert client.get(f"/api/goals/other-goal/mocks/{sid}").status_code == 404
    first = opened.json()["questions"][0]
    result = client.post(
        f"/api/goals/{GOAL}/mocks/{sid}/submit",
        json={"answers": [{"item_id": first["item_id"], "response": right_key(first)}]},
    ).json()
    assert (result["n"], result["answered"], result["correct"]) == (3, 1, 1)
    with open_store(settings) as store:
        channels = {r["channel"] for r in store.query(
            "SELECT channel FROM events WHERE session_id = ? AND kind = 'answer'", (sid,))}
    assert channels == {"web"}
    listing = client.get(f"/api/goals/{GOAL}/mocks").json()
    assert listing["open"] is None and listing["mocks"][0]["correct"] == 1
    assert client.get(f"/api/goals/{GOAL}/mocks/{sid}").json()["status"] == "submitted"
