"""Disputes are transparent and evidence-settled. None of them grants mastery."""

from __future__ import annotations

import pytest
from conftest import force_holdout, make_item

from learning_tutor.learner import disputes as disputes_mod
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner.store import LearnerError


def test_the_six_types_are_the_only_ones(store, graph):
    for dispute_type in disputes_mod.TYPES:
        row = disputes_mod.open_dispute(
            store, dispute_type=dispute_type, node_id=graph["Covectors"], note="n"
        )
        assert row["status"] == "open"
    assert len(disputes_mod.TYPES) == 6
    with pytest.raises(LearnerError, match="unknown dispute type"):
        disputes_mod.open_dispute(store, dispute_type="the computer is wrong")


def test_i_already_know_this_schedules_a_two_item_check_and_grants_nothing(store, graph):
    node = graph["Covectors"]
    for i in range(3):
        make_item(store, node, stem=f"stem {i}")
    before = evidence_mod.derive(store, node).state
    row = disputes_mod.open_dispute(
        store, dispute_type="I already know this", node_id=node, note="I did this at uni"
    )
    assert len(row["check_items"]) == 2
    assert row["mastery_granted"] is False
    assert "not evidence" in row["next_action"]
    assert evidence_mod.derive(store, node).state == before == "unknown"


def test_the_check_warns_when_there_are_not_enough_validated_items(store, graph):
    row = disputes_mod.open_dispute(
        store, dispute_type="I already know this", node_id=graph["Covectors"]
    )
    assert row["check_items"] == []
    assert "author and validate" in row["warning"]


def test_the_check_never_offers_a_holdout(store, graph):
    node = graph["Covectors"]
    made = [make_item(store, node, stem=f"stem {i}", uses=3, promote=True) for i in range(6)]
    for item in made[:2]:
        force_holdout(store, item.item_id)
    holdouts = {i.item_id for i in made[:2]} | {i.item_id for i in made if i.holdout}
    row = disputes_mod.open_dispute(store, dispute_type="test me instead", node_id=node)
    assert not (set(row["check_items"]) & holdouts)


def test_settling_a_dispute_does_not_touch_node_state(store, graph):
    node = graph["Covectors"]
    make_item(store, node)
    row = disputes_mod.open_dispute(store, dispute_type="I already know this", node_id=node)
    settled = disputes_mod.settle(
        store, row["dispute_id"], outcome="upheld", evidence="passed both check items"
    )
    assert settled["outcome"] == "upheld"
    assert settled["mastery_granted"] is False
    assert evidence_mod.derive(store, node).state == "unknown"


def test_a_dispute_cannot_be_settled_twice(store, graph):
    row = disputes_mod.open_dispute(store, dispute_type="misclick", node_id=graph["Covectors"])
    disputes_mod.settle(store, row["dispute_id"], outcome="upheld")
    with pytest.raises(LearnerError, match="already settled"):
        disputes_mod.settle(store, row["dispute_id"], outcome="rejected")


def test_disputes_are_logged_as_events(store, graph):
    row = disputes_mod.open_dispute(store, dispute_type="not on my exam", node_id=graph["k-forms"])
    disputes_mod.settle(store, row["dispute_id"], outcome="upheld", evidence="syllabus p2")
    events = store.query("SELECT * FROM events WHERE kind = 'dispute' ORDER BY ts")
    assert len(events) == 2


def test_dispute_stats(store, graph):
    a = disputes_mod.open_dispute(store, dispute_type="misclick", node_id=graph["Covectors"])
    b = disputes_mod.open_dispute(store, dispute_type="ambiguous question", node_id=graph["Covectors"])
    disputes_mod.settle(store, a["dispute_id"], outcome="upheld")
    disputes_mod.settle(store, b["dispute_id"], outcome="rejected")
    stats = disputes_mod.stats(store)
    assert stats["disputes"] == 2
    assert stats["settled"] == 2
    assert stats["learner_was_right_percent"] == 50
