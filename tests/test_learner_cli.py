"""CLI smoke test: every command in CONTRACTS.md, driven through a real subprocess."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from learning_tutor.learner.store import CURRENT_SCHEMA_VERSION

CONSOLE_SCRIPT = Path(sys.executable).parent / ("learner.exe" if os.name == "nt" else "learner")


def base_command() -> list[str]:
    if CONSOLE_SCRIPT.exists():
        return [str(CONSOLE_SCRIPT)]
    if shutil.which("uv"):
        return ["uv", "run", "learner"]
    pytest.skip("neither the `learner` console script nor `uv` is available")
    raise AssertionError


def run(args: list[str], data_dir: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    env = {**os.environ, "LT_DATA_DIR": str(data_dir), "PYTHONIOENCODING": "utf-8"}
    for var in ("LT_VAULT_DIR", "LT_PROBE_BUDGET", "LT_HOLDOUT_FRACTION", "LT_EVIDENCE_MODEL"):
        env.pop(var, None)
    proc = subprocess.run(
        base_command() + args,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    if check:
        assert proc.returncode == 0, f"{args} failed: {proc.stderr or proc.stdout}"
    return proc


def out(proc: subprocess.CompletedProcess):
    return json.loads(proc.stdout)


def test_cli_end_to_end(tmp_path):
    data = tmp_path / "data"
    graph_file = tmp_path / "graph.json"
    item_file = tmp_path / "item.json"
    ops_file = tmp_path / "ops.json"
    log_file = tmp_path / "session-log.md"
    graph_file.write_text(
        json.dumps(
            {
                "nodes": [
                    {"title": "Vectors"},
                    {"title": "Covectors", "aliases": ["dual vectors"]},
                    {"title": "Wedge product"},
                ],
                "edges": [
                    {"from": "Vectors", "to": "Covectors", "type": "strict_prerequisite", "provenance": "course"},
                    {"from": "Covectors", "to": "Wedge product", "type": "strict_prerequisite", "provenance": "model"},
                ],
            }
        ),
        encoding="utf-8",
    )
    item_file.write_text(
        json.dumps(
            {
                "stem": "What eats a vector and returns a number?",
                "options": ["a covector", "a vector"],
                "answer": "a covector",
                "distractor_misconceptions": {"a vector": "vectors and covectors are the same"},
                "kind": "mc",
                "components": ["dual space"],
            }
        ),
        encoding="utf-8",
    )
    log_file.write_text("# Session\n\nWorked on covectors.\n", encoding="utf-8")

    # init
    init = out(run(["init"], data))
    assert init["schema_version"] == CURRENT_SCHEMA_VERSION
    assert Path(init["db"]).exists()

    # goal add
    goal = out(
        run(
            [
                "goal", "add",
                "--id", "differential-forms",
                "--title", "Differential forms",
                "--depth", "apply",
                "--deadline", "2027-01-01",
                "--minutes-per-session", "45",
                "--purpose", "read the Spivak chapter",
            ],
            data,
        )
    )
    assert goal["goal_id"] == "differential-forms"
    assert Path(goal["sources_dir"]).is_dir()
    assert Path(goal["sources_dir"]).parent == data / "sources"

    # graph import / show
    imported = out(run(["graph", "import", "--goal", "differential-forms", "--file", str(graph_file)], data))
    assert imported["graph_version"] == 1
    assert len(imported["nodes"]) == 3
    covectors = imported["nodes"][1]

    shown = out(run(["graph", "show", "--goal", "differential-forms", "--format", "json"], data))
    assert len(shown["nodes"]) == 3
    mermaid = run(["graph", "show", "--goal", "differential-forms", "--format", "mermaid"], data)
    assert "classDef unknown" in mermaid.stdout

    # item add / validate / lifecycle guard
    item = out(run(["item", "add", "--node", "dual vectors", "--file", str(item_file), "--author", "claude"], data))
    assert item["status"] == "TEACHING_ONLY"
    assert item["item_version_id"]

    same_author = run(["item", "validate", "--item", item["item_id"], "--by", "claude", "--result", "pass"], data, check=False)
    assert same_author.returncode != 0
    assert "error" in json.loads(same_author.stderr)

    validated = out(
        run(
            ["item", "validate", "--item", item["item_id"], "--by", "solver-subagent",
             "--result", "pass", "--notes", "solved independently"],
            data,
        )
    )
    assert validated["status"] == "PRACTICE_EVIDENCE"

    # session + next + record
    session = out(run(["session", "start", "--goal", "differential-forms", "--channel", "claude-code"], data))
    picks = out(run(["next", "--goal", "differential-forms", "--session", session["session_id"], "--mode", "probe"], data))
    assert picks["mode"] == "probe"
    assert picks["picks"]

    recorded = out(
        run(
            [
                "record", "answer",
                "--session", session["session_id"],
                "--item", item["item_id"],
                "--response", "a covector",
                "--correct", "1",
                "--confidence", "4",
                "--assistance", "0",
                "--context", "probe",
                # graded against the item's key rather than by the tutoring model: the
                # default (host_llm) is recorded but never counts toward mastery.
                "--evaluation-method", "blind_solver",
            ],
            data,
        )
    )
    assert recorded["wrote_evidence"] is True
    assert recorded["node_state_name"] == "fragile"

    unearned = out(
        run(
            [
                "record", "answer",
                "--session", session["session_id"],
                "--item", item["item_id"],
                "--response", "a covector",
                "--correct", "1",
                "--assistance", "5",
                "--context", "in-session",
            ],
            data,
        )
    )
    assert unearned["counts_toward_mastery"] is False

    teach_back = out(
        run(
            ["record", "teach-back", "--session", session["session_id"], "--node", covectors,
             "--score", "3", "--rubric-version", "rubric-v1", "--assistance", "0"],
            data,
        )
    )
    assert teach_back["passed"] is True

    # promote once the item has enough uses (a teach-back is not an item use)
    too_few = run(["item", "promote", "--item", item["item_id"]], data, check=False)
    assert too_few.returncode != 0
    assert "recorded uses" in json.loads(too_few.stderr)["error"]
    run(
        ["record", "answer", "--session", session["session_id"], "--item", item["item_id"],
         "--response", "a covector", "--correct", "1", "--assistance", "1",
         "--context", "in-session"],
        data,
    )
    promoted = out(run(["item", "promote", "--item", item["item_id"]], data))
    assert promoted["status"] == "MASTERY_ELIGIBLE"

    # misconception sequence
    claim = "vectors and covectors are the same"
    suspected = out(run(["misconception", "suspect", "--node", covectors, "--claim", claim], data))
    assert suspected["state"] == "suspected"
    out_of_order = run(
        ["misconception", "confirm-step", "--node", covectors, "--claim", claim,
         "--step", "counterexample", "--outcome", "held"],
        data,
        check=False,
    )
    assert out_of_order.returncode != 0
    for step in ("reasoning", "prediction", "counterexample"):
        step_result = out(
            run(["misconception", "confirm-step", "--node", covectors, "--claim", claim,
                 "--step", step, "--outcome", "held"], data)
        )
    assert step_result["state"] == "active"
    resolved = out(run(["misconception", "resolve", "--node", covectors, "--claim", claim], data))
    assert resolved["state"] == "resolved"

    # disputes
    dispute = out(
        run(["dispute", "open", "--type", "I already know this", "--node", covectors,
             "--note", "did this at university"], data)
    )
    assert dispute["mastery_granted"] is False
    settled = out(
        run(["dispute", "settle", "--dispute", dispute["dispute_id"], "--outcome", "rejected",
             "--evidence", "failed both check items"], data)
    )
    assert settled["outcome"] == "rejected"

    # graph revise with evidence migration
    ops_file.write_text(
        json.dumps({"ops": [{"op": "split", "node": covectors,
                             "into": [{"title": "Covector basics"}, {"title": "Covector algebra"}]}]}),
        encoding="utf-8",
    )
    revised = out(run(["graph", "revise", "--goal", "differential-forms", "--ops", str(ops_file)], data))
    assert revised["ops"][0]["evidence_events_copied"] > 0

    # session end + log
    ended = out(run(["session", "end", "--session", session["session_id"], "--summary", "covered covectors"], data))
    assert ended["ended_at"]
    logged = out(run(["log", "--session", session["session_id"], "--file", str(log_file)], data))
    assert Path(logged["log"]).exists()

    # views + metrics + export
    summary = out(run(["summary", "--goal", "differential-forms", "--format", "md"], data))
    assert summary["markdown"].startswith("# Learner")
    assert (data / "learner" / "state.json").exists()

    metrics = out(run(["metrics", "--goal", "differential-forms"], data))
    assert set(metrics) >= {"holdout_success_7d", "false_mastery", "item_rejection"}

    holdouts = out(run(["holdout-check", "--goal", "differential-forms"], data))
    assert holdouts["due"] >= 0

    exported = out(run(["export", "--out", str(tmp_path / "events.jsonl")], data))
    assert exported["events"] > 0
    lines = (tmp_path / "events.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == exported["events"]
    assert all(json.loads(line)["event_id"] for line in lines)


def test_json_flag_is_accepted_anywhere(tmp_path):
    data = tmp_path / "data"
    run(["init"], data)
    before = run(["--json", "metrics"], data)
    after = run(["metrics", "--json"], data)
    assert json.loads(before.stdout) and json.loads(after.stdout)


def test_failures_print_json_on_stderr_and_exit_non_zero(tmp_path):
    data = tmp_path / "data"
    run(["init"], data)
    proc = run(["summary", "--goal", "does-not-exist"], data, check=False)
    assert proc.returncode != 0
    assert json.loads(proc.stderr)["error"]
    assert proc.stdout.strip() == ""

    bad_flags = run(["goal", "add", "--title", "no id"], data, check=False)
    assert bad_flags.returncode != 0
    assert json.loads(bad_flags.stderr)["error"]


def test_data_dir_flag_overrides_the_environment(tmp_path):
    elsewhere = tmp_path / "elsewhere"
    result = out(run(["init", "--data-dir", str(elsewhere)], tmp_path / "data"))
    assert Path(result["db"]).parent.parent == elsewhere
    assert (elsewhere / "learner" / "events.db").exists()


def test_help_exits_zero(tmp_path):
    proc = run(["--help"], tmp_path / "data")
    assert "holdout-check" in proc.stdout
