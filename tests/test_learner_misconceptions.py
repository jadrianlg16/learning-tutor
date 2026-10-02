"""A misconception is a hypothesis until all three confirmation steps hold."""

from __future__ import annotations

import pytest
from conftest import ago, make_item

from learning_tutor.learner import api
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner import misconceptions as misc_mod
from learning_tutor.learner.store import LearnerError

CLAIM = "the wedge product behaves like ordinary multiplication"


def test_suspicion_alone_is_not_durable_state(store, graph):
    node = graph["Wedge product"]
    row = misc_mod.suspect(store, node, CLAIM)
    assert row["state"] == "suspected"
    assert misc_mod.active_for_node(store, node) is None
    assert evidence_mod.derive(store, node).state != "misconception"


def test_all_three_steps_are_required_and_ordered(store, graph):
    node = graph["Wedge product"]
    misc_mod.suspect(store, node, CLAIM)

    with pytest.raises(LearnerError, match="expected 'reasoning' next"):
        misc_mod.confirm_step(store, node, CLAIM, step="counterexample", outcome="held")

    after_one = misc_mod.confirm_step(store, node, CLAIM, step="reasoning", outcome="held")
    assert after_one["state"] == "suspected"
    after_two = misc_mod.confirm_step(store, node, CLAIM, step="prediction", outcome="held")
    assert after_two["state"] == "suspected"
    assert misc_mod.active_for_node(store, node) is None

    after_three = misc_mod.confirm_step(
        store, node, CLAIM, step="counterexample", outcome="held"
    )
    assert after_three["state"] == "active"
    assert misc_mod.active_for_node(store, node)["claim"] == CLAIM
    assert evidence_mod.derive(store, node).state == "misconception"


def test_a_dropped_step_ends_the_hypothesis(store, graph):
    node = graph["Wedge product"]
    misc_mod.suspect(store, node, CLAIM)
    misc_mod.confirm_step(store, node, CLAIM, step="reasoning", outcome="held")
    dropped = misc_mod.confirm_step(store, node, CLAIM, step="prediction", outcome="dropped")
    assert dropped["state"] == "resolved"
    assert dropped["resolution_reason"] == "dropped at prediction"
    assert misc_mod.active_for_node(store, node) is None


def test_confirm_step_needs_a_suspicion_first(store, graph):
    with pytest.raises(LearnerError, match="no suspected misconception"):
        misc_mod.confirm_step(
            store, graph["Wedge product"], CLAIM, step="reasoning", outcome="held"
        )


def confirm(store, node, claim=CLAIM):
    misc_mod.suspect(store, node, claim)
    for step in ("reasoning", "prediction", "counterexample"):
        misc_mod.confirm_step(store, node, claim, step=step, outcome="held")


def test_high_confidence_wrong_answer_only_suspects(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    result = api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a vector",
        correct=False,
        confidence=5,
        assistance_level=0,
        context="in-session",
    )
    claims = [m["claim"] for m in result["misconceptions"]]
    assert claims == ["vectors and covectors are the same thing"]
    assert result["misconceptions"][0]["state"] == "suspected"
    assert evidence_mod.derive(store, node).state != "misconception"


def test_low_confidence_wrong_answer_suspects_nothing(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    result = api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a vector",
        correct=False,
        confidence=2,
        assistance_level=0,
        context="in-session",
    )
    assert result["misconceptions"] == []


def test_an_independent_pass_weakens_an_active_misconception(store, graph):
    node = graph["Covectors"]
    confirm(store, node, "vectors and covectors are the same thing")
    assert evidence_mod.derive(store, node).state == "misconception"
    item = make_item(store, node)
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
    row = misc_mod.for_node(store, node)[0]
    assert row["state"] == "weakened"
    assert evidence_mod.derive(store, node).state != "misconception"


def test_resolved_then_failed_becomes_recurred(store, graph):
    node = graph["Covectors"]
    claim = "vectors and covectors are the same thing"
    confirm(store, node, claim)
    misc_mod.resolve(store, node, claim, reason="clinic run")
    assert misc_mod.for_node(store, node)[0]["state"] == "resolved"

    item = make_item(store, node)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a vector",
        correct=False,
        assistance_level=0,
        context="in-session",
    )
    assert misc_mod.for_node(store, node)[0]["state"] == "recurred"
    assert evidence_mod.derive(store, node).state == "misconception"


def test_historical_state_reconstruction_uses_events(store, graph):
    """`derive(before=...)` must not see a misconception confirmed later."""

    node = graph["Covectors"]
    item = make_item(store, node)
    for days in (9, 8):
        api.record_answer(
            store,
            evaluation_method="rubric",
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            assistance_level=0,
            context="in-session",
            ts=ago(days=days),
        )
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="delayed",
        ts=ago(days=7),
    )
    cutoff = ago(days=6)
    confirm(store, node, "vectors and covectors are the same thing")
    assert evidence_mod.derive(store, node).state == "misconception"
    assert evidence_mod.derive(store, node, before=cutoff).state == "known"
