"""Stable ids, alias resolution, and evidence migration through split and merge."""

from __future__ import annotations

import pytest
from conftest import ago, make_item

from learning_tutor.learner import api
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner import graph as graph_mod
from learning_tutor.learner.ids import node_id as make_node_id
from learning_tutor.learner.store import LearnerError


def test_node_ids_are_stable_and_shaped(store, graph):
    for title, node_id in graph.items():
        assert node_id.startswith("n_")
        assert node_id == make_node_id(title)
    # the same title always hashes to the same id, in any database
    assert make_node_id("Covectors") == make_node_id("Covectors")
    assert len(make_node_id("Covectors").rsplit("_", 1)[1]) == 4


def test_aliases_resolve_to_ids(store, graph):
    assert graph_mod.resolve(store, "dual vectors") == graph["Covectors"]
    assert graph_mod.resolve(store, "One-Forms") == graph["Covectors"]
    assert graph_mod.resolve(store, "Covectors") == graph["Covectors"]
    assert graph_mod.resolve(store, graph["Covectors"]) == graph["Covectors"]
    with pytest.raises(LearnerError, match="unknown node"):
        graph_mod.resolve(store, "tensor densities")


def test_import_bumps_graph_version_and_logs_an_event(store, goal):
    from conftest import GRAPH

    result = api.graph_import(store, goal, GRAPH)
    assert result["graph_version"] == 1
    events = store.query("SELECT * FROM events WHERE kind = 'graph_revision'")
    assert len(events) == 1


def test_split_copies_evidence_to_every_child(store, graph):
    node = graph["Wedge product"]
    item = make_item(store, node, stem="wedge stem")
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="in-session",
    )
    before = store.query("SELECT * FROM events WHERE node_id = ? AND kind = 'answer'", (node,))
    assert len(before) == 1

    result = api.graph_revise(
        store,
        "g_forms",
        {
            "ops": [
                {
                    "op": "split",
                    "node": node,
                    "into": [{"title": "Wedge of one-forms"}, {"title": "Wedge in k-forms"}],
                }
            ]
        },
    )
    children = result["ops"][0]["children"]
    assert result["ops"][0]["evidence_events_copied"] == 2
    for child in children:
        rows = store.query(
            "SELECT payload FROM events WHERE node_id = ? AND kind = 'answer'", (child,)
        )
        assert len(rows) == 1
        assert '"migrated": true' in rows[0]["payload"]
    # the parent's own events are untouched: append-only means nothing was rewritten
    after = store.query("SELECT * FROM events WHERE node_id = ? AND kind = 'answer'", (node,))
    assert len(after) == 1
    assert store.one("SELECT retired FROM nodes WHERE node_id = ?", (node,))["retired"] == 1
    # items follow the split
    assert store.one("SELECT node_id FROM items WHERE item_id = ?", (item.item_id,))[
        "node_id"
    ] == children[0]


def test_merge_unions_evidence(store, graph):
    left, right = graph["Wedge product"], graph["k-forms"]
    for node, stem in ((left, "left stem"), (right, "right stem")):
        item = make_item(store, node, stem=stem)
        api.record_answer(
            store,
            evaluation_method="rubric",
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            assistance_level=0,
            context="in-session",
        )
    result = api.graph_revise(
        store,
        "g_forms",
        [{"op": "merge", "nodes": [left, right], "into": {"title": "Wedge and k-forms"}}],
    )
    target = result["ops"][0]["node"]
    assert result["ops"][0]["evidence_events_copied"] == 2
    rows = store.query("SELECT * FROM events WHERE node_id = ? AND kind = 'answer'", (target,))
    assert len(rows) == 2
    # both old titles now resolve to the merged node
    assert graph_mod.resolve(store, "Wedge product") == target
    assert graph_mod.resolve(store, "k-forms") == target


def test_migrated_evidence_reproduces_the_state(store, graph):
    """A split must not silently downgrade what the learner had shown."""

    node = graph["Covectors"]
    item = make_item(store, node)
    for context, days in (("in-session", 9), ("delayed", 2)):
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
    assert evidence_mod.derive(store, node).state == "known"
    result = api.graph_revise(
        store,
        "g_forms",
        [{"op": "split", "node": node, "into": [{"title": "Covector basics"}, {"title": "Covector algebra"}]}],
    )
    for child in result["ops"][0]["children"]:
        state = evidence_mod.derive(store, child)
        assert state.independent_passes == 2
        assert state.last_delayed == "pass"


def test_add_and_remove_ops(store, graph):
    result = api.graph_revise(
        store,
        "g_forms",
        [
            {
                "op": "add",
                "node": {"title": "Pullbacks"},
                "edges": [{"from": "k-forms", "to": "Pullbacks"}],
            }
        ],
    )
    new_node = result["ops"][0]["node"]
    assert graph_mod.resolve(store, "Pullbacks") == new_node
    api.graph_revise(store, "g_forms", [{"op": "remove", "node": "Pullbacks", "reason": "off-syllabus"}])
    assert store.one("SELECT retired FROM nodes WHERE node_id = ?", (new_node,))["retired"] == 1
    assert new_node not in [n.node_id for n in graph_mod.get_nodes(store, "g_forms")]


def test_mermaid_has_a_classdef_per_state(store, graph):
    mermaid = api.graph_show(store, "g_forms", "mermaid")
    for state in ("unknown", "fragile", "known", "misconception"):
        assert f"classDef {state} " in mermaid
    assert "graph TD" in mermaid
    assert "class " in mermaid


def test_edge_and_provenance_types_are_checked(store, goal):
    with pytest.raises(LearnerError, match="unknown edge type"):
        api.graph_import(
            store,
            goal,
            {"nodes": [{"title": "A"}, {"title": "B"}], "edges": [{"from": "A", "to": "B", "type": "bogus"}]},
        )
    with pytest.raises(LearnerError, match="unknown provenance"):
        api.graph_import(
            store,
            goal,
            {
                "nodes": [{"title": "C"}, {"title": "D"}],
                "edges": [{"from": "C", "to": "D", "type": "supports", "provenance": "vibes"}],
            },
        )


def test_topological_order_follows_prerequisites(store, graph):
    nodes = graph_mod.get_nodes(store, "g_forms")
    edges = graph_mod.get_edges(store, [n.node_id for n in nodes])
    order = graph_mod.topological_order(nodes, edges)
    assert order.index(graph["Vectors"]) < order.index(graph["Covectors"])
    assert order.index(graph["Covectors"]) < order.index(graph["Wedge product"])
    assert order.index(graph["k-forms"]) < order.index(graph["Exterior derivative"])
