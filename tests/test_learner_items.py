"""The item lifecycle rules, versioning, and holdout assignment."""

from __future__ import annotations

import pytest
from conftest import item_spec, make_item

from learning_tutor.learner import api
from learning_tutor.learner import items as items_mod
from learning_tutor.learner.ids import hash_fraction
from learning_tutor.learner.store import LearnerError


def test_new_items_start_teaching_only(store, graph):
    item = items_mod.add(store, graph["Covectors"], item_spec(), author="author-model")
    assert item.status == "TEACHING_ONLY"
    assert item.version.version == 1


def test_author_cannot_validate_own_item(store, graph):
    item = items_mod.add(store, graph["Covectors"], item_spec(), author="claude")
    with pytest.raises(LearnerError, match="validator must differ from the author"):
        items_mod.validate(store, item.item_id, by="claude", result="pass")
    with pytest.raises(LearnerError, match="validator must differ"):
        items_mod.validate(store, item.item_id, by="  CLAUDE ", result="pass")
    assert items_mod.get(store, item.item_id).status == "TEACHING_ONLY"


def test_passing_validation_promotes_to_practice_evidence(store, graph):
    item = items_mod.add(store, graph["Covectors"], item_spec(), author="claude")
    updated = items_mod.validate(store, item.item_id, by="solver-model", result="pass")
    assert updated.status == "PRACTICE_EVIDENCE"


def test_failing_validation_keeps_it_teaching_only(store, graph):
    item = items_mod.add(store, graph["Covectors"], item_spec(), author="claude")
    updated = items_mod.validate(
        store, item.item_id, by="solver-model", result="fail", notes="two options are correct"
    )
    assert updated.status == "TEACHING_ONLY"
    assert items_mod.rejection_rate(store)["rejected"] == 1


def test_editing_a_stem_creates_a_new_version_and_revalidation(store, graph):
    item = make_item(store, graph["Covectors"], stem="original stem")
    assert item.status == "PRACTICE_EVIDENCE"
    updated = items_mod.add(
        store,
        graph["Covectors"],
        item_spec("reworded stem"),
        author="author-model",
        item=item.item_id,
    )
    assert updated.version.version == 2
    assert updated.current_version_id != item.current_version_id
    assert updated.status == "TEACHING_ONLY", "a reworded stem is a new question"
    versions = store.query(
        "SELECT * FROM item_versions WHERE item_id = ? ORDER BY version", (item.item_id,)
    )
    assert [v["stem"] for v in versions] == ["original stem", "reworded stem"]


def test_promote_needs_validation_and_enough_uses(store, graph):
    item = items_mod.add(store, graph["Covectors"], item_spec(), author="claude")
    with pytest.raises(LearnerError, match="PRACTICE_EVIDENCE"):
        items_mod.promote(store, item.item_id)
    items_mod.validate(store, item.item_id, by="solver-model", result="pass")
    with pytest.raises(LearnerError, match="recorded uses"):
        items_mod.promote(store, item.item_id)
    for _ in range(store.settings.promote_min_uses):
        api.record_answer(
            store,
            session_id=None,
            item_id=item.item_id,
            response="a covector",
            correct=True,
            assistance_level=0,
            context="in-session",
        )
    promoted = items_mod.promote(store, item.item_id)
    assert promoted.status == "MASTERY_ELIGIBLE"


def test_promote_refuses_an_item_nobody_ever_got_right(store, graph):
    item = make_item(store, graph["Covectors"])
    for _ in range(3):
        api.record_answer(
            store,
            session_id=None,
            item_id=item.item_id,
            response="a vector",
            correct=False,
            assistance_level=0,
            context="in-session",
        )
    with pytest.raises(LearnerError, match="never been answered correctly"):
        items_mod.promote(store, item.item_id)


def test_promote_refuses_while_an_ambiguity_dispute_is_open(store, graph):
    from learning_tutor.learner import disputes as disputes_mod

    item = make_item(store, graph["Covectors"], uses=3)
    disputes_mod.open_dispute(
        store,
        dispute_type="ambiguous question",
        node_id=graph["Covectors"],
        item_id=item.item_id,
        note="two options defensible",
    )
    with pytest.raises(LearnerError, match="ambiguity dispute"):
        items_mod.promote(store, item.item_id)


def test_holdout_assignment_is_deterministic_and_roughly_the_configured_fraction(store, graph):
    """The flag is a pure function of the item id, so it never flips."""

    ids = [f"i_{i:05d}" for i in range(2000)]
    fraction = store.settings.holdout_fraction
    holdouts = [i for i in ids if hash_fraction(i) < fraction]
    assert 0.15 < len(holdouts) / len(ids) < 0.25
    assert [i for i in ids if hash_fraction(i) < fraction] == holdouts


def test_promotion_records_holdout_membership(store, graph):
    made = [make_item(store, graph["Covectors"], stem=f"stem {i}", uses=3, promote=True) for i in range(12)]
    holdouts = [i for i in made if i.holdout]
    for item in made:
        row = store.one("SELECT 1 FROM holdouts WHERE item_id = ?", (item.item_id,))
        assert bool(row) == item.holdout
        assert item.holdout == (hash_fraction(item.item_id) < store.settings.holdout_fraction)
    assert 0 <= len(holdouts) <= len(made)


def test_only_evidence_items_write_evidence(store, graph):
    from learning_tutor.learner import evidence as evidence_mod

    item = items_mod.add(store, graph["Covectors"], item_spec(), author="claude")
    result = api.record_answer(
        store,
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="in-session",
    )
    assert result["wrote_evidence"] is False
    assert result["schedule"] is None
    state = evidence_mod.derive(store, graph["Covectors"])
    assert state.state == "unknown"
    assert state.independent_passes == 0


def test_mc_items_are_shape_checked(store, graph):
    with pytest.raises(LearnerError, match="at least two options"):
        items_mod.add(store, graph["Covectors"], item_spec(options=["only one"], answer="only one"))
    with pytest.raises(LearnerError, match="answer must be one of the options"):
        items_mod.add(store, graph["Covectors"], item_spec(answer="not there"))
