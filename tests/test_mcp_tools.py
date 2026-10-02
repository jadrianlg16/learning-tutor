"""The MCP server: the tool surface, and that it is the same surface in both backends.

Driven through FastMCP's in-memory client, so the tools are exercised the way a real MCP
client calls them (name, arguments, structured content) rather than as plain functions.

The two backends are the point of these tests: with `LT_LEARNER_URL` unset the tools open
the database in this process, with it set they proxy to `learner-svc`. Same names, same
arguments, same JSON.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import GRAPH, item_spec
from fastapi.testclient import TestClient
from fastmcp import Client
from fastmcp.exceptions import ToolError

from learning_tutor import mcp_server
from learning_tutor.learner_svc import create_app

#: Every tool CONTRACTS.md names: the CLI verbs with underscores, plus the four Stage 2
#: reads/writes that exist so nothing but learner-svc opens events.db, plus the study tools
#: (CONTRACTS.md, *Study tools*) and the exam-prep tools (*Exam blueprint, mixed practice
#: and sealed mock exams*) — both exercised in test_study_surfaces.py.
STUDY_TOOLS = {
    "learner_goal_update",
    "learner_study_import",
    "learner_bank_pending",
    "learner_bank_review",
    "learner_item_blind_check",
    "learner_practice_next",
    "learner_practice_answer",
    "learner_cards_add",
    "learner_cards_next",
    "learner_card_reveal",
    "learner_card_review",
    "learner_cards_export",
    "learner_tables_list",
    "learner_table_get",
    "learner_table_save",
    "learner_table_cards",
}
EXAM_TOOLS = {
    "learner_goal_blueprint",
    "learner_progress",
    "learner_mock_start",
    "learner_mock_show",
    "learner_mock_submit",
    "learner_mock_list",
}
EXPECTED_TOOLS = STUDY_TOOLS | EXAM_TOOLS | {
    "learner_dispute_open",
    "learner_dispute_settle",
    "learner_disputes",
    "learner_events",
    "learner_export",
    "learner_log",
    "learner_misconceptions",
    "learner_goal_add",
    "learner_graph_import",
    "learner_graph_revise",
    "learner_graph_show",
    "learner_holdout_check",
    "learner_item_add",
    "learner_item_promote",
    "learner_item_validate",
    "learner_metrics",
    "learner_misconception",
    "learner_next",
    "learner_record_answer",
    "learner_record_teach_back",
    "learner_session_end",
    "learner_session_start",
    "learner_summary",
}


def call(name: str, **arguments):
    """Call one tool through the in-memory MCP client and return its structured output."""

    async def _go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(name, arguments)

    return asyncio.run(_go()).structured_content


def tool_names() -> set[str]:
    async def _go():
        async with Client(mcp_server.mcp) as client:
            return {tool.name for tool in await client.list_tools()}

    return asyncio.run(_go())


@pytest.fixture(autouse=True)
def local_backend(monkeypatch, settings):
    """Default every test to the in-process backend against the test data directory."""

    monkeypatch.delenv("LT_LEARNER_URL", raising=False)
    monkeypatch.setenv("LT_MCP_ENABLED", "1")
    mcp_server.set_backend(mcp_server.LocalBackend())
    yield
    mcp_server.set_backend(None)


@pytest.fixture
def proxy(settings):
    """Point the MCP server at a live `learner-svc` app instead of the database."""

    client = TestClient(create_app(settings))
    mcp_server.set_backend(mcp_server.HttpBackend("http://testserver", client=client))
    yield client
    mcp_server.set_backend(mcp_server.LocalBackend())


def seed() -> dict[str, str]:
    """A goal, a graph and one validated item — through the tools themselves."""

    call("learner_goal_add", goal_id="g_forms", title="Differential forms", depth="apply")
    call("learner_graph_import", goal="g_forms", nodes=GRAPH["nodes"], edges=GRAPH["edges"])
    graph = call("learner_graph_show", goal="g_forms")
    nodes = {node["title"]: node["node_id"] for node in graph["nodes"]}
    item = call(
        "learner_item_add",
        node=nodes["Covectors"],
        spec=item_spec(),
        author="author-model",
    )
    call(
        "learner_item_validate",
        item=item["item_id"],
        by="solver-model",
        result="pass",
        evaluation_method="blind_solver",
    )
    return {**nodes, "item_id": item["item_id"]}


# ------------------------------------------------------------------------ the surface
def test_every_contracted_tool_is_registered():
    assert tool_names() == EXPECTED_TOOLS
    # 23 core + 16 study + 6 exam prep: docs/modules/learner-svc.md quotes these counts
    assert (len(STUDY_TOOLS), len(EXAM_TOOLS), len(EXPECTED_TOOLS)) == (16, 6, 45)


def test_the_tools_are_named_after_the_cli_verbs():
    assert all(name.startswith("learner_") for name in tool_names())
    assert not any("-" in name for name in tool_names())


def test_tools_return_structured_output():
    async def _go():
        async with Client(mcp_server.mcp) as client:
            return {t.name: t.outputSchema for t in await client.list_tools()}

    schemas = asyncio.run(_go())
    assert schemas["learner_summary"]["type"] == "object"
    assert schemas["learner_record_answer"]["type"] == "object"


# ---------------------------------------------------------------------------- in-process
def test_summary_in_process():
    seed()
    result = call("learner_summary", goal="g_forms")
    assert result["format"] == "md"
    assert "Goal: Differential forms" in result["markdown"]


def test_record_answer_in_process():
    ids = seed()
    result = call(
        "learner_record_answer",
        item=ids["item_id"],
        correct=True,
        response="a covector",
        evaluation_method="blind_solver",
    )
    assert result["wrote_evidence"] is True
    assert result["counts_toward_mastery"] is True

    self_graded = call("learner_record_answer", item=ids["item_id"], correct=True)
    assert self_graded["evaluation_method"] == "host_llm"
    assert self_graded["counts_toward_mastery"] is False


def test_a_rejected_operation_becomes_a_tool_error():
    with pytest.raises(ToolError, match="unknown goal"):
        call("learner_summary", goal="nope")


def test_next_picks_a_probe_question():
    ids = seed()
    picks = call("learner_next", goal="g_forms", mode="probe")
    assert picks["mode"] == "probe"
    assert picks["picks"]
    served = {pick.get("item_id") for pick in picks["picks"]}
    assert served <= {ids["item_id"], None}


# --------------------------------------------------------------------------- over HTTP
def test_summary_over_the_http_proxy(proxy):
    seed()
    assert isinstance(mcp_server.backend(), mcp_server.HttpBackend)
    result = call("learner_summary", goal="g_forms")
    assert result["format"] == "md"
    assert "Goal: Differential forms" in result["markdown"]


def test_record_answer_over_the_http_proxy(proxy):
    ids = seed()
    result = call(
        "learner_record_answer",
        item=ids["item_id"],
        correct=True,
        response="a covector",
        evaluation_method="rubric",
    )
    assert result["wrote_evidence"] is True
    assert result["counts_toward_mastery"] is True

    self_graded = call("learner_record_answer", item=ids["item_id"], correct=True)
    assert self_graded["evaluation_method"] == "host_llm"
    assert self_graded["counts_toward_mastery"] is False


def test_the_http_proxy_surfaces_errors_as_tool_errors(proxy):
    with pytest.raises(ToolError, match="unknown goal"):
        call("learner_summary", goal="nope")


def test_idempotency_replays_through_the_proxy(proxy):
    ids = seed()
    args = dict(
        item=ids["item_id"],
        correct=True,
        response="a covector",
        evaluation_method="rubric",
        idempotency_key="mcp-1",
    )
    first = call("learner_record_answer", **args)
    second = call("learner_record_answer", **args)
    assert first["event_id"] == second["event_id"]


def test_omitting_the_goal_resolves_the_same_way_in_both_backends(proxy):
    """`goal` is optional whenever there is exactly one — including through the proxy,
    where the goal sits in the URL path and the service cannot resolve it."""

    seed()
    over_http = call("learner_summary")
    assert over_http["goal_id"] == "g_forms"
    assert call("learner_metrics")["goal_id"] == "g_forms"
    assert call("learner_holdout_check")["goal_id"] == "g_forms"
    assert call("learner_graph_show")["goal_id"] == "g_forms"

    mcp_server.set_backend(mcp_server.LocalBackend())
    in_process = call("learner_summary")
    assert in_process == over_http


def test_omitting_the_goal_with_two_goals_is_an_error_in_both_backends(proxy):
    seed()
    call("learner_goal_add", goal_id="g_other", title="Another")
    with pytest.raises(ToolError, match="--goal is required"):
        call("learner_summary")

    mcp_server.set_backend(mcp_server.LocalBackend())
    with pytest.raises(ToolError, match="--goal is required"):
        call("learner_summary")


def test_the_backend_is_chosen_by_the_environment(monkeypatch):
    mcp_server.set_backend(None)
    monkeypatch.setenv("LT_LEARNER_URL", "http://learner-svc:5034")
    assert isinstance(mcp_server.backend(), mcp_server.HttpBackend)
    assert mcp_server.backend().describe()["url"] == "http://learner-svc:5034"

    mcp_server.set_backend(None)
    monkeypatch.delenv("LT_LEARNER_URL")
    assert isinstance(mcp_server.backend(), mcp_server.LocalBackend)


# ----------------------------------------------------------- the Stage 2 additions
def _answer(ids: dict[str, str]) -> None:
    call(
        "learner_record_answer",
        item=ids["item_id"],
        correct=True,
        response="a covector",
        evaluation_method="blind_solver",
    )


@pytest.mark.parametrize("over_http", [False, True])
def test_events_disputes_and_misconceptions_read_the_same_rows(over_http, proxy):
    """The four new tools, in both backends. `proxy` sets the HTTP backend; the False
    case puts it back."""

    if not over_http:
        mcp_server.set_backend(mcp_server.LocalBackend())
    ids = seed()
    call("learner_dispute_open", type="ambiguous question", node=ids["Covectors"])
    call(
        "learner_misconception",
        action="suspect",
        node=ids["Covectors"],
        claim="covectors are just vectors",
    )
    _answer(ids)  # last, so it is the newest row on this node

    events = call("learner_events", node=ids["Covectors"])
    assert events["order"] == "newest_first"
    assert events["events"][0]["response"] == "a covector"
    assert call("learner_events", kind="answer", limit=1)["count"] == 1

    disputes = call("learner_disputes", goal="g_forms")
    assert disputes["disputes"][0]["type"] == "ambiguous question"
    assert call("learner_disputes", status="settled")["count"] == 0

    misconceptions = call("learner_misconceptions", node=ids["Covectors"])
    assert misconceptions["misconceptions"][0]["state"] == "suspected"


@pytest.mark.parametrize("over_http", [False, True])
def test_learner_log_writes_the_vault_file_in_both_backends(over_http, proxy, settings):
    if not over_http:
        mcp_server.set_backend(mcp_server.LocalBackend())
    seed()
    session = call("learner_session_start", goal="g_forms")["session_id"]
    result = call("learner_log", session=session, markdown="# Log\n\nCovectors.\n")

    assert set(result) == {"session_id", "log"}, "one surface in both backends"
    written = Path(result["log"])
    assert written.parent == Path(settings.sessions_dir)
    assert written.read_text(encoding="utf-8") == "# Log\n\nCovectors.\n"


def test_a_bad_log_filename_is_a_tool_error():
    seed()
    session = call("learner_session_start", goal="g_forms")["session_id"]
    with pytest.raises(ToolError, match="invalid log filename"):
        call("learner_log", session=session, markdown="x", filename="../escape.md")


# ------------------------------------------------------------------------------ gate
def test_the_gate_refuses_instead_of_touching_the_store(monkeypatch):
    monkeypatch.setenv("LT_MCP_ENABLED", "0")
    result = call("learner_summary", goal="g_forms")
    assert result["available"] is False
    assert result["env_var"] == "LT_MCP_ENABLED"


def test_the_gate_is_open_by_default(monkeypatch):
    monkeypatch.delenv("LT_MCP_ENABLED", raising=False)
    assert mcp_server._mcp_enabled() is True
    for value in ("0", "false", "no", "OFF"):
        monkeypatch.setenv("LT_MCP_ENABLED", value)
        assert mcp_server._mcp_enabled() is False


# ------------------------------------------------------------------------------ argv
def test_the_entry_point_defaults_to_stdio():
    assert mcp_server.parse_args([]).http is False
    args = mcp_server.parse_args(["--http", "--port", "5099"])
    assert args.http is True
    assert args.port == 5099
