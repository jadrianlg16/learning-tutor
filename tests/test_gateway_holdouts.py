"""Holdouts reachable from the product: the due list, the answer, and the auto-promote rule.

Same harness as ``test_gateway_routes.py``: a real learner-svc mounted in-process, a fake
model. The point of every test here is a non-zero denominator — before these routes the
gateway never promoted an item and never served a holdout, so ``learner/metrics.py`` could
only ever report ``null``.
"""

from __future__ import annotations

import pytest
from conftest import force_holdout
from test_gateway_routes import (  # noqa: F401 - the autouse fixtures apply by import
    GOAL,
    FakeLLM,
    _gateway_env,
    _no_real_ollama,
    build_client,
    create_goal,
    read_state,
    teach_one_step,
)

from learning_tutor.learner.store import open_store
from learning_tutor.tutor import generate as generate_mod
from learning_tutor.tutor import orchestrator as orch


@pytest.fixture
def llm(monkeypatch) -> FakeLLM:
    fake = FakeLLM()
    monkeypatch.setattr(generate_mod, "generate_structured", fake)
    return fake


@pytest.fixture
def client(settings, llm):
    with build_client(settings) as test_client:
        yield test_client

# ------------------------------------------------------------------ the rule, pure


def test_promotion_is_due_at_the_threshold_and_not_before():
    assert orch.promotion_due(uses=2, status="PRACTICE_EVIDENCE", min_uses=3).due is False
    assert orch.promotion_due(uses=3, status="PRACTICE_EVIDENCE", min_uses=3).due is True
    assert orch.promotion_due(uses=9, status="TEACHING_ONLY", min_uses=3).due is False
    assert orch.promotion_due(uses=9, status="MASTERY_ELIGIBLE", min_uses=3).due is False


def test_promotion_threshold_reads_the_same_variable_as_learner_svc(monkeypatch):
    monkeypatch.setenv("LT_PROMOTE_MIN_USES", "5")
    ruling = orch.promotion_due(uses=4, status="PRACTICE_EVIDENCE")
    assert ruling.due is False
    assert ruling.min_uses == 5


def test_count_uses_mirrors_the_learner_core_rule():
    rows = [
        {"kind": "answer", "item_version_id": "v1", "payload": {"item_id": "i1"}},
        {"kind": "probe_answer", "item_version_id": "v1", "payload": {"item_id": "i1"}},
        {"kind": "teach_back", "item_version_id": None, "payload": {"item_id": "i1"}},
        {"kind": "answer", "item_version_id": "v9", "payload": {"item_id": "i2"}},
    ]
    assert orch.count_uses(rows, item_id="i1", item_version_id="v1") == 2
    assert orch.count_uses(rows, item_id="i2") == 1


# ------------------------------------------------------------ the rule, in the flow


def _answer(client, session_id: str, item_id: str, response: str = "B") -> dict:
    reply = client.post(
        f"/api/goals/{GOAL}/teach/answer",
        json={
            "session_id": session_id,
            "item_id": item_id,
            "response": response,
            "confidence": 4,
            "assistance_level": 0,
        },
    )
    assert reply.status_code == 200, reply.text
    return reply.json()


def _item_row(settings, item_id: str) -> dict:
    with open_store(settings) as store:
        return dict(store.one("SELECT * FROM items WHERE item_id = ?", (item_id,)))


def test_teach_answer_promotes_at_the_threshold_not_before(client, settings):
    session_id, taught = teach_one_step(client)
    item_id = taught["checkpoint"]["item_id"]
    assert _item_row(settings, item_id)["status"] == "PRACTICE_EVIDENCE"
    assert settings.promote_min_uses == 3

    first = _answer(client, session_id, item_id)
    assert first["promotion"]["attempted"] is False
    assert first["promotion"]["uses"] == 1
    assert _item_row(settings, item_id)["status"] == "PRACTICE_EVIDENCE"

    second = _answer(client, session_id, item_id)
    assert second["promotion"]["attempted"] is False
    assert second["promotion"]["uses"] == 2
    assert _item_row(settings, item_id)["status"] == "PRACTICE_EVIDENCE"

    third = _answer(client, session_id, item_id)
    assert third["promotion"]["attempted"] is True
    assert third["promotion"]["promoted"] is True
    assert third["promotion"]["uses"] == 3
    assert third["promotion"]["min_uses"] == 3
    assert third["promotion"]["status"] == "MASTERY_ELIGIBLE"
    row = _item_row(settings, item_id)
    assert row["status"] == "MASTERY_ELIGIBLE"
    # learner-svc made the holdout draw, and the gateway reports rather than decides it
    assert third["promotion"]["holdout"] == bool(row["holdout"])
    # the stored key survives the promotion, so a later holdout check can be graded
    assert read_state(settings)["questions"][item_id]["status"] == "MASTERY_ELIGIBLE"


def test_a_promote_refusal_is_reported_not_raised(client, settings):
    """A retry after promotion, and an item learner-svc will not promote, both come back
    200 with the reason in ``promotion`` — the checkpoint was recorded either way."""

    session_id, taught = teach_one_step(client)
    item_id = taught["checkpoint"]["item_id"]
    for _ in range(3):
        _answer(client, session_id, item_id)
    fourth = _answer(client, session_id, item_id)
    assert fourth["promotion"]["attempted"] is False
    assert fourth["promotion"]["reason"] == "already MASTERY_ELIGIBLE"
    assert fourth["promotion"]["uses"] == 4


