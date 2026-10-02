"""Every decision rule, with no model, no network and no database.

That this file needs none of those is the point: IDEA.md's *Decision ownership* says the
orchestration module decides, and a decision you cannot test without an LLM is a decision
the LLM is making.
"""

from __future__ import annotations

import pytest

from learning_tutor.tutor import orchestrator as orch

# --------------------------------------------------------------------------- grading


def test_grade_mc_uses_the_key_not_the_client():
    options = ["A. two", "B. four", "C. six", "D. eight", "E. I do not know"]
    assert orch.grade_mc("B", "B", options).correct is True
    assert orch.grade_mc("A", "B", options).correct is False
    # the full option text is accepted as well as the letter
    assert orch.grade_mc("four", "B", options).correct is True


def test_grade_mc_records_a_frozen_grader():
    result = orch.grade_mc("B", "B", ["A. x", "B. y"])
    assert result.grader_version == "mc-key-v1"
    assert result.evaluation_method == "rubric"


@pytest.mark.parametrize("response", ["E", "I do not know", "idk", "I don't know"])
def test_idk_is_never_correct_and_is_flagged(response):
    options = ["A. two", "B. four", "E. I do not know"]
    result = orch.grade_mc(response, "B", options)
    assert result.idk is True
    assert result.correct is False


def test_empty_answer_is_not_a_pass():
    assert orch.grade_mc("", "B", ["A. x", "B. y"]).correct is False
    assert orch.grade_mc(None, "B", ["A. x", "B. y"]).correct is False


def test_options_for_wire_keys_labelled_and_unlabelled():
    labelled = orch.options_for_wire(["A. two", "B. four"])
    assert labelled == [{"key": "A", "text": "two"}, {"key": "B", "text": "four"}]
    bare = orch.options_for_wire(["two", "four", "six"])
    assert [o["key"] for o in bare] == ["A", "B", "C"]
    assert bare[0]["text"] == "two"


# ------------------------------------------------------------------------- the ladder


def test_no_reveal_before_an_attempt():
    ruling = orch.hint_allowed(6, attempts=0, highest_reached=5)
    assert ruling.allowed is False
    assert "attempt" in ruling.reason


def test_reveal_allowed_after_an_attempt():
    assert orch.hint_allowed(6, attempts=1, highest_reached=5).allowed is True


def test_hints_never_skip_a_level():
    assert orch.hint_allowed(1, attempts=1, highest_reached=0).allowed is True
    assert orch.hint_allowed(2, attempts=1, highest_reached=1).allowed is True
    refused = orch.hint_allowed(4, attempts=1, highest_reached=1)
    assert refused.allowed is False
    assert "one level at a time" in refused.reason


@pytest.mark.parametrize("level", [0, -1, 7, 99])
def test_hint_level_out_of_range(level):
    assert orch.hint_allowed(level, attempts=3, highest_reached=5).allowed is False


def test_a_pass_at_assistance_five_or_six_never_counts():
    assert orch.counts_toward_mastery(correct=True, idk=False, assistance_level=4) is True
    assert orch.counts_toward_mastery(correct=True, idk=False, assistance_level=5) is False
    assert orch.counts_toward_mastery(correct=True, idk=False, assistance_level=6) is False
    assert orch.counts_toward_mastery(correct=True, idk=True, assistance_level=0) is False


# --------------------------------------------------------------------------- the probe


def test_probe_stops_on_each_of_the_four_rules():
    assert orch.probe_stop(asked=3, budget=12, learner_asked_to_stop=True).stop is True
    assert orch.probe_stop(asked=3, budget=12, picks_available=False).stop is True
    assert orch.probe_stop(asked=12, budget=12).stop is True
    assert orch.probe_stop(asked=4, budget=12, consecutive_idk=3).stop is True
    assert orch.probe_stop(asked=4, budget=12, consecutive_wrong=3).stop is True


def test_probe_keeps_going_below_every_threshold():
    stop = orch.probe_stop(asked=4, budget=12, consecutive_idk=2, consecutive_wrong=2)
    assert stop.stop is False
    assert stop.reason == ""


def test_next_probe_question_reads_the_services_choice():
    assert orch.next_probe_question({"picks": []}) is None
    pick = {"node_id": "n_a", "node_title": "A"}
    assert orch.next_probe_question({"picks": [pick]}) is pick


