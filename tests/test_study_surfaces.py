"""The study tools' agent-host surface: the MCP tools (both backends) and the CLI.

CONTRACTS.md, *Study tools* and *Exam blueprint, mixed practice and sealed mock exams*. The
rules themselves live in ``learner/study.py``, ``blueprint.py`` and ``exam.py``; these tests
pin the wrappers: every tool answers, the in-process and the HTTP-proxy backend return the
same JSON, and the CLI verbs do what CONTRACTS.md lists — with no key reaching a caller
before an attempt, and none at all from an open mock.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError
from test_mcp_tools import EXAM_TOOLS, STUDY_TOOLS

from learning_tutor import cli, mcp_server
from learning_tutor.config import get_settings
from learning_tutor.learner import exam as exam_mod
from learning_tutor.learner_svc import create_app

BODY = """\
# Covector notes

## 1.1 Covectors

### Key concepts

**Covector.** A linear map from vectors to numbers.

**Dual basis.** The covectors that each pick out one coordinate.

### Comparison

| Criterion | Vector | Covector |
|---|---|---|
| Eats | nothing | a vector |
| Transforms | contravariantly | covariantly |

## Practice questions

1. What eats a vector and returns a number? [1.1]
A) A scalar
B) A linear functional
C) A matrix

2. Which transforms covariantly? [1.1]
A) Covector components
B) Vector components
C) Neither
"""

KEY = """\
| # | Tag | Answer | Why |
|---|---|---|---|
| 1 | 1.1 | B | Covectors are linear functionals on vectors. |
| 2 | 1.1 | A | Covector components transform with the basis. |
"""

#: one file with its own key table — the shape the CLI test imports
NOTES = BODY + "\n## Answer key\n\n" + KEY

Q1 = "What eats a vector and returns a number?"
Q2 = "Which transforms covariantly?"
#: the keyed option of each question, and the key's explanations: none may leak early
CORRECT = {Q1: "A linear functional", Q2: "Covector components"}
WHY = ("Covectors are linear functionals on vectors.", "Covector components transform with the basis.")


def key_of(question: dict[str, Any], text: str) -> str:
    """The letter `text` was shown under. Options are shuffled per question and per day, so
    a test answers by the served letters (and sends the served `order` back), never by a
    letter from the source file."""

    return next(o["key"] for o in question["options"] if o["text"] == text)


def assert_no_key(rows: list[dict[str, Any]]) -> None:
    """Nothing but `options` may carry the keyed option's text, and nothing the key's why."""

    assert rows
    for row in rows:
        assert {"answer", "explanation", "correct_answer"}.isdisjoint(row), row
        for field, value in row.items():
            if field != "options":
                assert CORRECT[row["stem"]] not in json.dumps(value, ensure_ascii=False), field
        text = json.dumps(row, ensure_ascii=False)
        assert not any(why in text for why in WHY), row


# ------------------------------------------------------------------------------ MCP
def call(name: str, **arguments: Any) -> Any:
    """One tool call through FastMCP's in-memory client; its structured output."""

    async def _go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(_go()).structured_content


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, settings):
    monkeypatch.delenv("LT_LEARNER_URL", raising=False)
    monkeypatch.setenv("LT_MCP_ENABLED", "1")
    mcp_server.set_backend(mcp_server.LocalBackend())
    yield
    mcp_server.set_backend(None)


@pytest.fixture
def proxy(settings):
    """The HTTP backend against a live learner-svc app on the same data directory."""

    client = TestClient(create_app(settings))
    mcp_server.set_backend(mcp_server.HttpBackend("http://testserver", client=client))
    yield client
    mcp_server.set_backend(mcp_server.LocalBackend())


def use_backend(kind: str, data_dir: Path) -> None:
    settings = get_settings(data_dir)
    settings.ensure_dirs()
    if kind == "local":
        mcp_server.set_backend(mcp_server.LocalBackend(str(data_dir)))
    else:
        client = TestClient(create_app(settings))
        mcp_server.set_backend(mcp_server.HttpBackend("http://testserver", client=client))


def write_sources(tmp_path: Path) -> tuple[Path, Path]:
    body, key = tmp_path / "notes.md", tmp_path / "key.md"
    body.write_text(BODY, encoding="utf-8")
    key.write_text(KEY, encoding="utf-8")
    return body, key


