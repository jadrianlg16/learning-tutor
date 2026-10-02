"""`learner next` in each mode, the probe budget, and the holdout wall."""

from __future__ import annotations

from conftest import ago, force_holdout, make_item

from learning_tutor.learner import api, selection
from learning_tutor.learner import evidence as evidence_mod
from learning_tutor.learner import misconceptions as misc_mod


def pass_node(store, node, *, delayed=True, stem_prefix="s"):
    """Drive a node to `known` with two independent passes and a delayed pass."""

    item = make_item(store, node, stem=f"{stem_prefix} {node}")
    for days, context in ((9, "in-session"), (8, "in-session"), (2, "delayed" if delayed else "in-session")):
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


def test_probe_picks_the_node_that_splits_the_chain(store, graph):
    """On a 5-node chain with nothing known, the middle node halves the space."""

    result = api.next_(store, "g_forms", mode="probe")
    assert result["mode"] == "probe"
    assert result["picks"][0]["node_id"] == graph["Wedge product"]
    assert result["picks"][0]["context"] == "probe"
    assert result["probe_budget"] == 12


def test_probe_moves_on_once_part_of_the_chain_is_known(store, graph):
    for node in ("Vectors", "Covectors", "Wedge product"):
        pass_node(store, graph[node], stem_prefix=node)
    result = api.next_(store, "g_forms", mode="probe")
    assert result["picks"][0]["node_id"] in (graph["k-forms"], graph["Exterior derivative"])


def test_probe_budget_is_per_session_and_enforced(store, graph, monkeypatch):
    session = api.session_start(store, goal_id="g_forms")["session_id"]
    item = make_item(store, graph["Covectors"])
    for _ in range(store.settings.probe_budget):
        api.record_answer(
            store,
            evaluation_method="rubric",
            session_id=session,
            item_id=item.item_id,
            response="a covector",
            correct=False,
            assistance_level=0,
            context="probe",
        )
    result = api.next_(store, "g_forms", mode="probe", session_id=session)
    assert result["picks"] == []
    assert result["probe_budget_remaining"] == 0
    assert "budget spent" in result["note"]


def test_probe_budget_is_configurable(store, graph, monkeypatch):
    monkeypatch.setenv("LT_PROBE_BUDGET", "3")
    from learning_tutor.config import get_settings
    from learning_tutor.learner.store import Store

    other = Store(get_settings())
    assert api.next_(other, "g_forms", mode="probe")["probe_budget"] == 3
    other.close()


def test_teach_picks_the_edge(store, graph):
    pass_node(store, graph["Vectors"], stem_prefix="v")
    result = api.next_(store, "g_forms", mode="teach")
    assert result["picks"][0]["node_id"] == graph["Covectors"]
    assert "prerequisites are known" in result["picks"][0]["reason"]

    pass_node(store, graph["Covectors"], stem_prefix="c")
    assert api.next_(store, "g_forms", mode="teach")["picks"][0]["node_id"] == graph["Wedge product"]


def test_teach_starts_at_the_root_when_nothing_is_known(store, graph):
    result = api.next_(store, "g_forms", mode="teach")
    assert result["picks"][0]["node_id"] == graph["Vectors"]


def test_review_serves_due_items_first(store, graph):
    item = make_item(store, graph["Covectors"])
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a vector",
        correct=False,
        assistance_level=0,
        context="in-session",
        ts=ago(days=30),
    )
    result = api.next_(store, "g_forms", mode="review")
    assert result["picks"][0]["item_id"] == item.item_id
    assert "FSRS due" in result["picks"][0]["reason"]


def test_review_falls_back_to_fragile_then_misconception(store, graph):
    node = graph["Covectors"]
    item = make_item(store, node)
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=None,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=3,
        context="in-session",
        ts=ago(days=0),
    )
    assert evidence_mod.derive(store, node).state == "fragile"
    picks = api.next_(store, "g_forms", mode="review")["picks"]
    assert picks and picks[0]["node_id"] == node
    assert "fragile" in picks[0]["reason"]

    claim = "vectors and covectors are the same thing"
    misc_mod.suspect(store, node, claim)
    for step in ("reasoning", "prediction", "counterexample"):
        misc_mod.confirm_step(store, node, claim, step=step, outcome="held")
    picks = api.next_(store, "g_forms", mode="review")["picks"]
    assert "misconception check" in picks[0]["reason"]
    assert picks[0]["item_id"] == item.item_id  # the item whose distractor is that claim


def test_review_offers_a_transfer_variant_for_a_known_node(store, graph):
    node = graph["Covectors"]
    pass_node(store, node, stem_prefix="base")
    make_item(store, node, stem="same idea, new surface", surface_form="word problem")
    picks = api.next_(store, "g_forms", mode="review")["picks"]
    transfer = [p for p in picks if p["context"] == "transfer"]
    assert transfer, picks
    assert "surface form" in transfer[0]["reason"]


def test_next_never_serves_a_holdout(store, graph):
    node = graph["Covectors"]
    made = [make_item(store, node, stem=f"stem {i}", uses=3, promote=True) for i in range(6)]
    for item in made[:2]:
        force_holdout(store, item.item_id)
    holdouts = {i.item_id for i in made[:2]} | {i.item_id for i in made if i.holdout}
    assert holdouts
    served = set()
    for mode in ("probe", "review", "teach", "auto"):
        for pick in api.next_(store, "g_forms", mode=mode, n=10)["picks"]:
            if pick["item_id"]:
                served.add(pick["item_id"])
    assert not (served & holdouts)


def test_auto_mode_prefers_review_then_probe_then_teach(store, graph):
    assert api.next_(store, "g_forms", mode="auto")["mode"] == "probe"
    for node in graph.values():
        pass_node(store, node, stem_prefix=node)
    # everything known and nothing due yet -> teach (which then reports nothing left)
    result = api.next_(store, "g_forms", mode="auto")
    assert result["mode"] in ("review", "teach")


def test_split_scores_are_deterministic(store, graph):
    first = api.next_(store, "g_forms", mode="probe", n=3)["picks"]
    second = api.next_(store, "g_forms", mode="probe", n=3)["picks"]
    assert [p["node_id"] for p in first] == [p["node_id"] for p in second]


def test_picks_flag_missing_items(store, graph):
    pick = api.next_(store, "g_forms", mode="teach")["picks"][0]
    assert pick["item_id"] is None
    assert "author and validate" in pick["reason"]


def test_probe_budget_used_counts_only_probe_context(store, graph):
    session = api.session_start(store, goal_id="g_forms")["session_id"]
    item = make_item(store, graph["Covectors"])
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=session,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="in-session",
    )
    assert selection.probe_budget_used(store, session) == 0
    api.record_answer(
        store,
        evaluation_method="rubric",
        session_id=session,
        item_id=item.item_id,
        response="a covector",
        correct=True,
        assistance_level=0,
        context="probe",
    )
    assert selection.probe_budget_used(store, session) == 1