# ------------------------------------------------------------------ after an answer


def _decide(**over):
    base = dict(correct=True, idk=False, confidence=None, assistance_level=0)
    base.update(over)
    return orch.decide_after_answer(**base)


def test_pass_continues():
    assert _decide().decision == "continue"


def test_wrong_repeats_until_three_fails():
    assert _decide(correct=False, consecutive_fails=1).decision == "repeat"
    assert _decide(correct=False, consecutive_fails=2).decision == "repeat"


def test_three_fails_switches_strategy_then_backs_up_then_stops():
    first = _decide(correct=False, consecutive_fails=3)
    assert first.decision == "switch_strategy"
    second = _decide(correct=False, consecutive_fails=3, strategy_switched=True)
    assert second.decision == "back_up"
    third = _decide(
        correct=False, consecutive_fails=4, strategy_switched=True, backed_up=True
    )
    assert third.decision == "end_session"


def test_teach_back_falls_due_every_two_nodes():
    assert _decide(nodes_since_teach_back=1).decision == "continue"
    assert _decide(nodes_since_teach_back=2).decision == "teach_back_due"
    assert _decide(nodes_since_teach_back=3).decision == "teach_back_due"


def test_the_session_cap_wins_over_everything():
    decision = _decide(
        correct=False, consecutive_fails=3, minutes_elapsed=46, minutes_cap=45
    )
    assert decision.decision == "end_session"
    assert "cap" in decision.reason


def test_high_confidence_wrong_suspects_a_misconception():
    assert _decide(correct=False, confidence=5).misconception_suspected is True
    assert _decide(correct=False, confidence=4).misconception_suspected is True
    assert _decide(correct=False, confidence=2).misconception_suspected is False
    # a confident *right* answer is not a misconception
    assert _decide(correct=True, confidence=5).misconception_suspected is False
    # nor is a wrong answer reached with help — the help explains it
    assert (
        _decide(correct=False, confidence=5, assistance_level=3).misconception_suspected
        is False
    )
    # nor "I do not know", which is a clean signal, not a belief
    assert _decide(correct=False, idk=True, confidence=5).misconception_suspected is False


def test_reveal_becomes_allowed_only_once_an_attempt_has_happened():
    assert _decide(correct=False).reveal_allowed is True
    assert _decide(correct=True).reveal_allowed is False


# ------------------------------------------------------------------- strategy choice


def test_novices_get_the_worked_example_and_never_socratic():
    novice = {"state": "unknown", "independent_passes": 0, "assisted_passes": 0}
    assert orch.expertise_for(novice) == "novice"
    assert orch.choose_strategy(novice) == "example-first"
    rotated = {orch.choose_strategy(novice, switches=i) for i in range(6)}
    assert "socratic" not in rotated


def test_expertise_reversal_moves_toward_problem_first():
    fragile = {"state": "fragile", "independent_passes": 1, "assisted_passes": 1}
    assert orch.expertise_for(fragile) == "intermediate"
    assert orch.choose_strategy(fragile) == "socratic"
    known = {"state": "known", "independent_passes": 2, "assisted_passes": 0}
    assert orch.expertise_for(known) == "advanced"
    assert orch.choose_strategy(known) == "formal-first"


def test_a_strategy_switch_changes_the_strategy():
    novice = {"state": "unknown"}
    assert orch.choose_strategy(novice, switches=0) != orch.choose_strategy(novice, switches=1)


def test_strategy_is_never_a_learning_style():
    """There is no input to choose_strategy that is not evidence."""

    import inspect

    signature = inspect.signature(orch.choose_strategy)
    assert set(signature.parameters) == {"node_state", "expertise", "switches"}


# ------------------------------------------------------------------- misconceptions


def test_the_sequence_is_reasoning_then_prediction_then_counterexample():
    step, state = orch.misconception_next_step([])
    assert (step, state) == ("reasoning", "suspected")
    step, state = orch.misconception_next_step([{"step": "reasoning", "outcome": "held"}])
    assert (step, state) == ("prediction", "suspected")
    step, state = orch.misconception_next_step(
        [
            {"step": "reasoning", "outcome": "held"},
            {"step": "prediction", "outcome": "held"},
        ]
    )
    assert (step, state) == ("counterexample", "suspected")