def scenario(body: Path, key: Path) -> list[tuple[str, Any]]:
    """Every study tool, in order, with `goal` omitted wherever the tool allows it."""

    steps: list[tuple[str, Any]] = []

    def step(name: str, **arguments: Any) -> Any:
        result = call(name, **arguments)
        steps.append((name, result))
        return result

    call("learner_goal_add", goal_id="g_cov", title="Covectors")
    step("learner_goal_update", deadline="2026-12-01", minutes_per_session=30)
    step("learner_study_import", markdown=NOTES, what=["questions"], dry_run=True)
    step("learner_study_import", path=str(body), key_path=str(key))
    pending = step("learner_bank_pending")
    ids = {row["stem"]: row["item_id"] for row in pending["items"]}
    step("learner_item_blind_check", item=ids[Q1], answer="B", by="solver-model")
    step("learner_item_blind_check", item=ids[Q2], answer="Neither", by="solver-model")
    step("learner_bank_review")
    served = step("learner_practice_next", n=5)["questions"][0]
    step(
        "learner_practice_answer",
        item=ids[Q1],
        response=key_of(served, CORRECT[Q1]),
        order=served["order"],
        confidence=4,
    )
    step(
        "learner_cards_add",
        cards=[
            {
                "front": "Annihilator",
                "back": "The covectors that vanish on a subspace.",
                "node": "1.1 Covectors",
            }
        ],
    )
    cards = step("learner_cards_next", n=10)
    card = cards["cards"][0]["item_id"]
    step("learner_card_reveal", item=card)
    step("learner_card_review", item=card, rating="good")
    table = step("learner_tables_list")["tables"][0]["table_id"]
    step("learner_table_get", table=table)
    step(
        "learner_table_save",
        title="Bases",
        columns=["Term", "Meaning"],
        rows=[["basis", "a spanning independent set"]],
        node="1.1 Covectors",
    )
    step("learner_table_cards", table=table)
    step("learner_cards_export", include="all")
    return steps


_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?")
_ID = re.compile(r"\b(?:[a-z]+_[0-9a-z]{26}|[0-9A-Z]{26})\b")


def normalize(value: Any, seen: dict[str, str]) -> Any:
    """Random ids → <idN> in first-seen order, timestamps → <ts>: what two runs share."""

    if isinstance(value, dict):
        return {k: normalize(v, seen) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize(v, seen) for v in value]
    if isinstance(value, str):
        value = _TS.sub("<ts>", value)
        return _ID.sub(lambda m: seen.setdefault(m.group(0), f"<id{len(seen)}>"), value)
    return value


def test_every_study_tool_returns_the_same_json_in_both_backends(tmp_path):
    body, key = write_sources(tmp_path)
    runs = {}
    for kind in ("local", "http"):
        use_backend(kind, tmp_path / kind)
        runs[kind] = scenario(body, key)

    assert {name for name, _ in runs["local"]} == STUDY_TOOLS, "every study tool is exercised"
    assert normalize(runs["local"], {}) == normalize(runs["http"], {})

    results: dict[str, list[Any]] = {}
    for name, result in runs["local"]:
        results.setdefault(name, []).append(result)
    assert results["learner_goal_update"][0]["changed"]["deadline"] == {
        "from": None,
        "to": "2026-12-01",
    }
    dry, real = results["learner_study_import"]
    assert dry["dry_run"] is True and dry["cards"] is None and dry["questions"]["imported"] == 2
    assert real["source"] == "notes.md", "the source label defaults to the file name"
    assert real["questions"] == {"parsed": 2, "imported": 2, "skipped_existing": 0, "problems": []}
    assert real["cards"]["imported"] == 2 and real["tables"]["imported"] == 1
    assert_no_key(results["learner_bank_pending"][0]["items"])
    passed, failed = results["learner_item_blind_check"]
    assert (passed["result"], passed["status"]) == ("pass", "PRACTICE_EVIDENCE")
    assert (failed["result"], failed["status"]) == ("fail", "TEACHING_ONLY")
    assert "answer" not in passed and "answer" not in failed
    review = results["learner_bank_review"][0]["items"]
    assert [r["stem"] for r in review] == [Q2]
    assert review[0]["answer"] == {"key": "A", "text": CORRECT[Q2]}, "review includes keys"
    practice = results["learner_practice_next"][0]["questions"]
    assert [q["stem"] for q in practice] == [Q1], "a failed question is held back"
    assert_no_key(practice)
    assert sorted(practice[0]["order"]) == [0, 1, 2]
    graded = results["learner_practice_answer"][0]
    assert graded["correct"] is True and graded["counts_toward_mastery"] is True
    assert graded["correct_answer"] == {"key": key_of(practice[0], CORRECT[Q1]), "text": CORRECT[Q1]}
    assert graded["explanation"] == WHY[0]
    fronts = results["learner_cards_next"][0]["cards"]
    assert [c["front"] for c in fronts] == ["Covector", "Dual basis", "Annihilator"]
    assert all("back" not in c for c in fronts)
    assert results["learner_card_reveal"][0]["back"] == "A linear map from vectors to numbers."
    flip = results["learner_card_review"][0]
    assert flip["counts_toward_mastery"] is False
    assert results["learner_table_get"][0]["rows"][0] == ["Eats", "nothing", "a vector"]
    assert results["learner_table_save"][0]["new"] is True
    assert results["learner_table_cards"][0] == {"parsed": 4, "imported": 4, "skipped_existing": 0}
    exported = results["learner_cards_export"][0]
    # 7 cards + Q1; Q2 failed its blind check, so its possibly wrong key is not exported
    assert exported["count"] == len(exported["text"].splitlines()) == 8
    assert Q2 not in exported["text"] and Q1 in exported["text"]


