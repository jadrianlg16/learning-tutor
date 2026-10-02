"""The rule-based evidence model, and the assistance ceiling."""

from __future__ import annotations

import pytest
from conftest import ago, make_item

from learning_tutor.learner import api, fsrs_sched
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner.store import LearnerError


def answer(store, item, *, correct=True, assistance=0, context="in-session", days=0, confidence=None):
    return api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a covector" if correct else "a vector",
        correct=correct,
        confidence=confidence,
        assistance_level=assistance,
        context=context,
        ts=ago(days=days),
    )


def test_no_evidence_means_unknown(store, graph):
    state = evidence_mod.derive(store, graph["Covectors"])
    assert state.state == "unknown"
    assert state.uncertainty == "high"
    assert state.reasons == ["no evidence recorded"]


def test_assistance_five_never_counts_as_a_pass(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    for days in (10, 8, 6, 4):
        result = answer(store, item, assistance=5, days=days)
        assert result["counts_toward_mastery"] is False
    state = evidence_mod.derive(store, node)
    assert state.independent_passes == 0
    assert state.assisted_passes == 0
    assert state.unearned_passes == 4
    assert state.state == "unknown", "a wall of partial solutions is not knowledge"


def test_assistance_five_is_scheduled_as_a_lapse(store):
    assert fsrs_sched.rating_for(correct=True, assistance_level=5).name == "Again"
    assert fsrs_sched.rating_for(correct=True, assistance_level=6).name == "Again"
    assert fsrs_sched.rating_for(correct=True, assistance_level=4).name == "Hard"
    assert fsrs_sched.rating_for(correct=True, assistance_level=2).name == "Hard"
    assert fsrs_sched.rating_for(correct=True, assistance_level=1).name == "Good"
    assert fsrs_sched.rating_for(correct=True, assistance_level=1, confidence=2).name == "Hard"
    assert fsrs_sched.rating_for(correct=True, assistance_level=0).name == "Good"
    assert fsrs_sched.rating_for(correct=True, assistance_level=0, confidence=5).name == "Easy"
    assert fsrs_sched.rating_for(correct=False, assistance_level=0, confidence=5).name == "Again"
    assert fsrs_sched.rating_for(correct=True, assistance_level=0, idk=True).name == "Again"


def test_hinted_passes_are_assisted_not_independent(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    answer(store, item, assistance=3, days=5)
    answer(store, item, assistance=2, days=4)
    state = evidence_mod.derive(store, node)
    assert state.assisted_passes == 2
    assert state.independent_passes == 0
    assert state.state == "fragile"


def test_known_needs_delayed_or_transfer_evidence(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    answer(store, item, days=6)
    answer(store, item, days=5)
    in_session_only = evidence_mod.derive(store, node)
    assert in_session_only.state == "fragile"
    assert any("delayed or transfer" in r for r in in_session_only.reasons)

    answer(store, item, context="delayed", days=1)
    assert evidence_mod.derive(store, node).state == "known"


def test_a_failed_delayed_retrieval_breaks_known(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    answer(store, item, days=9)
    answer(store, item, days=8)
    answer(store, item, context="delayed", days=6)
    assert evidence_mod.derive(store, node).state == "known"
    answer(store, item, correct=False, context="delayed", days=1)
    state = evidence_mod.derive(store, node)
    assert state.state == "fragile"
    assert state.last_delayed == "fail"


def test_only_fails_stays_unknown(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    answer(store, item, correct=False, days=3)
    answer(store, item, correct=False, days=2)
    state = evidence_mod.derive(store, node)
    assert state.state == "unknown"
    assert state.fails == 2


def test_transfer_pass_counts_as_delayed_style_evidence(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    answer(store, item, days=4)
    answer(store, item, context="transfer", days=2)
    state = evidence_mod.derive(store, node)
    assert state.transfer_passes == 1
    assert state.state == "known"


def test_teach_back_is_evidence_without_an_item(store, graph):
    node = graph["Covectors"]
    api.record_teach_back(
        store, session_id=None, node=node, score=3, rubric_version="rubric-v1", ts=ago(days=3)
    )
    api.record_teach_back(
        store, session_id=None, node=node, score=2, rubric_version="rubric-v1", ts=ago(days=2)
    )
    result = api.record_teach_back(
        store,
        session_id=None,
        node=node,
        score=3,
        rubric_version="rubric-v1",
        context="delayed",
        ts=ago(days=1),
    )
    assert result["passed"] is True
    state = evidence_mod.derive(store, node)
    assert state.independent_passes == 3
    assert state.state == "known"


def test_low_teach_back_score_is_a_fail(store, graph):
    node = graph["Covectors"]
    result = api.record_teach_back(
        store, session_id=None, node=node, score=1, rubric_version="rubric-v1"
    )
    assert result["passed"] is False
    assert evidence_mod.derive(store, node).fails == 1


def test_uncertainty_labels_move_with_the_evidence(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    assert evidence_mod.derive(store, node).uncertainty == "high"
    answer(store, item, days=9)
    assert evidence_mod.derive(store, node).uncertainty == "high"
    answer(store, item, days=8)
    assert evidence_mod.derive(store, node).uncertainty == "medium"
    answer(store, item, days=7)
    answer(store, item, context="delayed", days=6)
    assert evidence_mod.derive(store, node).uncertainty == "low"


def test_bkt_is_a_stub_behind_a_flag(store, graph, monkeypatch):
    monkeypatch.setenv("LT_EVIDENCE_MODEL", "bkt")
    from learning_tutor.config import get_settings
    from learning_tutor.learner.store import Store

    bkt_store = Store(get_settings())
    with pytest.raises(NotImplementedError, match="hidden-item prediction"):
        evidence_mod.derive(bkt_store, graph["Covectors"])
    bkt_store.close()


def test_unknown_evidence_model_is_rejected(store, graph, monkeypatch):
    monkeypatch.setenv("LT_EVIDENCE_MODEL", "astrology")
    from learning_tutor.config import get_settings
    from learning_tutor.learner.store import Store

    other = Store(get_settings())
    with pytest.raises(LearnerError, match="unknown LT_EVIDENCE_MODEL"):
        evidence_mod.derive(other, graph["Covectors"])
    other.close()