def test_three_held_confirms_and_any_dropped_kills_it():
    held3 = [{"step": s, "outcome": "held"} for s in orch.MISCONCEPTION_STEPS]
    assert orch.misconception_next_step(held3) == (None, "active")
    dropped = [
        {"step": "reasoning", "outcome": "held"},
        {"step": "prediction", "outcome": "dropped"},
    ]
    assert orch.misconception_next_step(dropped) == (None, "dropped")


class _RecordingClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def post(self, path, body, **kwargs):
        self.calls.append((path, body))
        return self.response


def test_misconception_sequence_posts_the_step_and_reads_the_state():
    client = _RecordingClient(
        {"state": "suspected", "steps": [{"step": "reasoning", "outcome": "held"}]}
    )
    result = orch.misconception_sequence(
        client,
        goal_id="g",
        node_id="n_1",
        claim="a covector is a vector written sideways",
        step="reasoning",
        outcome="held",
    )
    assert client.calls[0][0] == "/v1/misconceptions/confirm-step"
    assert client.calls[0][1]["step"] == "reasoning"
    assert result["state"] == "suspected"
    assert result["next_step"] == "prediction"


def test_misconception_sequence_rejects_an_unknown_step():
    with pytest.raises(ValueError):
        orch.misconception_sequence(
            _RecordingClient({}), goal_id="g", node_id="n", claim="c", step="vibes",
            outcome="held",
        )


# ------------------------------------------------------------------------- the graph


NODES = [
    {"node_id": "n_a", "title": "A"},
    {"node_id": "n_b", "title": "B"},
    {"node_id": "n_c", "title": "C"},
]
EDGES = [
    {"from_node": "n_a", "to_node": "n_b", "type": "strict_prerequisite"},
    {"from_node": "n_b", "to_node": "n_c", "type": "strict_prerequisite"},
    {"from_node": "n_a", "to_node": "n_c", "type": "supports"},
]


def test_learning_path_respects_strict_prerequisites_only():
    assert orch.learning_path(NODES, EDGES) == ["n_a", "n_b", "n_c"]


def test_learning_path_is_deterministic_under_a_cycle():
    cyclic = EDGES + [{"from_node": "n_c", "to_node": "n_a", "type": "strict_prerequisite"}]
    assert orch.learning_path(NODES, cyclic) == orch.learning_path(NODES, cyclic)


def test_path_mermaid_colours_by_state():
    mermaid = orch.path_mermaid(NODES, ["n_a", "n_b"], {"n_a": "known", "n_b": "fragile"})
    assert "graph LR" in mermaid
    assert "n_a --> n_b" in mermaid
    assert "class n_a known;" in mermaid
    assert "class n_b fragile;" in mermaid


# ----------------------------------------------------------------------- feasibility


GRAPH_10 = {
    "nodes": [
        {"node_id": f"n_{i}", "title": str(i), "state": {"state": "unknown"}} for i in range(10)
    ]
}


def test_feasibility_shows_the_arithmetic_and_fits():
    goal = {"minutes_per_session": 60, "deadline": "2026-10-05"}
    result = orch.feasibility(goal, GRAPH_10, today="2026-09-05", sessions_per_week=3)
    assert result.nodes_remaining == 10
    assert result.minutes_needed == 10 * orch.PACE_MIN
    assert result.sessions_left == 12  # 30 days x 3/7
    assert result.fits is True
    assert str(result.minutes_needed) in result.statement
    assert result.options == []


def test_feasibility_offers_exactly_three_options_when_it_does_not_fit():
    goal = {"minutes_per_session": 20, "deadline": "2026-09-12"}
    result = orch.feasibility(goal, GRAPH_10, today="2026-09-05", sessions_per_week=2)
    assert result.fits is False
    assert len(result.options) == 3
    assert "does not fit" in result.statement


def test_feasibility_says_so_when_there_is_no_deadline():
    result = orch.feasibility(
        {"minutes_per_session": 45}, GRAPH_10, today="2026-09-05", sessions_per_week=3
    )
    assert result.fits is None
    assert "No deadline" in result.statement


