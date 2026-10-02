"""Study tools: the markdown readers, the question bank, flashcards, tables, goal update.

CONTRACTS.md, *Study tools*. The rules under test are the ones the feature exists to keep:
keys never leave before an attempt, a flashcard flip is never evidence, an imported
question counts only after a blind check by someone other than its author, and importing
the same file twice adds nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from learning_tutor.learner import api, study_md
from learning_tutor.learner import items as items_mod
from learning_tutor.learner.store import LearnerError
from learning_tutor.learner_svc import create_app

GOAL = "egel"

MD = """# Área 1. Demo

## 1.1 Tipos de requerimientos (2 reactivos)

### Conceptos clave

**Requerimiento funcional.** Describe qué hace el sistema.

**Requerimiento no funcional.** Describe cómo debe ser:

- rendimiento
- seguridad

### Comparaciones

| Criterio | Funcional | No funcional |
|---|---|---|
| Qué describe | Qué hace | Cómo debe ser |
| Verificación | Caso de prueba | Medición |

## 1.2 Técnicas de obtención (1 reactivo)

### Conceptos clave

**Entrevista.** Conversación estructurada con stakeholders.

## 25 reactivos de práctica

1. Un sistema debe responder en 3 s. ¿Qué tipo de requerimiento es? [1.1]
   A) Funcional
   B) No funcional
   C) De dominio

