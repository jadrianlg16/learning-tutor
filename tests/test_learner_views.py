"""learner.md and state.json: evidence, never decimals."""

from __future__ import annotations

import json
import re

from conftest import ago, make_item

from learning_tutor.learner import api, views
from learning_tutor.learner import misconceptions as misc_mod
from learning_tutor.learner.store import CURRENT_SCHEMA_VERSION


def teach_to_known(store, node, stem_prefix="s"):
    item = make_item(store, node, stem=f"{stem_prefix} {node}")
    for days, context in ((9, "in-session"), (8, "in-session"), (2, "delayed")):
        api.record_answer(
            store,
            evaluation_method="rubric",
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            assistance_level=0,
            context=context,
            ts=ago(days=days),
        )
    return item


def test_learner_md_has_no_decimals(store, graph):
    node = graph["Covectors"]
    teach_to_known(store, graph["Vectors"], "v")
    item = make_item(store, node, stem="fragile stem")
    for assistance, days in ((3, 6), (0, 5)):
        api.record_answer(
            store,
            evaluation_method="rubric",
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            assistance_level=assistance,
            context="in-session",
            ts=ago(days=days),
        )
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a vector",
        correct=False,
        assistance_level=1,
        context="delayed",
        ts=ago(days=1),
    )
    session = api.session_start(store, goal_id="g_forms")["session_id"]
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=session,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=2,
        context="in-session",
    )
    api.session_end(store, session, "covered covectors")

    result = api.summary(store, "g_forms", "md")
    text = result["markdown"]
    assert not views.has_decimals(text), text
    assert not re.search(r"\d\.\d", text), text
    assert "uncertainty" in text
    assert "%" not in text


def test_learner_md_layout(store, graph):
    teach_to_known(store, graph["Vectors"], "v")
    store.settings.notes_path.write_text(
        "# Notes\n\n## Prefs\n\n- formal-first over analogy\n- short steps\n", encoding="utf-8"
    )
    text = api.summary(store, "g_forms", "md")["markdown"]
    assert text.startswith("# Learner — ")
    assert "Goal: Differential forms · apply · deadline" in text
    assert "sessions left of ~" in text
    assert "Known:    Vectors" in text
    assert "Unknown:  " in text
    assert "Due today:" in text
    assert "Prefs: formal-first over analogy · short steps" in text
    assert str(store.settings.learner_md_path) == str(store.settings.vault_dir / "learner.md")
    assert store.settings.learner_md_path.read_text(encoding="utf-8") == text


def test_only_active_misconceptions_reach_the_view(store, graph):
    node = graph["Covectors"]
    claim = "vectors and covectors are the same thing"
    misc_mod.suspect(store, node, claim)
    text = api.summary(store, "g_forms", "md")["markdown"]
    assert "Misconception" not in text

    for step in ("reasoning", "prediction", "counterexample"):
        misc_mod.confirm_step(store, node, claim, step=step, outcome="held")
    text = api.summary(store, "g_forms", "md")["markdown"]
    assert f'Misconception (active): "{claim}"' in text


def test_known_nodes_outside_the_goal_collapse_to_one_line(store, graph):
    api.goal_add(store, goal_id="g_la", title="Linear algebra", depth="explain")
    api.graph_import(
        store,
        "g_la",
        {"nodes": [{"title": "Matrix rank", "domain": "linear algebra"}, {"title": "Eigenvalues", "domain": "linear algebra"}], "edges": []},
    )
    from learning_tutor.learner import graph as graph_mod

    for node in graph_mod.get_nodes(store, "g_la"):
        teach_to_known(store, node.node_id, node.title)
    text = api.summary(store, "g_forms", "md")["markdown"]
    assert "+2 known in linear algebra" in text


def test_feasibility_flags_a_tight_deadline(store, goal):
    api.graph_import(
        store,
        goal,
        {"nodes": [{"title": f"Topic {i}"} for i in range(40)], "edges": []},
    )
    store.update("goals", {"goal_id": goal}, {"deadline": ago(days=-7)[:10], "minutes_per_session": 30})
    summary = views.build_summary(store, goal)
    assert summary.feasibility["sessions_needed"] > summary.feasibility["sessions_left"]
    assert summary.feasibility["tight"] is True
    assert summary.goal_line.endswith("!")


def test_state_json_is_regenerated_on_every_write(store, graph):
    path = store.settings.state_path
    api.summary(store, "g_forms", "json")
    first = json.loads(path.read_text(encoding="utf-8"))
    assert first["schema_version"] == CURRENT_SCHEMA_VERSION
    assert len(first["nodes"]) == 5
    assert first["node_states"][graph["Covectors"]]["state"] == "unknown"

    teach_to_known(store, graph["Covectors"], "c")
    second = json.loads(path.read_text(encoding="utf-8"))
    assert second["node_states"][graph["Covectors"]]["state"] == "known"
    assert second["generated_at"] >= first["generated_at"]


def test_summary_json_carries_the_same_evidence(store, graph):
    teach_to_known(store, graph["Vectors"], "v")
    data = api.summary(store, "g_forms", "json")
    assert data["known"] == ["Vectors"]
    assert data["due_today"] >= 0
    assert data["goal_id"] == "g_forms"


def test_notes_md_is_created_but_never_holds_numbers(settings):
    api.init(settings)
    text = settings.notes_path.read_text(encoding="utf-8")
    assert "## Prefs" in text
    assert not re.search(r"\d\.\d", text)