def test_feasibility_ignores_nodes_already_known():
    graph = {
        "nodes": [
            {"node_id": "n_1", "state": {"state": "known"}},
            {"node_id": "n_2", "state": {"state": "unknown"}},
        ]
    }
    result = orch.feasibility(
        {"minutes_per_session": 45, "deadline": "2026-12-01"},
        graph,
        today="2026-09-05",
    )
    assert result.nodes_remaining == 1


def test_pace_min_is_stated_as_an_assumption():
    result = orch.feasibility(
        {"minutes_per_session": 45, "deadline": "2026-12-01"}, GRAPH_10, today="2026-09-05"
    )
    assert "assumption" in result.statement


def test_feasibility_carries_the_names_web_ui_reads():
    """`web-ui/src/lib/types.ts::Feasibility` is the shape; these are its four keys."""

    goal = {"minutes_per_session": 60, "deadline": "2026-10-05"}
    payload = orch.feasibility(
        goal, GRAPH_10, today="2026-09-05", sessions_per_week=3
    ).as_dict()

    assert set(payload) >= {"sessions_needed", "sessions_available", "verdict", "assumption"}
    assert payload["sessions_needed"] == 2  # ceil(120 min / 60)
    assert payload["sessions_available"] == payload["sessions_left"] == 12
    assert payload["verdict"] == "comfortable"
    assert "not a measurement" in payload["assumption"]
    # the arithmetic keys are still there — nothing was renamed out from under the docs
    assert payload["minutes_needed"] == 120
    assert payload["fits"] is True


def test_feasibility_verdict_matches_the_ui_thresholds():
    # 10 nodes x 12 min = 120 min; 40 min sessions -> 3 needed.
    tight = orch.feasibility(
        {"minutes_per_session": 40, "deadline": "2026-09-12"},
        GRAPH_10,
        today="2026-09-05",
        sessions_per_week=3,
    )
    assert tight.sessions_needed == 3
    assert tight.sessions_left == 3  # 7 days x 3/7
    assert tight.verdict == "tight"  # equal is tight, not comfortable (1.25x is the bar)

    short = orch.feasibility(
        {"minutes_per_session": 20, "deadline": "2026-09-12"},
        GRAPH_10,
        today="2026-09-05",
        sessions_per_week=2,
    )
    assert short.verdict == "not-feasible"

    no_deadline = orch.feasibility(
        {"minutes_per_session": 45}, GRAPH_10, today="2026-09-05", sessions_per_week=3
    )
    assert no_deadline.sessions_left is None
    assert no_deadline.verdict == "unknown"


def test_feasibility_without_a_session_length_has_no_sessions_needed():
    """No minutes per session, no arithmetic — and the gateway drops the field entirely."""

    result = orch.feasibility(
        {"deadline": "2026-12-01"}, GRAPH_10, today="2026-09-05", sessions_per_week=3
    )
    assert result.sessions_needed is None
    assert result.verdict == "unknown"


# ------------------------------------------------------- interrupts (checkpoint guard)


@pytest.mark.parametrize(
    "text",
    [
        "what is the answer?",
        "What's the correct answer to this one?",
        "which option is correct?",
        "Which letter is right?",
        "just tell me the answer",
        "give me the answer, I am out of time",
        "can you show me the solution",
        "what should I pick here?",
        "is it B?",
        "I give up — what's the right answer",
    ],
)
def test_an_interrupt_asking_for_the_key_is_recognised(text):
    assert orch.asks_for_the_checkpoint_answer(text) is True


@pytest.mark.parametrize(
    "text",
    [
        "wait, why does the output have to be a scalar?",
        "where did that minus sign come from?",
        "why is linearity part of the definition and not a consequence?",
        "I do not follow the second line of the derivation",
        "does this still hold in three dimensions?",
        "can you say that again in plain words?",
    ],
)
def test_a_real_question_about_the_step_is_not_treated_as_asking_for_the_key(text):
    assert orch.asks_for_the_checkpoint_answer(text) is False


def test_the_refusal_text_is_the_hint_ladder_rule_not_a_generated_apology():
    rule = orch.CHECKPOINT_ANSWER_RULE
    assert "No hint before an attempt" in rule
    assert "level 6" in rule.lower()
    assert "non-pass" in rule