**2.** Ordene las fases de la obtención: [1.2]
1. Validar
2. Obtener
3. Analizar
A) 2, 3, 1
B) 1, 2, 3
C) 3, 2, 1
"""

KEY = """| # | Subárea | Respuesta | Justificación |
|---|---|---|---|
| 1 | 1.1 | B | Tiempo de respuesta es desempeño, no una función. |
| 2 | 1.2 | A | Primero se obtiene, luego se analiza y al final se valida. |
"""

AUTHOR = "author-model"
#: the stored option order, as a client that shows the options unshuffled sends it back
STORED = [0, 1, 2]


@pytest.fixture
def egel(store) -> str:
    api.goal_add(store, goal_id=GOAL, title="EGEL", deadline="2026-11-20", minutes_per_session=30)
    return GOAL


@pytest.fixture
def imported(store, egel) -> dict:
    return api.study_import(store, egel, MD, key_markdown=KEY, source="demo.md", author=AUTHOR)


def question_ids(store) -> dict[int, str]:
    rows = store.query(
        "SELECT i.item_id, v.source FROM items i JOIN item_versions v "
        "ON v.item_version_id = i.current_version_id WHERE v.kind = 'mc'"
    )
    return {int(r["source"].rsplit("#", 1)[1]): r["item_id"] for r in rows}


# --------------------------------------------------------------------------- readers
def test_questions_parse_plain_and_bold_numbers_and_keep_ordering_steps_in_the_stem():
    questions, problems = study_md.parse_questions(MD)
    assert problems == []
    assert [q.number for q in questions] == [1, 2]
    first, second = questions
    assert first.tag == "1.1" and "[1.1]" not in first.stem
    assert [letter for letter, _ in first.options] == ["A", "B", "C"]
    # the ordering steps sit at column 0 and still belong to the stem, not to new questions
    assert second.tag == "1.2"
    assert "1. Validar" in second.stem and "3. Analizar" in second.stem
    assert second.options[0] == ("A", "2, 3, 1")


def test_a_rule_or_blank_line_after_the_options_is_not_part_of_the_last_option():
    text = "\n".join(
        [
            "## Preguntas",
            "",
            "1. Pick one [1.1]",
            "   A) First",
            "   B) Second option",
            "      wrapped onto a second line",
            "   C) Third",
            "",
            "---",
            "",
            "## Bibliografía",
        ]
    )
    (question,), problems = study_md.parse_questions(text)
    assert problems == []
    assert question.options == [
        ("A", "First"), ("B", "Second option wrapped onto a second line"), ("C", "Third")
    ]


def test_a_combined_key_is_matched_on_number_and_tag():
    combined = KEY + "| 1 | 2.1 | C | another area's question 1 |\n"
    questions, _ = study_md.parse_questions(MD)
    rows = study_md.parse_key(combined)
    assert len(rows) == 3
    assert study_md.match_key(questions[0], rows).letter == "B"


def test_cards_come_from_bold_terms_with_their_bullets():
    cards = study_md.parse_cards(MD)
    fronts = [c.front for c in cards]
    assert fronts == ["Requerimiento funcional", "Requerimiento no funcional", "Entrevista"]
    assert "- seguridad" in cards[1].back
    assert [c.tag for c in cards] == ["1.1", "1.1", "1.2"]


def test_tables_are_titled_by_their_section_and_answer_keys_are_skipped():
    tables = study_md.parse_tables(MD + "\n" + KEY)
    assert len(tables) == 1
    table = tables[0]
    assert table.title == "1.1 Tipos de requerimientos: Funcional vs No funcional"
    assert table.columns == ["Criterio", "Funcional", "No funcional"]
    assert table.rows[1] == ["Verificación", "Caso de prueba", "Medición"]


def test_node_titles_come_from_tagged_headings():
    assert study_md.parse_node_titles(MD) == {
        "1.1": "1.1 Tipos de requerimientos",
        "1.2": "1.2 Técnicas de obtención",
    }


# --------------------------------------------------------------------------- goal update
def test_goal_update_changes_the_row_and_leaves_a_note_event(store, egel):
    result = api.goal_update(store, egel, deadline="2026-12-04", sessions_per_week=5)
    assert result["changed"] == {
        "deadline": {"from": "2026-11-20", "to": "2026-12-04"},
        "sessions_per_week": {"from": None, "to": 5},
    }
    assert api.goal_get(store, egel)["sessions_per_week"] == 5
    notes = api.events(store, goal_id=egel, kind="note")["events"]
    assert notes[0]["payload"]["goal_update"]["deadline"]["to"] == "2026-12-04"
    # no change → no event
    assert api.goal_update(store, egel, deadline="2026-12-04")["changed"] == {}
    assert len(api.events(store, goal_id=egel, kind="note")["events"]) == 1
    # "" clears the deadline
    assert api.goal_update(store, egel, deadline="")["goal"]["deadline"] is None


def test_goal_update_rejects_bad_values(store, egel):
    with pytest.raises(LearnerError, match="YYYY-MM-DD"):
        api.goal_update(store, egel, deadline="04/12/2026")
    with pytest.raises(LearnerError, match="sessions per week"):
        api.goal_update(store, egel, sessions_per_week=0)
    with pytest.raises(LearnerError, match="cannot update"):
        api.goal_update(store, egel, phase="teach")


# --------------------------------------------------------------------------- import
def test_import_files_questions_under_new_course_nodes(store, imported):
    assert imported["questions"] == {
        "parsed": 2, "imported": 2, "skipped_existing": 0, "problems": []
    }
    assert imported["cards"] == {"parsed": 3, "imported": 3, "skipped_existing": 0}
    assert imported["tables"] == {"parsed": 1, "imported": 1, "skipped_existing": 0}
    titles = [n["title"] for n in imported["nodes_created"]]
    assert titles == ["1.1 Tipos de requerimientos", "1.2 Técnicas de obtención"]
    graph = api.graph_show(store, GOAL)
    edge = graph["edges"][0]
    assert (edge["type"], edge["provenance"]) == ("course_sequence", "course")
    # every imported question starts TEACHING_ONLY, authored by who the caller said
    for item_id in question_ids(store).values():
        record = items_mod.get(store, item_id)
        assert (record.status, record.author) == ("TEACHING_ONLY", AUTHOR)


def test_importing_the_same_file_twice_adds_nothing(store, imported):
    again = api.study_import(store, GOAL, MD, key_markdown=KEY, source="demo.md", author=AUTHOR)
    assert again["questions"]["imported"] == 0 and again["questions"]["skipped_existing"] == 2
    assert again["cards"]["imported"] == 0 and again["tables"]["imported"] == 0
    assert again["nodes_created"] == []


def test_a_dry_run_writes_nothing(store, egel):
    report = api.study_import(store, egel, MD, key_markdown=KEY, dry_run=True, author=AUTHOR)
    assert report["questions"]["imported"] == 2 and report["dry_run"] is True
    assert store.one("SELECT COUNT(*) AS n FROM items")["n"] == 0
    assert store.one("SELECT COUNT(*) AS n FROM study_tables")["n"] == 0
    assert api.graph_show(store, egel)["nodes"] == []


def test_a_question_without_a_key_row_is_reported_not_guessed(store, egel):
    report = api.study_import(
        store, egel, MD, key_markdown=KEY.splitlines()[0], what=["questions"], author=AUTHOR
    )
    assert report["questions"]["imported"] == 0
    assert "question 1: no answer-key row" in report["questions"]["problems"]


# --------------------------------------------------------------------------- practice
def test_practice_serves_no_key_and_grades_server_side(store, imported):
    served = api.practice_next(store, GOAL, n=5)
    assert len(served["questions"]) == 2 and served["counts"]["new_available"] == 2
    first = served["questions"][0]
    assert set(first) >= {"item_id", "stem", "options", "checked", "reason", "context"}
    assert "answer" not in first and "explanation" not in first
    assert first["checked"] is False and first["reason"] == "new"

    # letters follow the order the options were shown in; `order` says what that was
    key_of = {o["text"]: o["key"] for o in first["options"]}
    assert sorted(first["order"]) == [0, 1, 2]
    wrong_key = next(o["key"] for o in first["options"] if o["text"] == "Funcional")
    wrong = api.practice_answer(
        store, item_id=first["item_id"], response=wrong_key, order=first["order"]
    )
    assert wrong["correct"] is False
    assert wrong["your_answer"] == {"key": wrong_key, "text": "Funcional"}
    assert wrong["correct_answer"] == {"key": key_of["No funcional"], "text": "No funcional"}
    assert wrong["explanation"].startswith("Tiempo de respuesta")
    assert wrong["counts_toward_mastery"] is False
    assert "not yet verified" in wrong["note"]
    # scheduled all the same, so an unchecked question comes back
    assert wrong["schedule"]["rating"] == "Again"
    event = api.events(store, kind="answer")["events"][0]
    assert (event["evaluation_method"], event["grader_version"]) == ("rubric", "bank-key-v1")


def test_a_bad_response_is_rejected(store, imported):
    item = question_ids(store)[1]
    with pytest.raises(LearnerError, match="not one of the options"):
        api.practice_answer(store, item_id=item, response="Z")


def test_after_a_passing_blind_check_a_correct_answer_counts(store, imported):
    item = question_ids(store)[1]
    check = api.item_blind_check(store, item, answer="B", by="solver-model")
    assert check == {
        "item_id": item, "result": "pass", "status": "PRACTICE_EVIDENCE",
        "key_matched": True, "ambiguous": False,
    }
    right = api.practice_answer(store, item_id=item, response="B", order=STORED, confidence=4)
    assert right["correct"] is True and right["checked"] is True
    assert right["counts_toward_mastery"] is True
    assert right["node_state"]["independent_passes"] == 1


def test_the_author_cannot_blind_check_their_own_question(store, imported):
    item = question_ids(store)[1]
    with pytest.raises(LearnerError, match="must differ from the author"):
        api.item_blind_check(store, item, answer="B", by=AUTHOR)


def test_a_failed_blind_check_holds_the_question_back_for_review(store, imported):
    ids = question_ids(store)
    pending = api.bank_pending(store, GOAL)
    assert pending["pending"] == 2
    assert all("answer" not in row for row in pending["items"])
    result = api.item_blind_check(store, ids[2], answer="B", by="solver-model", notes="hmm")
    assert (result["result"], result["key_matched"]) == ("fail", False)
    assert api.item_blind_check(store, ids[1], answer="B", by="solver-model", ambiguous=True)[
        "result"
    ] == "fail"
    review = api.bank_review(store, GOAL)["items"]
    assert {r["item_id"] for r in review} == {ids[1], ids[2]}
    ordering = next(r for r in review if r["item_id"] == ids[2])
    assert ordering["answer"]["key"] == "A" and ordering["solver_answer"]["key"] == "B"
    assert ordering["notes"] == "hmm"
    assert api.practice_next(store, GOAL, n=5)["questions"] == []
    assert api.bank_pending(store, GOAL)["pending"] == 0
    assert api.study_overview(store, GOAL)["bank"]["rejected"] == 2
    # a possibly wrong key does not leave for Anki either
    assert api.cards_export(store, GOAL, include="questions")["count"] == 0


def test_a_holdout_is_never_served_for_practice(store, imported):
    item = question_ids(store)[1]
    store.update("items", {"item_id": item}, {"holdout": 1})
    served = api.practice_next(store, GOAL, n=5)["questions"]
    assert [q["item_id"] for q in served] == [question_ids(store)[2]]
    with pytest.raises(LearnerError, match="holdout"):
        api.practice_answer(store, item_id=item, response="B")


def test_the_daily_new_cap_holds(store, imported, monkeypatch):
    monkeypatch.setenv("LT_PRACTICE_NEW_PER_DAY", "1")
    from learning_tutor.config import get_settings
    from learning_tutor.learner.store import open_store

    with open_store(get_settings()) as capped:
        first = api.practice_next(capped, GOAL, n=5)["questions"]
        assert len(first) == 1
        api.practice_answer(capped, item_id=first[0]["item_id"], response="B", order=STORED)
        after = api.practice_next(capped, GOAL, n=5)
        assert after["counts"]["new_today"] == 1
        assert all(q["reason"] != "new" for q in after["questions"])


def test_an_answer_after_a_gap_is_a_delayed_retrieval(store, imported):
    item = question_ids(store)[1]
    api.item_blind_check(store, item, answer="B", by="solver-model")
    earlier = (datetime.now(UTC) - timedelta(days=2)).isoformat(timespec="seconds")
    api.record_answer(
        store, session_id=None, item_id=item, response="No funcional", correct=True,
        evaluation_method="rubric", ts=earlier.replace("+00:00", "Z"),
    )
    later = api.practice_answer(store, item_id=item, response="B", order=STORED)
    assert later["context"] == "delayed"
    assert later["node_state"]["last_delayed"] == "pass"


# --------------------------------------------------------------------------- cards
def test_cards_flip_in_three_calls_and_never_count(store, imported):
    served = api.cards_next(store, GOAL, n=1)
    card = served["cards"][0]
    assert set(card) == {"item_id", "node_id", "node_title", "front", "reason"}
    back = api.card_reveal(store, card["item_id"])
    assert back["back"] == "Describe qué hace el sistema."
    before = api.graph_show(store, GOAL)
    result = api.card_review(store, card["item_id"], rating="good")
    assert result["counts_toward_mastery"] is False
    event = api.events(store, kind="card_review")["events"][0]
    assert (event["evaluation_method"], event["response"]) == ("self_report", "good")
    # scheduled into the future, and not a single node moved
    assert datetime.fromisoformat(result["schedule"]["due"]) > datetime.now(UTC)
    after = api.graph_show(store, GOAL)
    assert [n["state"] for n in after["nodes"]] == [n["state"] for n in before["nodes"]]
    assert api.cards_next(store, GOAL, n=5)["counts"]["new_today"] == 1


def test_a_card_is_not_a_question_and_cannot_be_validated(store, imported):
    card = api.cards_next(store, GOAL)["cards"][0]["item_id"]
    with pytest.raises(LearnerError, match="flashcard"):
        api.practice_answer(store, item_id=card, response="A")
    with pytest.raises(LearnerError, match="flashcard"):
        api.item_validate(store, card, by="solver-model", result="pass")
    with pytest.raises(LearnerError, match="rating must be"):
        api.card_review(store, card, rating="perfect")


def test_harness_cards_need_a_node_and_dedupe(store, imported):
    cards = [{"front": "SRS", "back": "Software requirements specification", "node": "1.1"}]
    assert api.cards_add(store, GOAL, cards, author="claude")["imported"] == 1
    assert api.cards_add(store, GOAL, cards, author="claude")["skipped_existing"] == 1
    with pytest.raises(LearnerError, match="needs a node"):
        api.cards_add(store, GOAL, [{"front": "x", "back": "y"}], author="claude")


def test_export_is_anki_text(store, imported):
    tsv = api.cards_export(store, GOAL)
    assert tsv["count"] == 3
    lines = tsv["text"].splitlines()
    front, back, tag = lines[1].split("\t")
    assert front == "Requerimiento no funcional" and "<br>- rendimiento" in back
    assert tag == "1.1_Tipos_de_requerimientos"
    questions = api.cards_export(store, GOAL, fmt="csv", include="questions")
    assert questions["text"].startswith("front,back,tags\n") and questions["count"] == 2
    assert "B) No funcional" in questions["text"]


# --------------------------------------------------------------------------- tables
def test_tables_list_get_and_become_cards(store, imported):
    listed = api.tables_list(store, GOAL)["tables"]
    assert len(listed) == 1 and listed[0]["node_title"] == "1.1 Tipos de requerimientos"
    assert "rows" not in listed[0]
    table = api.table_get(store, listed[0]["table_id"])
    assert table["rows"][0] == ["Qué describe", "Qué hace", "Cómo debe ser"]
    made = api.table_cards(store, table["table_id"])
    assert made == {"parsed": 4, "imported": 4, "skipped_existing": 0}
    fronts = {c["front"] for c in api.cards_next(store, GOAL, n=20)["cards"]}
    assert "Funcional — Qué describe" in fronts


def test_saved_tables_are_validated_and_deduped(store, imported):
    saved = api.table_save(
        store, GOAL, title="Glosario", columns=["Término", "Definición"],
        rows=[["RNF", "Requerimiento no funcional"]], node="1.1", author="claude",
    )
    assert saved["new"] is True and saved["row_count"] == 1
    again = api.table_save(
        store, GOAL, title="Glosario", columns=["Término", "Definición"],
        rows=[["RNF", "Requerimiento no funcional"]], node="1.1", author="claude",
    )
    assert again["new"] is False and again["table_id"] == saved["table_id"]
    assert api.table_cards(store, saved["table_id"])["imported"] == 1
    with pytest.raises(LearnerError, match="cells"):
        api.table_save(store, GOAL, title="x", columns=["a", "b"], rows=[["only one"]],
                       author="claude")
    loose = api.table_save(store, GOAL, title="No concept", columns=["a", "b"],
                           rows=[["1", "2"]], author="claude")
    with pytest.raises(LearnerError, match="not filed under a concept"):
        api.table_cards(store, loose["table_id"])


# --------------------------------------------------------------------------- learner-svc
@pytest.fixture
def client(settings) -> TestClient:
    return TestClient(create_app(settings))


def test_the_study_routes_over_http(client):
    client.post("/v1/goals", json={"goal_id": GOAL, "title": "EGEL", "deadline": "2026-11-20"})
    patched = client.patch(f"/v1/goals/{GOAL}", json={"deadline": "2026-12-04"})
    assert patched.status_code == 200 and patched.json()["goal"]["deadline"] == "2026-12-04"

    report = client.post(
        f"/v1/study/{GOAL}/import", json={"markdown": MD, "key_markdown": KEY, "author": AUTHOR}
    ).json()
    assert report["questions"]["imported"] == 2

    served = client.get(f"/v1/practice/{GOAL}/next", params={"n": 1}).json()
    item = served["questions"][0]["item_id"]
    answer = client.post(
        "/v1/practice/answer", json={"item_id": item, "response": "B", "order": STORED}
    )
    assert answer.status_code == 200 and answer.json()["correct"] is True

    pending = client.get(f"/v1/bank/{GOAL}/pending").json()
    assert pending["pending"] == 2
    check = client.post(f"/v1/items/{item}/blind-check", json={"answer": "B", "by": "solver"})
    assert check.json()["status"] == "PRACTICE_EVIDENCE"

    card = client.get(f"/v1/cards/{GOAL}/next").json()["cards"][0]["item_id"]
    assert client.post(f"/v1/cards/{card}/reveal").json()["back"]
    reviewed = client.post(f"/v1/cards/{card}/review", json={"rating": "hard"})
    assert reviewed.json()["counts_toward_mastery"] is False

    export = client.get(f"/v1/cards/{GOAL}/export", params={"format": "tsv"})
    assert export.headers["content-type"].startswith("text/tab-separated-values")
    assert export.headers["x-card-count"] == "3"
    assert "attachment" in export.headers["content-disposition"]

    tables = client.get(f"/v1/tables/{GOAL}").json()["tables"]
    table_id = tables[0]["table_id"]
    assert client.get(f"/v1/tables/{GOAL}/{table_id}").json()["rows"]
    assert client.get(f"/v1/tables/{GOAL}/t_nope").status_code == 404
    assert client.post(f"/v1/tables/{GOAL}/{table_id}/cards").json()["imported"] == 4
    overview = client.get(f"/v1/study/{GOAL}").json()
    assert overview["bank"]["checked"] == 1 and overview["cards"]["total"] == 7


def test_the_views_count_the_goals_own_cadence(store, egel):
    deadline = datetime.now(UTC).date() + timedelta(days=60)
    api.goal_update(store, egel, deadline=deadline.isoformat(), sessions_per_week=7)
    summary = api.summary(store, egel, "json")
    days = (deadline - datetime.now(UTC).date()).days
    assert summary["feasibility"]["sessions_left"] == max(0, int(days * 7 / 7))
    assert json.dumps(summary)  # still serialisable