def test_the_reads_agree_on_one_store(proxy, tmp_path):
    """Import once, then read through both backends: byte-for-byte the same JSON —
    including the export, which the proxy rebuilds from a text body and a header."""

    call("learner_goal_add", goal_id="g_cov", title="Covectors")
    call("learner_study_import", markdown=NOTES, source="notes.md")
    pending = call("learner_bank_pending", goal="g_cov")
    ids = {row["stem"]: row["item_id"] for row in pending["items"]}
    call("learner_item_blind_check", item=ids[Q2], answer="C", by="solver-model")
    card = call("learner_cards_next")["cards"][0]["item_id"]
    table = call("learner_tables_list")["tables"][0]["table_id"]

    reads = [
        ("learner_bank_pending", {}),
        ("learner_bank_review", {"goal": "g_cov"}),
        ("learner_practice_next", {"n": 3}),
        ("learner_cards_next", {"n": 3}),
        ("learner_card_reveal", {"item": card}),
        ("learner_tables_list", {}),
        ("learner_table_get", {"table": table, "goal": "g_cov"}),
        ("learner_cards_export", {}),
        ("learner_cards_export", {"format": "csv", "include": "all"}),
    ]
    over_http = [call(name, **args) for name, args in reads]
    mcp_server.set_backend(mcp_server.LocalBackend())
    in_process = [call(name, **args) for name, args in reads]
    assert over_http == in_process
    assert in_process[-1]["text"].startswith("front,back,tags\n")


@pytest.mark.parametrize("over_http", [False, True])
def test_refusals_are_tool_errors_in_both_backends(over_http, proxy, tmp_path):
    if not over_http:
        mcp_server.set_backend(mcp_server.LocalBackend())
    call("learner_goal_add", goal_id="g_cov", title="Covectors")
    call("learner_goal_add", goal_id="g_other", title="Other")
    call("learner_study_import", goal="g_cov", markdown=NOTES)
    items = call("learner_bank_pending", goal="g_cov")["items"]
    table = call("learner_tables_list", goal="g_cov")["tables"][0]["table_id"]
    card = call("learner_cards_next", goal="g_cov")["cards"][0]["item_id"]

    with pytest.raises(ToolError, match="--goal is required"):
        call("learner_practice_next")
    with pytest.raises(ToolError, match="not both"):
        call("learner_study_import", goal="g_cov", markdown=NOTES, path="notes.md")
    with pytest.raises(ToolError, match="no such file"):
        call("learner_study_import", goal="g_cov", path=str(tmp_path / "missing.md"))
    with pytest.raises(ToolError, match="unknown table"):
        call("learner_table_get", table=table, goal="g_other")
    with pytest.raises(ToolError, match="flashcard"):
        call("learner_practice_answer", item=card, response="A")
    with pytest.raises(ToolError):  # the importer is the author: it cannot be the solver
        call("learner_item_blind_check", item=items[0]["item_id"], answer="B", by="import")
    # the table is found without naming its goal, even with two goals
    assert call("learner_table_get", table=table)["goal_id"] == "g_cov"


def test_a_practice_answer_replays_by_idempotency_key_through_the_proxy(proxy):
    call("learner_goal_add", goal_id="g_cov", title="Covectors")
    call("learner_study_import", markdown=NOTES)
    served = call("learner_practice_next")["questions"][0]
    args = {
        "item": served["item_id"],
        "response": "B",
        "order": served["order"],
        "idempotency_key": "practice-1",
    }
    first = call("learner_practice_answer", **args)
    assert call("learner_practice_answer", **args)["event_id"] == first["event_id"]


