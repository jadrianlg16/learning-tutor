"""`tutor` — prompts and decisions (Stage 2, Mode A).

Two halves that must not be confused, per IDEA.md *Review response* → *Decision
ownership*: **one orchestration module decides the next question, pass/fail, strategy
change, session end and graph revision**, and `tutor` "supplies prompts, never decisions".

* :mod:`.orchestrator` — every decision, as code. Pure functions (or thin wrappers over a
  learner-svc client), unit-tested with no model in the loop.
* :mod:`.prompts` — the prompt pack loader. The single source of truth is
  ``skills/teach/prompts/<version>/``, the same files the Stage 0 skill reads: Mode A runs
  the same pedagogy through ``llm``, it does not write a second one.
* :mod:`.generate` — the LLM-facing calls: typed schemas, the item-authoring pipeline
  (author → blind solve → validate), the diagram check/fix/render loop, plan verification.

See docs/modules/tutor.md.
"""

from __future__ import annotations

from .orchestrator import (
    CHECKPOINT_ANSWER_RULE,
    PACE_MIN,
    TEACH_BACK_EVERY_NODES,
    Decision,
    Feasibility,
    GradeResult,
    HintRuling,
    ProbeStop,
    asks_for_the_checkpoint_answer,
    choose_strategy,
    decide_after_answer,
    expertise_for,
    feasibility,
    grade_mc,
    hint_allowed,
    learning_path,
    misconception_next_step,
    next_probe_question,
    next_teach_node,
    probe_stop,
)
from .prompts import PromptPack, PromptPackError, load_pack

__all__ = [
    "CHECKPOINT_ANSWER_RULE",
    "PACE_MIN",
    "TEACH_BACK_EVERY_NODES",
    "Decision",
    "Feasibility",
    "GradeResult",
    "HintRuling",
    "ProbeStop",
    "PromptPack",
    "PromptPackError",
    "asks_for_the_checkpoint_answer",
    "choose_strategy",
    "decide_after_answer",
    "expertise_for",
    "feasibility",
    "grade_mc",
    "hint_allowed",
    "learning_path",
    "load_pack",
    "misconception_next_step",
    "next_probe_question",
    "next_teach_node",
    "probe_stop",
]
