"""The study tools through the gateway, with a real in-process learner-svc and no model.

CONTRACTS.md, *Study tools* → *Gateway additions*: the shapes web-ui builds against, the
channel the gateway stamps (``web``), and the one thing only the gateway knows — which
markdown files sit in the goal's sources folder.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient
from test_gateway_routes import SyncASGITransport
from test_study import KEY, MD

from learning_tutor.gateway.app import create_app
from learning_tutor.learner_svc.app import create_app as create_learner_app

GOAL = "egel-demo"


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
        json={
            "goal_id": GOAL,
            "title": "EGEL demo",
            "deadline": "2026-11-20",
            "minutes_per_session": 30,
            "sessions_per_week": 3,
        },
    )
    assert response.status_code == 200, response.text
    # a hand-filled folder under the underscore spelling, as the real goal has
    folder = settings.sources_dir / GOAL.replace("-", "_") / "materials"
    folder.mkdir(parents=True)
    (folder / "area1.md").write_text(MD, encoding="utf-8")
    (folder / "area1-key.md").write_text(KEY, encoding="utf-8")
    (folder / "notes.txt").write_text("not markdown", encoding="utf-8")
    return GOAL


def import_by_path(client) -> dict:
    response = client.post(
        f"/api/goals/{GOAL}/study/import",
        json={
            "path": "egel_demo/materials/area1.md",
            "key_path": "egel_demo/materials/area1-key.md",
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_overview_lists_markdown_from_both_folder_spellings(client, goal, settings):
    (settings.sources_dir / GOAL).mkdir(parents=True, exist_ok=True)
    (settings.sources_dir / GOAL / "own.md").write_text("# own\n", encoding="utf-8")
    data = client.get(f"/api/goals/{GOAL}/study").json()
    paths = [f["path"] for f in data["importable"]]
    assert paths == [
        "egel-demo/own.md",
        "egel_demo/materials/area1-key.md",
        "egel_demo/materials/area1.md",
    ]
    assert data["bank"]["total"] == 0 and data["cards"]["total"] == 0 and data["tables"] == []


def test_import_by_path_and_paths_cannot_leave_the_goal(client, goal):
    report = import_by_path(client)
    assert report["questions"]["imported"] == 2 and report["cards"]["imported"] == 3
    assert report["source"] == "egel_demo/materials/area1.md"
    for bad, status in (
        ("../learner/notes.md", 400),
        ("egel_demo/materials/notes.txt", 400),
        ("egel_demo/materials/missing.md", 404),
    ):
        response = client.post(f"/api/goals/{GOAL}/study/import", json={"path": bad})
        assert response.status_code == status, (bad, response.text)


def test_multipart_import_with_a_dry_run_first(client, goal):
    files = {
        "file": ("area1.md", MD.encode("utf-8"), "text/markdown"),
        "key_file": ("key.md", KEY.encode("utf-8"), "text/markdown"),
    }
    dry = client.post(
        f"/api/goals/{GOAL}/study/import",
        files=files,
        data={"what": ["questions", "tables"], "dry_run": "1"},
    ).json()
    assert dry["dry_run"] is True and dry["questions"]["imported"] == 2
    assert dry["cards"] is None and dry["tables"]["imported"] == 1
    assert client.get(f"/api/goals/{GOAL}/study").json()["bank"]["total"] == 0

    real = client.post(f"/api/goals/{GOAL}/study/import", files=files).json()
    assert real["source"] == "area1.md" and real["questions"]["imported"] == 2
    assert client.post(f"/api/goals/{GOAL}/study/import", data={"x": "1"}).status_code == 400


def test_patch_goal_moves_the_date_and_the_cadence(client, goal, settings):
    import_by_path(client)  # gives the goal a graph, so feasibility has something to count
    response = client.patch(
        f"/api/goals/{GOAL}", json={"deadline": "2026-12-04", "sessions_per_week": 5}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"]["deadline"] == {"from": "2026-11-20", "to": "2026-12-04"}
    assert body["goal"]["deadline"] == "2026-12-04" and body["goal"]["sessions_per_week"] == 5
    days = (date(2026, 12, 4) - datetime.now(UTC).date()).days
    assert body["feasibility"]["sessions_left"] == max(0, int(days * 5 / 7))
    read = client.get(f"/api/goals/{GOAL}").json()["goal"]
    assert (read["deadline"], read["sessions_per_week"]) == ("2026-12-04", 5)
    state = json.loads((settings.data_dir / "gateway" / "state.json").read_text(encoding="utf-8"))
    assert state["goals"][GOAL]["contract"]["sessions_per_week"] == 5
    assert client.patch(f"/api/goals/{GOAL}", json={"deadline": "soon"}).status_code == 400
    assert client.patch("/api/goals/nope", json={"deadline": "2026-12-04"}).status_code == 404


def test_practice_cards_tables_and_export_through_the_gateway(client, goal):
    import_by_path(client)

    served = client.get(f"/api/goals/{GOAL}/practice/next", params={"n": 1}).json()
    question = served["questions"][0]
    assert "answer" not in question and question["options"][1]["key"] == "B"
    answer = client.post(
        f"/api/goals/{GOAL}/practice/answer",
        json={"item_id": question["item_id"], "response": "B", "order": [0, 1, 2],
              "confidence": 3},
    ).json()
    assert answer["correct"] is True and answer["correct_answer"]["key"] == "B"
    assert client.get(f"/api/goals/{GOAL}/practice/review").json()["items"] == []

    card = client.get(f"/api/goals/{GOAL}/cards/next").json()["cards"][0]
    revealed = client.post(f"/api/goals/{GOAL}/cards/reveal", json={"item_id": card["item_id"]})
    assert revealed.json()["back"]
    reviewed = client.post(
        f"/api/goals/{GOAL}/cards/review", json={"item_id": card["item_id"], "rating": "easy"}
    ).json()
    assert reviewed["counts_toward_mastery"] is False
    bad = client.post(
        f"/api/goals/{GOAL}/cards/review", json={"item_id": card["item_id"], "rating": "meh"}
    )
    assert bad.status_code == 400

    export = client.get(f"/api/goals/{GOAL}/cards/export", params={"format": "tsv"})
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/tab-separated-values")
    assert "attachment" in export.headers["content-disposition"]
    assert len(export.text.splitlines()) == 3

    tables = client.get(f"/api/goals/{GOAL}/tables").json()["tables"]
    table = client.get(f"/api/goals/{GOAL}/tables/{tables[0]['table_id']}").json()
    assert table["columns"] == ["Criterio", "Funcional", "No funcional"]
    made = client.post(f"/api/goals/{GOAL}/tables/{table['table_id']}/cards").json()
    assert made["imported"] == 4
    overview = client.get(f"/api/goals/{GOAL}/study").json()
    assert overview["cards"]["total"] == 7 and overview["bank"]["total"] == 2