# ------------------------------------------------------------------------------ CLI
@pytest.fixture
def learner(monkeypatch, capsys, tmp_path):
    """``learner …`` in process, against its own data directory; returns parsed JSON
    (``raw=True``: stdout as is)."""

    monkeypatch.setattr(cli, "_STATE", {"json": None, "data_dir": None})
    data = tmp_path / "cli-data"

    def run(*args: str, raw: bool = False) -> Any:
        capsys.readouterr()
        try:
            cli.main(["--json", "--data-dir", str(data), *args])
        except SystemExit as exc:
            err = capsys.readouterr().err
            raise AssertionError(f"learner {args} exited {exc.code}: {err}") from None
        out = capsys.readouterr().out
        return out if raw else json.loads(out)

    return run


def test_cli_study_flow(learner, tmp_path):
    notes = tmp_path / "notes.md"
    notes.write_text(NOTES, encoding="utf-8")
    learner("goal", "add", "--id", "g_cov", "--title", "Covectors")

    # goal update: a date set, then cleared with ""
    updated = learner("goal", "update", "--deadline", "2026-12-01", "--minutes-per-session", "30")
    assert updated["goal"]["deadline"] == "2026-12-01"
    assert updated["changed"]["minutes_per_session"] == {"from": None, "to": 30}
    assert learner("goal", "update", "--goal", "g_cov", "--deadline", "")["goal"]["deadline"] is None

    # import: a dry run writes nothing; the real one files everything under 1.1
    dry = learner("study", "import", "--file", str(notes), "--what", "questions,tables", "--dry-run")
    assert dry["dry_run"] is True and dry["cards"] is None
    assert learner("bank", "pending")["pending"] == 0
    report = learner("study", "import", "--file", str(notes))
    assert report["source"] == "notes.md"
    assert report["questions"] == {"parsed": 2, "imported": 2, "skipped_existing": 0, "problems": []}
    assert report["cards"]["imported"] == 2 and report["tables"]["imported"] == 1
    assert [n["title"] for n in report["nodes_created"]] == ["1.1 Covectors"]
    again = learner("study", "import", "--file", str(notes), "--source", "again")
    assert again["questions"]["skipped_existing"] == 2 and again["questions"]["imported"] == 0

    # bank pending: stems and options, and not a trace of the key
    pending = learner("bank", "pending", "--limit", "10")
    assert pending["pending"] == 2
    assert_no_key(pending["items"])
    ids = {row["stem"]: row["item_id"] for row in pending["items"]}

    # blind checks: the right pick validates, a wrong one leaves it unchecked and reviewable
    right = learner("item", "blind-check", "--item", ids[Q1], "--answer", "B", "--by", "solver")
    assert right == {
        "item_id": ids[Q1],
        "result": "pass",
        "status": "PRACTICE_EVIDENCE",
        "key_matched": True,
        "ambiguous": False,
    }
    wrong = learner(
        "item", "blind-check", "--item", ids[Q2], "--answer", "B", "--by", "solver",
        "--notes", "B looked right",
    )
    assert (wrong["result"], wrong["status"], wrong["key_matched"]) == ("fail", "TEACHING_ONLY", False)
    review = learner("bank", "review")["items"]
    assert [r["item_id"] for r in review] == [ids[Q2]]
    assert review[0]["answer"]["text"] == CORRECT[Q2]
    assert (review[0]["solver_answer"]["key"], review[0]["notes"]) == ("B", "B looked right")

    # practice: the checked question only, no key until it is answered
    practice = learner("practice", "next", "--n", "5")
    assert [q["item_id"] for q in practice["questions"]] == [ids[Q1]]
    assert_no_key(practice["questions"])
    served = practice["questions"][0]
    right = key_of(served, CORRECT[Q1])
    graded = learner(
        "practice", "answer", "--item", ids[Q1], "--response", right,
        "--order", ",".join(map(str, served["order"])), "--confidence", "4",
    )
    assert graded["correct"] is True and graded["checked"] is True
    assert graded["correct_answer"] == {"key": right, "text": CORRECT[Q1]}

    # cards: fronts, then the back, then a self-rating that is never evidence
    cards_file = tmp_path / "cards.json"
    cards_file.write_text(
        json.dumps([{"front": "Annihilator", "back": "Covectors vanishing on a subspace.", "node": "1.1"}]),
        encoding="utf-8",
    )
    assert learner("cards", "add", "--file", str(cards_file))["imported"] == 1
    cards = learner("cards", "next", "--n", "10")["cards"]
    assert [c["front"] for c in cards] == ["Covector", "Dual basis", "Annihilator"]
    assert all("back" not in c for c in cards)
    revealed = learner("cards", "reveal", "--item", cards[0]["item_id"])
    assert revealed["back"] == "A linear map from vectors to numbers."
    flip = learner("cards", "review", "--item", cards[0]["item_id"], "--rating", "good")
    assert flip["rating"] == "good" and flip["counts_toward_mastery"] is False

    # tables: list, show, save, and cards out of a comparison
    tables = learner("table", "list")["tables"]
    assert [t["title"] for t in tables] == ["1.1 Covectors: Vector vs Covector"]
    shown = learner("table", "show", "--table", tables[0]["table_id"])
    assert shown["rows"] == [["Eats", "nothing", "a vector"], ["Transforms", "contravariantly", "covariantly"]]
    table_file = tmp_path / "table.json"
    table_file.write_text(
        json.dumps({"title": "Bases", "columns": ["Term", "Meaning"], "rows": [["basis", "a spanning set"]]}),
        encoding="utf-8",
    )
    assert learner("table", "save", "--file", str(table_file))["new"] is True
    made = learner("table", "cards", "--table", tables[0]["table_id"])
    assert made == {"parsed": 4, "imported": 4, "skipped_existing": 0}

    # export: --out writes the file and reports it; without --out the text is printed raw
    deck = tmp_path / "out" / "deck.tsv"
    exported = learner("cards", "export", "--out", str(deck))
    assert exported == {"count": 7, "out": str(deck.resolve())}
    text = deck.read_text(encoding="utf-8")
    assert len(text.splitlines()) == 7
    assert text.splitlines()[0] == "Covector\tA linear map from vectors to numbers.\t1.1_Covectors"
    assert learner("cards", "export", raw=True) == text
    assert learner("cards", "export", "--format", "csv", raw=True).startswith("front,back,tags")


