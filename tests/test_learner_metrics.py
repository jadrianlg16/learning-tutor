"""Holdouts and the three Stage 0 metrics, on a scripted history."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import ago, force_holdout, make_item

from learning_tutor.learner import api
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner import holdouts as holdouts_mod
from learning_tutor.learner import items as items_mod
from learning_tutor.learner import metrics as metrics_mod


def known_node(store, node, stem="known stem"):
    item = make_item(store, node, stem=stem)
    for days, context in ((20, "in-session"), (19, "in-session"), (18, "delayed")):
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
    return item


def test_holdouts_become_due_after_the_delay(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    holdout = make_item(store, node, stem="holdout stem")
    force_holdout(store, holdout.item_id)

    assert holdouts_mod.due(store, "g_forms", now=datetime.now(UTC) - timedelta(days=29)) == []
    due = holdouts_mod.due(store, "g_forms")
    assert [row["item_id"] for row in due] == [holdout.item_id]
    assert due[0]["context"] == "delayed"
    assert due[0]["never_checked"] is True


def test_a_holdout_on_an_unknown_node_is_not_due(store, graph):
    holdout = make_item(store, graph["k-forms"], stem="unknown node holdout")
    force_holdout(store, holdout.item_id)
    assert holdouts_mod.due(store, "g_forms") == []


def test_transfer_holdouts_are_served_as_transfer(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    holdout = make_item(store, node, stem="different surface", surface_form="word problem")
    force_holdout(store, holdout.item_id)
    assert holdouts_mod.due(store, "g_forms")[0]["context"] == "transfer"


def test_holdout_check_marks_the_item_as_checked(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    holdout = make_item(store, node, stem="holdout stem")
    force_holdout(store, holdout.item_id)
    picks = api.holdout_check(store, "g_forms")
    assert picks["due"] == 1
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=holdout.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="delayed",
    )
    assert store.one("SELECT last_checked_at FROM holdouts WHERE item_id = ?", (holdout.item_id,))[
        "last_checked_at"
    ]
    assert api.holdout_check(store, "g_forms")["due"] == 0


def test_seven_day_holdout_success_rate(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    passed = make_item(store, node, stem="holdout a")
    failed = make_item(store, node, stem="holdout b")
    for item in (passed, failed):
        force_holdout(store, item.item_id)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=passed.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="delayed",
        ts=ago(days=2),
    )
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=failed.item_id,
        response="a vector",
        correct=False,
        assistance_level=0,
        context="delayed",
        ts=ago(days=3),
    )
    # outside the window: must not count
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=passed.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="delayed",
        ts=ago(days=20),
    )
    result = metrics_mod.compute(store, "g_forms").holdout_success_7d
    assert result["window_days"] == 7
    assert result["checks"] == 2
    assert result["passes"] == 1
    assert result["rate_percent"] == 50


def test_a_pass_at_high_assistance_is_not_a_holdout_pass(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    holdout = make_item(store, node, stem="holdout c")
    force_holdout(store, holdout.item_id)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=holdout.item_id,
        response="a covector",
        correct=True,
        assistance_level=5,
        context="delayed",
        ts=ago(days=1),
    )
    assert metrics_mod.compute(store, "g_forms").holdout_success_7d["passes"] == 0


def test_false_mastery_uses_the_state_at_the_time(store, graph):
    node = graph["Covectors"]
    known_node(store, node)
    holdout = make_item(store, node, stem="holdout d")
    force_holdout(store, holdout.item_id)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=holdout.item_id,
        response="a vector",
        correct=False,
        assistance_level=0,
        context="delayed",
        ts=ago(days=1),
    )
    result = metrics_mod.compute(store, "g_forms").false_mastery
    assert result["nodes_with_holdout_checks"] == 1
    assert result["nodes_known_then_failed"] == 1
    assert result["rate_percent"] == 100
    assert result["nodes"] == [node]
    # and the failure has already downgraded the node, so the state is honest now
    assert evidence_mod.derive(store, node).state == "fragile"


def test_a_failed_holdout_on_a_fragile_node_is_not_false_mastery(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node, stem="one pass only")
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="in-session",
        ts=ago(days=10),
    )
    assert evidence_mod.derive(store, node).state == "fragile"
    holdout = make_item(store, node, stem="holdout e")
    force_holdout(store, holdout.item_id)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=holdout.item_id,
        response="a vector",
        correct=False,
        assistance_level=0,
        context="delayed",
        ts=ago(days=1),
    )
    result = metrics_mod.compute(store, "g_forms").false_mastery
    assert result["nodes_with_holdout_checks"] == 1
    assert result["nodes_known_then_failed"] == 0
    assert result["rate_percent"] == 0


def test_item_rejection_rate(store, graph):
    node = graph["Covectors"]
    good = items_mod.add(store, node, {"stem": "a", "options": ["x", "y"], "answer": "x"}, author="a")
    bad = items_mod.add(store, node, {"stem": "b", "options": ["x", "y"], "answer": "x"}, author="a")
    items_mod.validate(store, good.item_id, by="solver", result="pass")
    items_mod.validate(store, bad.item_id, by="solver", result="fail", notes="ambiguous")
    result = metrics_mod.compute(store, "g_forms").item_rejection
    assert result["validations"] == 2
    assert result["rejected"] == 1
    assert result["rate_percent"] == 50


def test_rates_over_an_empty_denominator_are_null(store, graph):
    data = metrics_mod.compute(store, "g_forms")
    assert data.holdout_success_7d["rate_percent"] is None
    assert data.false_mastery["rate_percent"] is None
    assert data.item_rejection["rate_percent"] is None
