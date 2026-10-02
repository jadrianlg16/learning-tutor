"""Builds the one Telegram message a push sends, and the feedback edit after a tap.

Hard rule (CONTRACTS.md #6, IDEA.md *Downtime retrieval over Telegram*): push retrieval,
never content. The question message below carries the stem and the options **only** —
never the answer key, never an explanation. Feedback (1-3 lines, correct/incorrect + the
correct option + the misconception note if one exists) is built only by
:func:`build_feedback_message`, which every caller in :mod:`telegram.job` reaches only
*after* a tap has already been recorded. There is no code path from
:func:`build_question_message` to an answer key.
"""

from __future__ import annotations

import re
from typing import Any

_OPTION_RE = re.compile(r"^\s*([A-Za-z])[.)]\s*(.*)$")

IDK_LABEL = "I don't know"
NOT_NOW_LABEL = "Not now"


def option_letter(option_text: str) -> str:
    """"B. -2 - evaluate the linear functional..." -> "B". Falls back to the whole string."""

    match = _OPTION_RE.match(option_text)
    return match.group(1).upper() if match else option_text.strip()[:1].upper()


def _positional_letter(index: int) -> str:
    return chr(ord("A") + index)


def resolve_answer_letter(options: list[str], answer: str) -> str:
    """Normalize an item's ``answer`` field to the positional letter used in the keyboard.

    ``item_versions.answer`` is free text set by whoever authored the item: the canonical
    convention (``skills/teach/examples/item.example.json``) stores just the leading letter
    ("B"), but nothing enforces that — an item can just as well store the full matching
    option text. Both are resolved here to the same positional letter
    (:func:`_positional_letter`) so grading a tap is a single equality check regardless of
    which convention the item used.
    """

    for index, option in enumerate(options):
        letter = _positional_letter(index)
        if answer == option or answer == option_letter(option) or answer == letter:
            return letter
    # Nothing matched (a malformed item) — best effort so a bad item degrades to "always
    # wrong" instead of crashing the job.
    return option_letter(answer) if answer else ""


def option_by_letter(options: list[str], letter: str) -> str | None:
    index = ord(letter.upper()) - ord("A") if letter and len(letter) == 1 else -1
    if 0 <= index < len(options):
        return options[index]
    return None


def callback_data(kind: str, token: str, letter: str | None = None) -> str:
    if letter is not None:
        return f"lt:{kind}:{token}:{letter}"
    return f"lt:{kind}:{token}"


def parse_callback_data(data: str) -> dict[str, str]:
    parts = data.split(":")
    if len(parts) < 3 or parts[0] != "lt":
        raise ValueError(f"not a micro-review callback: {data!r}")
    kind, token, *rest = parts[1:]
    parsed = {"kind": kind, "token": token}
    if rest:
        parsed["letter"] = rest[0]
    return parsed


def build_question_message(pick: dict[str, Any], token: str) -> dict[str, Any]:
    """The outbound push: stem + options + IDK + Not now. No answer, no explanation."""

    node_title = pick.get("node_title") or "Review"
    stem = pick.get("stem") or ""
    options: list[str] = list(pick.get("options") or [])

    text = f"\U0001f501 {node_title}\n\n{stem}".rstrip()

    keyboard: list[list[dict[str, str]]] = []
    for index, option in enumerate(options):
        letter = _positional_letter(index)
        keyboard.append(
            [{"text": option[:64], "callback_data": callback_data("opt", token, letter)}]
        )
    keyboard.append(
        [
            {"text": IDK_LABEL, "callback_data": callback_data("idk", token)},
            {"text": NOT_NOW_LABEL, "callback_data": callback_data("notnow", token)},
        ]
    )
    return {"text": text, "reply_markup": {"inline_keyboard": keyboard}}


def build_feedback_message(
    *,
    node_title: str | None,
    stem: str | None,
    correct: bool,
    idk: bool,
    answer_key: str,
    options: list[str],
    distractor_misconceptions: dict[str, str] | None,
    picked_letter: str | None,
) -> str:
    """1-3 lines, shown only after an attempt — the one place content appears.

    ``answer_key`` and ``picked_letter`` are both positional letters (see
    :func:`resolve_answer_letter`); ``distractor_misconceptions`` keys may be either
    convention (a bare letter, per the canonical item example, or the full option text),
    so the lookup below tries the option text first and falls back to the letter.
    """

    correct_option = option_by_letter(options, answer_key) or answer_key
    if idk:
        headline = "You said you didn't know."
    elif correct:
        headline = "Correct."
    else:
        headline = "Not quite."

    lines = [headline]
    if not correct:
        lines.append(f"The answer was {correct_option}.")
        claim = None
        if not idk and picked_letter and distractor_misconceptions:
            picked_option = option_by_letter(options, picked_letter)
            claim = distractor_misconceptions.get(
                picked_option or "", distractor_misconceptions.get(picked_letter)
            )
        if claim:
            lines.append(f"That option matches: {claim}.")
    title = f"{node_title} — " if node_title else ""
    header = f"{title}{stem}" if stem else title.rstrip(" —")
    return "\n\n".join(part for part in (header, "\n".join(lines)) if part)


def build_not_now_message(node_title: str | None, stem: str | None) -> str:
    title = f"{node_title} — " if node_title else ""
    header = f"{title}{stem}" if stem else title.rstrip(" —")
    return "\n\n".join(part for part in (header, "Skipped — asked again another time.") if part)


__all__ = [
    "option_letter",
    "resolve_answer_letter",
    "option_by_letter",
    "callback_data",
    "parse_callback_data",
    "build_question_message",
    "build_feedback_message",
    "build_not_now_message",
    "IDK_LABEL",
    "NOT_NOW_LABEL",
]