def test_cli_errors_are_json_on_stderr(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(cli, "_STATE", {"json": None, "data_dir": None})
    data = str(tmp_path / "cli-data")
    cli.main(["--data-dir", data, "goal", "add", "--id", "g_cov", "--title", "Covectors"])
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        cli.main(["--data-dir", data, "study", "import", "--file", str(tmp_path / "nope.md")])
    assert exc.value.code == 1
    assert "no such file" in json.loads(capsys.readouterr().err)["error"]


# ------------------------------------------------------------------------------ exam prep
AUTHOR, SOLVER = "author-model", "solver-model"
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
#: the key table's explanation: nothing served before an attempt (or during a mock) has it
EXAM_WHY = "Porque sí."


def exam_bank(per_tag: int, prefix: str) -> tuple[str, str]:
    """``per_tag`` questions per topic and their key; stored option A is always right."""

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
            keys.append(f"| {n} | {tag} | A | {EXAM_WHY} |")
    return "\n".join(lines), "\n".join(keys)


def right(question: dict[str, Any]) -> str:
    return next(o["key"] for o in question["options"] if o["text"].startswith("Right"))


def wrong(question: dict[str, Any]) -> str:
    return next(o["key"] for o in question["options"] if o["text"].startswith("Wrong"))


def assert_unkeyed(questions: list[dict[str, Any]]) -> None:
    """Questions served before an attempt (practice, an open mock): stem, shuffled options
    and `order` — no key, no verdict, no explanation."""

    assert questions
    for q in questions:
        assert {"answer", "correct_answer", "your_answer", "correct", "explanation"}.isdisjoint(q)
        assert sorted(q["order"]) == list(range(len(q["options"])))
        assert EXAM_WHY not in json.dumps(q, ensure_ascii=False)


def seed_exam(goal: str = "g_exam") -> None:
    """A goal with a practice bank and a sealed one (2 per concept each), every question
    blind-checked — through the tools, so it works in either backend."""

    call("learner_goal_add", goal_id=goal, title="Exam", deadline="2026-12-04")
    for prefix, pool in (("Q", "practice"), ("M", "mock")):
        md, key = exam_bank(2, prefix)
        call("learner_study_import", goal=goal, markdown=md, key_markdown=key,
             source=f"{pool}.md", author=AUTHOR, pool=pool)
    for row in call("learner_bank_pending", goal=goal, limit=50)["items"]:
        call("learner_item_blind_check", item=row["item_id"], answer="A", by=SOLVER)


def exam_scenario() -> list[tuple[str, Any]]:
    """Every exam-prep tool, plus the practice changes (`pool`, `focus`, `order`)."""

    steps: list[tuple[str, Any]] = []

    def step(name: str, **arguments: Any) -> Any:
        result = call(name, **arguments)
        steps.append((name, result))
        return result

    call("learner_goal_add", goal_id="g_exam", title="Exam", deadline="2026-12-04")
    md, key = exam_bank(2, "Q")
    step("learner_study_import", markdown=md, key_markdown=key, source="bank.md", author=AUTHOR)
    md, key = exam_bank(2, "M")
    step("learner_study_import", markdown=md, key_markdown=key, source="mock.md", author=AUTHOR,
         pool="mock")
    step("learner_goal_blueprint", blueprint=BLUEPRINT)
    step("learner_goal_blueprint")
    for row in call("learner_bank_pending", limit=50)["items"]:
        call("learner_item_blind_check", item=row["item_id"], answer="A", by=SOLVER)
    served = step("learner_practice_next", n=2, focus="1")["questions"][0]
    step("learner_practice_answer", item=served["item_id"], response=right(served),
         order=served["order"], confidence=4)
    step("learner_mock_list")
    opened = step("learner_mock_start", n=4, minutes=10)
    session = opened["session_id"]
    step("learner_mock_show", session=session)
    first, second, third, _left_blank = opened["questions"]
    answers = [
        {"item_id": q["item_id"], "response": pick(q), "order": q["order"]}
        for q, pick in ((first, right), (second, right), (third, wrong))
    ]  # the fourth is left out: recorded as "I don't know"
    step("learner_mock_submit", session=session, answers=answers)
    step("learner_mock_show", session=session)
    step("learner_mock_list")
    step("learner_progress")
    return steps


def test_every_exam_tool_returns_the_same_json_in_both_backends(tmp_path, monkeypatch):
    # a mock shuffles with its session id as the seed, so two separate runs compare byte for
    # byte only if both draw the same id (the one-store test below needs no such pin)
    monkeypatch.setattr(exam_mod, "prefixed", lambda prefix: f"{prefix}_{'0' * 26}")
    runs = {}
    for kind in ("local", "http"):
        use_backend(kind, tmp_path / kind)
        runs[kind] = exam_scenario()

    assert EXAM_TOOLS <= {name for name, _ in runs["local"]}, "every exam tool is exercised"
    assert normalize(runs["local"], {}) == normalize(runs["http"], {})

    results: dict[str, list[Any]] = {}
    for name, result in runs["local"]:
        results.setdefault(name, []).append(result)
    practice_import, sealed_import = results["learner_study_import"]
    assert practice_import["pool"] == "practice" and practice_import["questions"]["imported"] == 6
    assert sealed_import["pool"] == "mock" and sealed_import["questions"]["imported"] == 6
    assert sealed_import["cards"] is None and sealed_import["tables"] is None, "questions only"

    stored, read_back = results["learner_goal_blueprint"]
    assert stored == read_back and stored["total_items"] == 12
    assert [s["ref"] for a in stored["areas"] for s in a["subareas"]] == ["1.1", "1.2", "2.1"]
    assert all(s["node_id"] for a in stored["areas"] for s in a["subareas"]), "refs resolved"

    practice = results["learner_practice_next"][0]
    assert practice["counts"]["focus"]["kind"] == "area"
    assert {q["area"]["code"] for q in practice["questions"]} == {"1"}
    assert_unkeyed(practice["questions"])
    graded = results["learner_practice_answer"][0]
    assert graded["correct"] is True and graded["counts_toward_mastery"] is True
    assert graded["correct_answer"]["key"] == right(practice["questions"][0])

    before, after = results["learner_mock_list"]
    assert (before["sealed_available"], before["open"], before["mocks"]) == (6, None, [])
    opened = results["learner_mock_start"][0]
    assert (opened["status"], opened["n"], opened["minutes"]) == ("open", 4, 10)
    assert_unkeyed(opened["questions"])
    shown_open, shown_done = results["learner_mock_show"]
    assert shown_open == opened, "show repeats the open mock, still sealed"
    submitted = results["learner_mock_submit"][0]
    assert submitted == shown_done
    assert (submitted["status"], submitted["n"], submitted["answered"], submitted["correct"]) == (
        "submitted", 4, 3, 2,
    )
    assert [i["your_answer"] is None for i in submitted["items"]] == [False, False, False, True]
    assert all(i["correct_answer"]["text"].startswith("Right") for i in submitted["items"])
    assert all(i["explanation"] == EXAM_WHY for i in submitted["items"]), "keys after submit"
    assert after["open"] is None and after["sealed_available"] == 2
    assert [(m["n"], m["answered"], m["correct"]) for m in after["mocks"]] == [(4, 3, 2)]

    progress = results["learner_progress"][0]
    assert progress["has_blueprint"] is True and progress["deadline"] == "2026-12-04"
    assert [a["code"] for a in progress["areas"]] == ["1", "2"]
    # one practice answer + four mock answers (the blank one as "I don't know")
    assert (progress["totals"]["first_attempts"], progress["totals"]["first_correct"]) == (5, 3)
    assert len(progress["mocks"]) == 1


def test_the_exam_reads_agree_on_one_store(proxy):
    """Seed and open a mock over HTTP, then read through both backends: the same JSON —
    while it is open and, after a submit through the other backend, graded."""

    seed_exam()
    call("learner_goal_blueprint", goal="g_exam", blueprint=BLUEPRINT)
    session = call("learner_mock_start", n=3)["session_id"]
    reads = [
        ("learner_goal_blueprint", {}),
        ("learner_progress", {}),
        ("learner_mock_list", {"goal": "g_exam"}),
        ("learner_mock_show", {"session": session}),
        ("learner_practice_next", {"n": 5, "focus": "2.1"}),
    ]
    over_http = [call(name, **args) for name, args in reads]
    mcp_server.set_backend(mcp_server.LocalBackend())
    assert [call(name, **args) for name, args in reads] == over_http
    assert over_http[3]["status"] == "open" and over_http[2]["open"]["session_id"] == session
    assert {q["ref"] for q in over_http[4]["questions"]} == {"2.1"}, "focus on one concept"

    answers = [
        {"item_id": q["item_id"], "response": right(q), "order": q["order"]}
        for q in over_http[3]["questions"]
    ]
    graded = call("learner_mock_submit", session=session, answers=answers)
    assert (graded["answered"], graded["correct"]) == (3, 3)
    in_process = call("learner_mock_show", session=session)
    mcp_server.set_backend(mcp_server.HttpBackend("http://testserver", client=proxy))
    assert call("learner_mock_show", session=session) == in_process == graded


@pytest.mark.parametrize("over_http", [False, True])
def test_exam_refusals_are_tool_errors_in_both_backends(over_http, proxy):
    if not over_http:
        mcp_server.set_backend(mcp_server.LocalBackend())
    call("learner_goal_add", goal_id="g_empty", title="Empty")
    with pytest.raises(ToolError, match="no blueprint"):
        call("learner_goal_blueprint")
    with pytest.raises(ToolError, match="no checked sealed questions"):
        call("learner_mock_start")
    seed_exam("g_ready")

    with pytest.raises(ToolError, match="--goal is required"):
        call("learner_progress")
    with pytest.raises(ToolError, match="non-empty `areas`"):
        call("learner_goal_blueprint", goal="g_ready", blueprint={"exam": "x"})
    unknown_ref = {"areas": [{"code": "9", "title": "Nope", "subareas": [
        {"ref": "9.9", "title": "Missing", "items": 3}]}]}
    with pytest.raises(ToolError, match="blueprint rejected"):
        call("learner_goal_blueprint", goal="g_ready", blueprint=unknown_ref)
    with pytest.raises(ToolError, match="questions only"):
        call("learner_study_import", goal="g_ready", markdown=NOTES, what=["cards"], pool="mock")
    with pytest.raises(ToolError, match="unknown focus"):
        call("learner_practice_next", goal="g_ready", focus="nowhere")
    served = call("learner_practice_next", goal="g_ready")["questions"][0]
    with pytest.raises(ToolError, match="permutation"):
        call("learner_practice_answer", item=served["item_id"], response="A", order=[0, 0, 1])

    opened = call("learner_mock_start", goal="g_ready", n=2)
    with pytest.raises(ToolError, match="still open"):
        call("learner_mock_start", goal="g_ready")
    with pytest.raises(ToolError, match="not a question of mock"):
        call("learner_mock_submit", session=opened["session_id"],
             answers=[{"item_id": served["item_id"], "response": "A"}])
    assert call("learner_mock_show", session=opened["session_id"])["status"] == "open", (
        "a rejected submit writes nothing"
    )
    call("learner_mock_submit", session=opened["session_id"], answers=[])
    with pytest.raises(ToolError, match="already submitted"):
        call("learner_mock_submit", session=opened["session_id"], answers=[])
    with pytest.raises(ToolError, match="unknown session"):
        call("learner_mock_show", session="s_nope")


def test_a_mock_submit_replays_by_idempotency_key_through_the_proxy(proxy):
    seed_exam()
    opened = call("learner_mock_start", n=2)
    answers = [
        {"item_id": q["item_id"], "response": right(q), "order": q["order"]}
        for q in opened["questions"]
    ]
    args = {"session": opened["session_id"], "answers": answers, "idempotency_key": "mock-1"}
    first = call("learner_mock_submit", **args)
    assert call("learner_mock_submit", **args) == first, "replayed, not 'already submitted'"


def test_the_tool_descriptions_carry_the_shuffle_and_sealing_rules():
    async def _go():
        async with Client(mcp_server.mcp) as client:
            return {t.name: t.description or "" for t in await client.list_tools()}

    described = asyncio.run(_go())
    for name in ("learner_practice_next", "learner_practice_answer", "learner_mock_start"):
        assert "order" in described[name] and "unchanged" in described[name], name
    assert "never state" in described["learner_practice_next"].lower()
    assert "no feedback" in described["learner_mock_start"].lower()
    assert "shuffled order" in mcp_server.INSTRUCTIONS


def test_cli_exam_flow(learner, tmp_path):
    learner("goal", "add", "--id", "g_exam", "--title", "Exam", "--deadline", "2026-12-04")
    for prefix, pool in (("Q", "practice"), ("M", "mock")):
        md, key = exam_bank(2, prefix)
        (tmp_path / f"{pool}.md").write_text(md, encoding="utf-8")
        (tmp_path / f"{pool}-key.md").write_text(key, encoding="utf-8")
        report = learner(
            "study", "import", "--file", str(tmp_path / f"{pool}.md"),
            "--key-file", str(tmp_path / f"{pool}-key.md"), "--author", AUTHOR, "--pool", pool,
        )
        assert (report["pool"], report["questions"]["imported"]) == (pool, 6)

    # blueprint: none yet, then set from a file (fields it does not take are ignored), shown
    with pytest.raises(AssertionError, match="no blueprint"):
        learner("goal", "blueprint")
    blueprint_file = tmp_path / "blueprint.json"
    blueprint_file.write_text(json.dumps({**BLUEPRINT, "total_items": 999}), encoding="utf-8")
    stored = learner("goal", "blueprint", "--goal", "g_exam", "--file", str(blueprint_file))
    assert stored["total_items"] == 12 and stored["exam"] == "Demo exam"
    assert learner("goal", "blueprint") == stored

    for row in learner("bank", "pending", "--limit", "50")["items"]:
        learner("item", "blind-check", "--item", row["item_id"], "--answer", "A", "--by", SOLVER)

    # practice: focused on área 2, answered by the served letter and order
    practice = learner("practice", "next", "--n", "5", "--focus", "2")
    assert practice["counts"]["focus"]["kind"] == "area"
    assert {q["ref"] for q in practice["questions"]} == {"2.1"}
    assert_unkeyed(practice["questions"])
    served = practice["questions"][0]
    order = ",".join(map(str, served["order"]))
    graded = learner(
        "practice", "answer", "--item", served["item_id"], "--response", right(served),
        "--order", order,
    )
    assert graded["correct"] is True and graded["correct_answer"]["key"] == right(served)
    with pytest.raises(AssertionError, match="--order must be option indexes"):
        learner("practice", "answer", "--item", served["item_id"], "--response", "A",
                "--order", "b,a,c")

    # a sealed mock: start, show (no keys), submit from a file, show the result, list
    assert learner("mock", "list")["sealed_available"] == 6
    opened = learner("mock", "start", "--n", "3", "--minutes", "5")
    assert (opened["status"], opened["n"], opened["minutes"]) == ("open", 3, 5)
    assert_unkeyed(opened["questions"])
    session = opened["session_id"]
    assert learner("mock", "show", "--session", session) == opened
    answers_file = tmp_path / "answers.json"
    answers_file.write_text(json.dumps({"answers": [
        {"item_id": q["item_id"], "response": right(q), "order": q["order"]}
        for q in opened["questions"][:2]
    ]}), encoding="utf-8")
    result = learner("mock", "submit", "--session", session, "--file", str(answers_file))
    assert (result["status"], result["answered"], result["correct"]) == ("submitted", 2, 2)
    assert learner("mock", "show", "--session", session) == result
    listed = learner("mock", "list", "--goal", "g_exam")
    assert listed["open"] is None and [m["correct"] for m in listed["mocks"]] == [2]

    progress = learner("progress")
    assert progress["has_blueprint"] is True and len(progress["mocks"]) == 1
    assert progress["totals"]["first_attempts"] == 4  # 1 practice answer + the mock's 3