# ------------------------------------------------------------------- the two routes


def _probe_to_first_item(client) -> tuple[str, str]:
    create_goal(client)
    client.post(f"/api/goals/{GOAL}/plan")
    client.post(f"/api/goals/{GOAL}/plan/approve", json={})
    started = client.post(f"/api/goals/{GOAL}/probe/start", json={}).json()
    item_id = started["question"]["item_id"]
    answered = client.post(
        f"/api/goals/{GOAL}/probe/answer",
        json={"session_id": started["session_id"], "item_id": item_id, "response": "B"},
    )
    assert answered.status_code == 200, answered.text
    return started["session_id"], item_id


def test_the_due_list_proxies_learner_svc_without_the_key(client, settings):
    _session, item_id = _probe_to_first_item(client)

    empty = client.get(f"/api/goals/{GOAL}/holdouts/due")
    assert empty.status_code == 200
    assert empty.json()["due"] == 0

    with open_store(settings) as store:
        force_holdout(store, item_id, assigned_days_ago=30)

    due = client.get(f"/api/goals/{GOAL}/holdouts/due").json()
    assert due["due"] == 1
    pick = due["picks"][0]
    assert pick["item_id"] == item_id
    assert pick["context"] == "delayed"
    assert pick["key_on_file"] is True
    assert "answer" not in pick
    assert pick["options"] and all(set(o) == {"key", "text"} for o in pick["options"])
    assert pick["assistance_level"] == 0
    # learner-svc's `next` still never serves it, in any mode
    with open_store(settings) as store:
        from learning_tutor.learner import api

        for mode in ("probe", "review", "teach", "auto"):
            picks = api.next_(store, GOAL, mode=mode, n=5)["picks"]
            assert item_id not in {p["item_id"] for p in picks}


def test_the_holdout_answer_records_the_context_and_is_idempotent(client, settings):
    session_id, item_id = _probe_to_first_item(client)
    with open_store(settings) as store:
        force_holdout(store, item_id, assigned_days_ago=30)
    phase_before = client.get(f"/api/goals/{GOAL}").json()["phase"]

    body = {"item_id": item_id, "response": "B", "context": "transfer",
            "session_id": session_id, "confidence": 5}
    first = client.post(
        f"/api/goals/{GOAL}/holdouts/answer", json=body, headers={"Idempotency-Key": "h-1"}
    )
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["correct"] is True
    assert first_body["context"] == "transfer"
    assert first_body["recorded"]["context"] == "transfer"
    assert first_body["recorded"]["assistance_level"] == 0
    assert first_body["counts_toward_mastery"] is True

    second = client.post(
        f"/api/goals/{GOAL}/holdouts/answer", json=body, headers={"Idempotency-Key": "h-1"}
    )
    assert second.status_code == 200
    assert second.json()["recorded"]["event_id"] == first_body["recorded"]["event_id"]

    with open_store(settings) as store:
        rows = [
            dict(r)
            for r in store.query(
                "SELECT e.context, e.kind FROM events e "
                "JOIN item_versions v ON v.item_version_id = e.item_version_id "
                "WHERE v.item_id = ? AND e.context = 'transfer'",
                (item_id,),
            )
        ]
        checked = store.one("SELECT last_checked_at FROM holdouts WHERE item_id = ?", (item_id,))
    assert len(rows) == 1, "same key + same body replays; it does not write twice"
    assert checked["last_checked_at"] is not None

    # the denominator the gates need is finally non-zero, and it is learner-svc's number.
    # Two, not one: `metrics._holdout_answers` counts every answer on an item that is a
    # holdout *now*, so the probe answer recorded before the item was forced into the pool
    # counts alongside the transfer check.
    metrics = client.get(f"/api/goals/{GOAL}/metrics").json()
    assert metrics["holdout_success_7d"]["checks"] == 2
    assert metrics["holdout_success_7d"]["passes"] == 2
    assert metrics["false_mastery"]["nodes_with_holdout_checks"] == 1

    # no longer due today, and the phase machine did not move
    assert client.get(f"/api/goals/{GOAL}/holdouts/due").json()["due"] == 0
    assert client.get(f"/api/goals/{GOAL}").json()["phase"] == phase_before


def test_a_holdout_answer_needs_a_stored_key_and_a_holdout_context(client):
    session_id, item_id = _probe_to_first_item(client)

    unknown = client.post(
        f"/api/goals/{GOAL}/holdouts/answer",
        json={"item_id": "i_nobody", "response": "B", "context": "delayed"},
    )
    assert unknown.status_code == 409
    assert unknown.json()["code"] == "unknown_item"

    in_session = client.post(
        f"/api/goals/{GOAL}/holdouts/answer",
        json={"item_id": item_id, "response": "B", "context": "in-session",
              "session_id": session_id},
    )
    assert in_session.status_code == 400
    assert in_session.json()["code"] == "bad_request"

    assert client.get("/api/goals/nope/holdouts/due").status_code == 404
