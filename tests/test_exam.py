"""Exam prep: the blueprint, mixed and shuffled practice, sealed mocks, progress.

CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. The rules under test:
new questions follow the blueprint's weights from the first pick, a question is graded by
the order it was shown in, a sealed question is invisible outside a mock until a mock has
used it, a mock gives nothing away before submit and grades everything once, and progress
counts first tries — never a migrated copy of an answer.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest
from fastapi.testclient import TestClient

from learning_tutor.learner import api, exam
from learning_tutor.learner.store import LearnerError, StateConflict
from learning_tutor.learner_svc import create_app

GOAL = "exam"
AUTHOR = "author-model"
SOLVER = "solver-model"
TOPICS = {"1.1": "Requisitos", "1.2": "Obtención", "2.1": "Arquitectura"}
BLUEPRINT = {
    "exam": "Demo exam",
    "source": "guide p. 11",
    "areas": [
        {"code": "1", "title": "Análisis", "subareas": [
            {"ref": "1.1", "title": "Requisitos", "items": 6},
            {"ref": "1.2", "title": "Obtención", "items": 2},
        ]},
        {"code": "2", "title": "Diseño", "subareas": [
            {"ref": "2.1", "title": "Arquitectura", "items": 4},
        ]},
    ],
}


def bank_md(per_tag: int, prefix: str) -> tuple[str, str]:
    """``per_tag`` questions per topic; option A (stored index 0) is always the key."""

    lines = ["# Bank", ""]
    keys = ["| # | Subárea | Respuesta | Justificación |", "|---|---|---|---|"]
    n = 0
    for tag, title in TOPICS.items():
        lines += [f"## {tag} {title}", ""]
        for i in range(per_tag):
            n += 1
            lines += [
                f"{n}. {prefix} {tag}-{i}: ¿cuál es la correcta? [{tag}]",
                f"A) Right {prefix}{tag}-{i}",
                f"B) Wrong one {prefix}{tag}-{i}",
                f"C) Wrong two {prefix}{tag}-{i}",
                "",
            ]
            keys.append(f"| {n} | {tag} | A | Porque sí. |")
    return "\n".join(lines), "\n".join(keys)


@pytest.fixture
def bank(store) -> str:
    api.goal_add(store, goal_id=GOAL, title="Exam", deadline="2026-12-04",
                 minutes_per_session=60)
    md, key = bank_md(8, "Q")
    report = api.study_import(store, GOAL, md, key_markdown=key, what=["questions"],
                              source="bank.md", author=AUTHOR)
    assert report["questions"]["imported"] == 24
    return GOAL


@pytest.fixture
def blueprint(store, bank) -> dict:
    return api.goal_blueprint_set(store, GOAL, BLUEPRINT)


@pytest.fixture
def sealed(store, blueprint) -> list[str]:
    md, key = bank_md(4, "M")
    report = api.study_import(store, GOAL, md, key_markdown=key, source="mock.md",
                              author=AUTHOR, pool="mock")
    assert report["pool"] == "mock" and report["questions"]["imported"] == 12
    return [
        r["item_id"]
        for r in store.query("SELECT item_id FROM items WHERE pool = 'mock' ORDER BY created_at")
    ]


def check_all(store, item_ids: list[str]) -> None:
    for item in item_ids:
        api.item_blind_check(store, item, answer="A", by=SOLVER)


def ref_of(store, question: dict) -> str:
    return question["ref"]


def right_key(question: dict) -> str:
    return next(o["key"] for o in question["options"] if o["text"].startswith("Right"))


def wrong_key(question: dict) -> str:
    return next(o["key"] for o in question["options"] if o["text"].startswith("Wrong"))


# --------------------------------------------------------------------------- store
def test_migration_4_adds_the_blueprint_table_and_the_pool(store, bank):
    assert store.schema_version() >= 4
    pools = {r["pool"] for r in store.query("SELECT pool FROM items")}
    assert pools == {"practice"}


# --------------------------------------------------------------------------- blueprint
def test_a_blueprint_resolves_its_refs_and_reports_shares(store, blueprint):
    assert blueprint["total_items"] == 12
    assert [a["code"] for a in blueprint["areas"]] == ["1", "2"]
    first = blueprint["areas"][0]
    assert first["exam_items"] == 8 and first["share"] == pytest.approx(8 / 12)
    assert [s["ref"] for s in first["subareas"]] == ["1.1", "1.2"]
    assert all(s["node_id"] for a in blueprint["areas"] for s in a["subareas"])


def test_a_blueprint_with_an_unknown_ref_writes_nothing(store, blueprint):
    bad = {"areas": [{"code": "9", "title": "Nada", "subareas": [
        {"ref": "9.9", "title": "Nada", "items": 3}]}]}
    with pytest.raises(LearnerError, match="9.9"):
        api.goal_blueprint_set(store, GOAL, bad)
    assert api.goal_blueprint(store, GOAL)["total_items"] == 12


def test_a_goal_without_a_blueprint_says_so(store, bank):
    with pytest.raises(LearnerError, match="no blueprint for goal"):
        api.goal_blueprint(store, GOAL)


def test_the_blueprint_and_tables_follow_a_merged_concept(store, blueprint):
    table = api.table_save(store, GOAL, title="Tipos", columns=["a", "b"],
                           rows=[["1", "2"]], node="1.2", author="claude")
    api.graph_revise(store, GOAL, [{"op": "add", "node": {"title": "Legacy notes"}}])
    before = api.goal_blueprint(store, GOAL)["areas"][0]["subareas"][1]["node_id"]
    # a new title makes a new node: the old one retires and its names move over
    merged = api.graph_revise(
        store, GOAL,
        [{"op": "merge", "nodes": ["1.2", "Legacy notes"],
          "into": {"title": "1.2 Obtención y validación"}}],
    )["ops"][0]["node"]
    assert merged != before
    after = api.goal_blueprint(store, GOAL)
    assert after["areas"][0]["subareas"][1]["node_id"] == merged
    assert api.table_get(store, table["table_id"])["node_id"] == merged
    assert {q["ref"] for q in api.practice_next(store, GOAL, n=20, focus="1.2")["questions"]}         == {"1.2"}


# --------------------------------------------------------------------------- practice
def test_new_questions_follow_the_blueprint_weights_from_the_first_picks(store, blueprint):
    served = api.practice_next(store, GOAL, n=12)["questions"]
    assert all(q["reason"] == "new" for q in served)
    assert Counter(q["ref"] for q in served) == {"1.1": 6, "1.2": 2, "2.1": 4}
    # the second pick is already from the other area: interleaved, not area-blocked
    assert served[1]["area"]["code"] == "2"
    assert served[0]["area"] == {"code": "1", "title": "Análisis"}


def test_without_a_blueprint_concepts_share_equally(store, bank):
    served = api.practice_next(store, GOAL, n=9)["questions"]
    assert Counter(q["node_title"] for q in served) == {
        "1.1 Requisitos": 3, "1.2 Obtención": 3, "2.1 Arquitectura": 3
    }
    assert served[0]["ref"] is None and served[0]["area"] is None


def test_focus_narrows_practice_to_an_area_or_a_concept(store, blueprint):
    area = api.practice_next(store, GOAL, n=20, focus="2")
    assert {q["ref"] for q in area["questions"]} == {"2.1"}
    assert area["counts"]["focus"] == {"kind": "area", "label": "Área 2 · Diseño"}
    concept = api.practice_next(store, GOAL, n=20, focus="1.2")
    assert {q["ref"] for q in concept["questions"]} == {"1.2"}
    assert concept["counts"]["focus"]["kind"] == "node"
    assert api.practice_next(store, GOAL, n=1, focus="mixed")["counts"]["focus"]["kind"] == "mixed"
    with pytest.raises(LearnerError, match="unknown focus"):
        api.practice_next(store, GOAL, focus="7")


def test_options_are_shuffled_and_graded_by_the_order_shown(store, blueprint):
    served = api.practice_next(store, GOAL, n=12)["questions"]
    assert all(sorted(q["order"]) == [0, 1, 2] for q in served)
    assert any(q["order"] != [0, 1, 2] for q in served)
    for q in served:  # the letters are the shown positions of the stored options
        assert [o["key"] for o in q["options"]] == ["A", "B", "C"]
    first, second, third = served[:3]

    right = api.practice_answer(store, item_id=first["item_id"], response=right_key(first),
                                order=first["order"])
    assert right["correct"] is True
    assert right["correct_answer"]["key"] == right_key(first)
    event = store.one("SELECT payload FROM events WHERE event_id = ?", (right["event_id"],))
    assert json.loads(event["payload"])["shown_order"] == first["order"]

    wrong = api.practice_answer(store, item_id=second["item_id"], response=wrong_key(second),
                                order=second["order"])
    assert wrong["correct"] is False
    assert wrong["correct_answer"]["key"] == right_key(second)

    # a client that forgets `order` is graded by the order served today
    forgot = api.practice_answer(store, item_id=third["item_id"], response=right_key(third))
    assert forgot["correct"] is True

    with pytest.raises(LearnerError, match="permutation"):
        api.practice_answer(store, item_id=first["item_id"], response="A", order=[0, 0, 1])


# --------------------------------------------------------------------------- sealed
def test_sealed_questions_stay_out_of_practice_cards_and_exports(store, sealed):
    counts = api.study_overview(store, GOAL)["bank"]
    assert counts["sealed"] == 12 and counts["total"] == 24
    served = api.practice_next(store, GOAL, n=50)["questions"]
    assert not any(q["stem"].startswith("M ") for q in served)
    export = api.cards_export(store, GOAL, include="questions")["text"]
    assert "Right M" not in export and "Right Q" in export
    pending = api.bank_pending(store, GOAL, limit=200)
    assert sum(1 for i in pending["items"] if i["stem"].startswith("M ")) == 12


def test_a_sealed_import_is_questions_only(store, blueprint):
    md, key = bank_md(1, "X")
    with pytest.raises(LearnerError, match="questions only"):
        api.study_import(store, GOAL, md, key_markdown=key, what=["cards"], pool="mock")


def test_a_mock_uses_only_checked_sealed_questions(store, sealed):
    with pytest.raises(LearnerError, match="blind check"):
        api.mock_start(store, GOAL, n=6)
    assert api.mock_list(store, GOAL)["sealed_unchecked"] == 12


def test_a_mock_round_trip(store, sealed):
    check_all(store, sealed)
    opened = api.mock_start(store, GOAL, n=6)
    assert opened["status"] == "open" and opened["n"] == 6
    assert opened["minutes"] == round(6 * store.settings.mock_minutes_per_item)
    for q in opened["questions"]:  # nothing that gives the key away while it is open
        assert set(q) == {"item_id", "ref", "area", "node_title", "stem", "options", "order"}
    assert "Porque" not in json.dumps(opened, ensure_ascii=False)
    assert Counter(q["ref"] for q in opened["questions"]) == {"1.1": 3, "1.2": 1, "2.1": 2}

    with pytest.raises(StateConflict, match="still open"):
        api.mock_start(store, GOAL, n=2)
    listing = api.mock_list(store, GOAL)
    assert listing["open"]["session_id"] == opened["session_id"]
    assert listing["sealed_available"] == 6  # the other six are reserved by the open mock
    assert api.mock_show(store, opened["session_id"])["status"] == "open"

    qs = opened["questions"]
    answers = [{"item_id": q["item_id"], "response": right_key(q)} for q in qs[:4]]
    answers.append({"item_id": qs[4]["item_id"], "response": wrong_key(qs[4]), "confidence": 5})
    # the sixth is left blank
    result = api.mock_submit(store, opened["session_id"], answers)
    assert result["status"] == "submitted"
    assert (result["n"], result["answered"], result["correct"]) == (6, 5, 4)
    assert sum(a["n"] for a in result["areas"]) == 6
    assert sum(a["correct"] for a in result["areas"]) == 4
    blank = next(i for i in result["items"] if i["item_id"] == qs[5]["item_id"])
    assert blank["your_answer"] is None and blank["correct"] is False
    assert blank["correct_answer"]["key"] == right_key(qs[5])
    assert blank["explanation"] == "Porque sí."

    events = store.query(
        "SELECT idk, evaluation_method, grader_version, prompt_version FROM events "
        "WHERE session_id = ? AND kind = 'answer'",
        (opened["session_id"],),
    )
    assert len(events) == 6 and sum(e["idk"] for e in events) == 1
    assert {(e["evaluation_method"], e["grader_version"], e["prompt_version"]) for e in events} \
        == {("rubric", "bank-key-v1", exam.MOCK_PROMPT_VERSION)}

    with pytest.raises(StateConflict, match="already submitted"):
        api.mock_submit(store, opened["session_id"], [])
    listing = api.mock_list(store, GOAL)
    assert listing["open"] is None and len(listing["mocks"]) == 1
    assert listing["mocks"][0]["correct"] == 4 and listing["mocks"][0]["areas"]
    # used questions leave the sealed pool and join practice rotation (scheduled, not new)
    counts = api.study_overview(store, GOAL)["bank"]
    assert counts["sealed"] == 6 and counts["total"] == 30
    assert not any(
        q["reason"] == "new" and q["stem"].startswith("M ")
        for q in api.practice_next(store, GOAL, n=50)["questions"]
    )


def test_a_mock_rejects_foreign_items_and_bad_answers_without_writing(store, sealed):
    check_all(store, sealed)
    opened = api.mock_start(store, GOAL, n=3)
    with pytest.raises(LearnerError, match="not a question of mock"):
        api.mock_submit(store, opened["session_id"], [{"item_id": "nope", "response": "A"}])
    first = opened["questions"][0]["item_id"]
    with pytest.raises(LearnerError, match="not one of its options"):
        api.mock_submit(store, opened["session_id"], [{"item_id": first, "response": "Z"}])
    # a bad confidence on the LAST question must not leave the first ones written
    last = opened["questions"][-1]
    good = [{"item_id": q["item_id"], "response": right_key(q)} for q in opened["questions"]]
    good[-1] = {"item_id": last["item_id"], "response": right_key(last), "confidence": 9}
    with pytest.raises(LearnerError, match="confidence"):
        api.mock_submit(store, opened["session_id"], good)
    assert not store.query(
        "SELECT 1 FROM events WHERE session_id = ? AND kind = 'answer'", (opened["session_id"],)
    )
    assert api.mock_show(store, opened["session_id"])["status"] == "open"


# --------------------------------------------------------------------------- progress
def test_wilson_range():
    assert exam.wilson(0, 0) == (None, None)
    assert exam.wilson(7, 10) == (40, 89)
    assert exam.wilson(10, 10)[1] == 100


def test_progress_counts_first_tries_by_area(store, sealed):
    focus = api.practice_next(store, GOAL, n=3, focus="1.1")["questions"]
    for i, q in enumerate(focus):
        key = right_key(q) if i < 2 else wrong_key(q)
        api.practice_answer(store, item_id=q["item_id"], response=key, order=q["order"])
    again = focus[2]
    api.practice_answer(store, item_id=again["item_id"], response=right_key(again),
                        order=again["order"])

    data = api.progress(store, GOAL)
    assert data["has_blueprint"] and data["deadline"] == "2026-12-04"
    area1, area2 = data["areas"]
    assert (area1["first_attempts"], area1["first_correct"]) == (3, 2)
    assert (area1["attempts"], area1["correct"]) == (4, 3)
    assert (area1["low"], area1["high"]) == exam.wilson(2, 3)
    assert area1["sealed"] == 8 and area2["sealed"] == 4
    sub = area1["subareas"][0]
    assert sub["ref"] == "1.1" and sub["seen"] == 3 and sub["exam_items"] == 6
    assert area2["first_attempts"] == 0 and area2["low"] is None
    assert data["disciplinar"]["weighted_accuracy"] is None
    assert "área 2 has 0" in data["disciplinar"]["note"]
    assert data["disciplinar"]["coverage"] == pytest.approx(6 / 12)
    today = data["activity"][-1]
    assert len(data["activity"]) == exam.ACTIVITY_DAYS
    assert today["date"] == data["today"] and today["answers"] == 4 and today["correct"] == 3
    assert data["forecast"][0]["date"] == data["today"]
    assert sum(d["due"] for d in data["forecast"]) >= 1  # the three answered are scheduled

    # a merge copies evidence events onto the new node; progress must not count them twice
    api.graph_revise(store, GOAL, [{"op": "add", "node": {"title": "Scratch"}}])
    api.graph_revise(store, GOAL, [{"op": "merge", "nodes": ["1.1", "Scratch"],
                                    "into": {"title": "1.1 Requisitos (fusionado)"}}])
    assert store.one(
        "SELECT COUNT(*) AS n FROM events WHERE json_extract(payload, '$.migrated') = 1"
    )["n"] >= 4
    after = api.progress(store, GOAL)
    assert (after["areas"][0]["first_attempts"], after["areas"][0]["attempts"]) == (3, 4)
    assert after["activity"][-1]["answers"] == 4


def test_the_headline_appears_once_every_area_has_enough_first_tries(store, blueprint):
    served = api.practice_next(store, GOAL, n=20)["questions"]
    for q in served:
        api.practice_answer(store, item_id=q["item_id"], response=right_key(q), order=q["order"])
    data = api.progress(store, GOAL)
    assert all(a["first_attempts"] >= exam.HEADLINE_MIN_FIRST_TRIES for a in data["areas"])
    assert data["disciplinar"]["weighted_accuracy"] == 100


# --------------------------------------------------------------------------- over HTTP
@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def test_the_exam_routes_over_http(client, store, sealed):
    assert client.get("/v1/goals/nope/blueprint").status_code == 404
    assert client.get(f"/v1/goals/{GOAL}/blueprint").json()["total_items"] == 12
    put = client.put(f"/v1/goals/{GOAL}/blueprint", json=BLUEPRINT)
    assert put.status_code == 200 and put.json()["total_items"] == 12

    served = client.get(f"/v1/practice/{GOAL}/next", params={"n": 2, "focus": "2"}).json()
    assert {q["ref"] for q in served["questions"]} == {"2.1"}
    q = served["questions"][0]
    graded = client.post("/v1/practice/answer",
                         json={"item_id": q["item_id"], "response": right_key(q),
                               "order": q["order"]})
    assert graded.status_code == 200 and graded.json()["correct"] is True
    assert client.get(f"/v1/progress/{GOAL}").json()["totals"]["first_attempts"] == 1

    check_all(store, sealed)
    started = client.post(f"/v1/mocks/{GOAL}", json={"n": 2})
    assert started.status_code == 200, started.text
    sid = started.json()["session_id"]
    assert client.post(f"/v1/mocks/{GOAL}", json={"n": 2}).status_code == 409
    assert client.get(f"/v1/mock/{sid}").json()["status"] == "open"
    submitted = client.post(f"/v1/mock/{sid}/submit", json={"answers": []})
    assert submitted.status_code == 200 and submitted.json()["answered"] == 0
    assert client.post(f"/v1/mock/{sid}/submit", json={"answers": []}).status_code == 409
    assert client.get("/v1/mock/s_nope").status_code == 404
    assert client.get(f"/v1/mocks/{GOAL}").json()["mocks"][0]["session_id"] == sid
