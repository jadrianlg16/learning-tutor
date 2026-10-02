"""Message formatting: the keyboard shape, and that no content leaks before an attempt."""

from __future__ import annotations

import pytest

from telegram.formatting import (
    IDK_LABEL,
    NOT_NOW_LABEL,
    build_feedback_message,
    build_not_now_message,
    build_question_message,
    callback_data,
    option_letter,
    parse_callback_data,
)

PICK = {
    "node_id": "n_1",
    "node_title": "Covectors",
    "item_id": "i_1",
    "item_version_id": "iv_1",
    "stem": "What does a covector eat?",
    "options": [
        "A. a vector, returns a scalar",
        "B. a scalar, returns a vector",
        "C. I do not know",
    ],
    "context": "in-session",
}


def test_option_letter_parses_dot_and_paren_forms():
    assert option_letter("A. some text") == "A"
    assert option_letter("b) other text") == "B"
    assert option_letter("just text") == "J"


def test_callback_data_round_trips():
    data = callback_data("opt", "tok123", "B")
    assert data == "lt:opt:tok123:B"
    parsed = parse_callback_data(data)
    assert parsed == {"kind": "opt", "token": "tok123", "letter": "B"}


def test_callback_data_without_letter_round_trips():
    data = callback_data("notnow", "tok123")
    parsed = parse_callback_data(data)
    assert parsed == {"kind": "notnow", "token": "tok123"}


def test_parse_callback_data_rejects_foreign_data():
    with pytest.raises(ValueError):
        parse_callback_data("mp:something:else")


def test_question_message_keyboard_has_every_option_plus_idk_and_not_now():
    message = build_question_message(PICK, "tok123")
    keyboard = message["reply_markup"]["inline_keyboard"]
    labels = [button["text"] for row in keyboard for button in row]

    for option in PICK["options"]:
        assert option[:64] in labels
    assert IDK_LABEL in labels
    assert NOT_NOW_LABEL in labels


def test_question_message_keyboard_callback_data_carries_the_token():
    message = build_question_message(PICK, "tok123")
    keyboard = message["reply_markup"]["inline_keyboard"]
    all_data = [button["callback_data"] for row in keyboard for button in row]
    assert "lt:opt:tok123:A" in all_data
    assert "lt:opt:tok123:B" in all_data
    assert "lt:idk:tok123" in all_data
    assert "lt:notnow:tok123" in all_data


def test_question_message_never_reveals_the_answer():
    message = build_question_message(PICK, "tok123")
    text = message["text"]
    # The stem and node title only — no "answer", no "correct", no distractor claim text.
    assert "correct" not in text.lower()
    assert "the answer is" not in text.lower()


def test_question_message_contains_stem_and_node_title():
    message = build_question_message(PICK, "tok123")
    assert PICK["stem"] in message["text"]
    assert PICK["node_title"] in message["text"]


def test_feedback_message_correct_is_short_and_positive():
    text = build_feedback_message(
        node_title="Covectors",
        stem="What does a covector eat?",
        correct=True,
        idk=False,
        answer_key="A",
        options=PICK["options"],
        distractor_misconceptions={},
        picked_letter="A",
    )
    assert "Correct" in text
    assert len(text.splitlines()) <= 4  # 1-3 lines of feedback plus the header


def test_feedback_message_incorrect_shows_the_correct_option():
    text = build_feedback_message(
        node_title="Covectors",
        stem="What does a covector eat?",
        correct=False,
        idk=False,
        answer_key="A",
        options=PICK["options"],
        distractor_misconceptions={"B": "confuses covectors and vectors"},
        picked_letter="B",
    )
    assert "Not quite" in text
    assert PICK["options"][0] in text  # the correct option, spelled out
    assert "confuses covectors and vectors" in text


def test_feedback_message_idk_does_not_claim_a_misconception():
    text = build_feedback_message(
        node_title="Covectors",
        stem="What does a covector eat?",
        correct=False,
        idk=True,
        answer_key="A",
        options=PICK["options"],
        distractor_misconceptions={"B": "confuses covectors and vectors"},
        picked_letter=None,
    )
    assert "didn't know" in text
    assert "confuses covectors and vectors" not in text


def test_not_now_message_has_no_answer_or_explanation():
    text = build_not_now_message("Covectors", "What does a covector eat?")
    assert "Skipped" in text
    for option in PICK["options"]:
        assert option not in text
