"""The LLM-facing half: typed schemas, the item pipeline, the diagram loop, verification.

Everything here *generates*. Nothing here *decides* — that is
:mod:`learning_tutor.tutor.orchestrator`, and the split is IDEA.md's *Decision ownership*
rule. A function in this module may fail, return nothing, or hand back something the
orchestrator then refuses; it never chooses what happens next.

Three guarantees this module implements, each from CONTRACTS.md:

* **The same model is never author, solver and judge** (hard rule 7 in the skill; the
  ``evaluation_method`` table in learner-svc). :func:`author_item` authors with role
  ``tutor``, blind-solves with role ``solver``, and only a solver agreement validates the
  item as ``blind_solver``. ``llm`` refuses a solver that resolves to the tutor's own
  provider+model unless ``LT_LLM_ALLOW_SAME_SOLVER=1``; that refusal leaves the item
  ``TEACHING_ONLY``, which is the correct outcome, not an error.
* **Corpus text is data** (hard rule 4). Every prompt goes through
  :meth:`PromptPack.compose`, which puts rendered corpus blocks in the user message with
  the ``<<<SOURCE ...>>>`` fences intact.
* **Cite or abstain.** :func:`verify_plan` runs ``corpus.cite_or_abstain`` per node claim
  and reports abstention as a first-class status, never as a missing citation.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..llm.structured import SameSolverError, generate_structured
from .prompts import PromptPack

# --------------------------------------------------------------------------- schemas


class Schema(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")


EdgeType = Literal[
    "strict_prerequisite",
    "recommended_background",
    "course_sequence",
    "co_requisite",
    "supports",
    "misconception_for",
    "transfer_related",
]
Provenance = Literal["course", "reference", "model", "learner_evidence", "human"]


class PlanNode(Schema):
    title: str
    aliases: list[str] = Field(default_factory=list)


class PlanEdge(Schema):
    #: ``from`` is a Python keyword; the wire name is what CONTRACTS.md and the CLI use.
    from_: str = Field(alias="from")
    to: str
    type: EdgeType = "strict_prerequisite"
    provenance: Provenance = "model"


class PlanGraph(Schema):
    """plan.md *Emit* — 10–40 nodes, typed edges, provenance on every edge."""

    #: ``min_length=1``: a reply with no nodes (a schema echo, an empty object) must fail
    #: validation so ``generate_structured`` retries with the error, instead of returning an
    #: empty plan that then gets cached.
    nodes: list[PlanNode] = Field(default_factory=list, min_length=1)
    edges: list[PlanEdge] = Field(default_factory=list)
    #: Ours, not the model's — :func:`generate_plan` overwrites whatever came back here.
    #: Excluded from ``model_dump`` so it can never reach learner-svc as graph data.
    warnings: list[str] = Field(default_factory=list, exclude=True)

    def payload(self) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """``(body for POST /v1/graph/{g}/import, dropped edges)``.

        Models reliably emit an edge to a node they forgot to declare — "Misconceptions
        about growth" appeared as an edge endpoint and never as a node in the first real
        run against a local llama3.1:8b. learner-svc resolves edge endpoints by title or
        alias and 404s the **whole import** on the first one it cannot find, so one
        hallucinated endpoint would cost the entire plan.

        Dropping the unresolvable edge and keeping the graph is the right trade: an edge
        is a claim about ordering, and a claim naming a thing that does not exist is not a
        claim worth keeping. What is dropped is reported, never swallowed.
        """

        known: set[str] = set()
        seen: list[PlanNode] = []
        for node in self.nodes:
            title = (node.title or "").strip()
            if not title or title.casefold() in known:
                continue
            seen.append(node)
            known.add(title.casefold())
            known.update(alias.strip().casefold() for alias in node.aliases if alias.strip())

        kept: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []
        for edge in self.edges:
            row = edge.model_dump(by_alias=True)
            missing = [
                name
                for name in (row.get("from"), row.get("to"))
                if str(name or "").strip().casefold() not in known
            ]
            if missing:
                dropped.append({**row, "reason": f"no such node: {', '.join(map(str, missing))}"})
            else:
                kept.append(row)

        return {"nodes": [node.model_dump() for node in seen], "edges": kept}, dropped


class ProbeItem(Schema):
    """probe.md *Item shape*: 4 content options plus "I do not know", always last, and
    every wrong option mapped to a **named** misconception."""

    stem: str
    options: list[str] = Field(default_factory=list)
    answer: str
    distractor_misconceptions: dict[str, str] = Field(default_factory=dict)
    kind: Literal["recall", "concept", "apply", "analyze", "transfer"] = "concept"
    components: list[str] = Field(default_factory=list)
    explanation: str = ""

    def spec(self) -> dict[str, Any]:
        """The body ``POST /v1/items`` takes (``explanation`` is ours, not the core's)."""

        return {
            "stem": self.stem,
            "options": list(self.options),
            "answer": self.answer,
            "distractor_misconceptions": dict(self.distractor_misconceptions),
            "kind": self.kind,
            "components": list(self.components),
        }


class CheckpointItem(ProbeItem):
    """Same shape as a probe item; a different phase file writes it (checkpoint.md)."""


class TeachStep(Schema):
    strategy: str = "example-first"
    markdown: str = ""
    mermaid: str | None = None
    latex: str | None = None
    self_explanation_prompt: str = ""


class HintText(Schema):
    level: int = 1
    hint_markdown: str = ""


class InterruptAnswer(Schema):
    """"Wait, why?" answered inline. No new node, no new step, no checkpoint answer."""

    answer_markdown: str = ""


class TeachBackGrade(Schema):
    """teach-back-rubric.md, frozen at ``teach-back-v1``: 0–3 with named anchors."""

    score: int = Field(default=0, ge=0, le=3)
    anchor: str = ""
    feedback_markdown: str = ""


class MisconceptionProbe(Schema):
    claim: str = ""
    step: Literal["reasoning", "prediction", "counterexample"] = "reasoning"
    prompt_markdown: str = ""
    expected_if_misconception: str = ""
    expected_if_correct: str = ""


class MisconceptionStepOutcome(Schema):
    """``held`` or ``dropped`` for one confirmation step, with the reason."""

    outcome: Literal["held", "dropped"] = "dropped"
    reason: str = ""
    reply_markdown: str = ""


class SolverVerdict(Schema):
    """What the blind solver returns. It never sees the key, the teaching, or the rationale."""

    answer: str = ""
    reasoning: str = ""
    ambiguous: bool = False


class MermaidFix(Schema):
    mermaid: str = ""


class Feedback(Schema):
    feedback_markdown: str = ""


# --------------------------------------------------------------------------- helpers

JSON_RULE = (
    "Reply with ONLY a JSON object matching the schema below. No prose, no code fence, no "
    "explanation outside the JSON.\n\nSchema:\n"
)


def _schema_hint(schema: type[BaseModel]) -> str:
    return JSON_RULE + json.dumps(schema.model_json_schema(), indent=2)


def goal_block(goal: dict[str, Any], extras: dict[str, Any] | None = None) -> str:
    """The goal contract as a prompt block. Facts only — no numbers about the learner."""

    merged = {**(goal or {}), **(extras or {})}
    lines = []
    for key in (
        "goal_id",
        "title",
        "concept",
        "depth",
        "purpose",
        "deadline",
        "minutes_per_session",
        "sessions_per_week",
        "assessment",
        "source_priority",
        "domain",
        "target_capabilities",
        "transfer_required",
    ):
        value = merged.get(key)
        if value not in (None, "", [], {}):
            lines.append(f"- **{key}**: {value}")
    return "\n".join(lines) or "- (no goal contract fields set)"


def _generate(
    prompt_pair: Any,
    schema: type[BaseModel],
    *,
    role: str = "tutor",
    retries: int = 2,
    cache: bool = True,
) -> tuple[Any, Any]:
    return generate_structured(
        prompt_pair.user + "\n\n" + _schema_hint(schema),
        schema,
        role=role,
        retries=retries,
        cache=cache,
        system_prompt=prompt_pair.system,
    )


# --------------------------------------------------------------------------- plan

#: plan.md asks for 10-40 nodes. Below this a "plan" is a table of contents, not a route
#: through the material — and an 8B model reliably lands there. Configurable because the
#: right floor for a two-week revision goal is not the right floor for a course.
DEFAULT_PLAN_MIN_NODES = 6


def plan_min_nodes() -> int:
    """``LT_PLAN_MIN_NODES`` — the node count below which a plan is retried once."""

    raw = os.environ.get("LT_PLAN_MIN_NODES")
    if raw is None or not raw.strip():
        return DEFAULT_PLAN_MIN_NODES
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"LT_PLAN_MIN_NODES must be an integer, got {raw!r}") from exc


def generate_plan(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None = None,
    corpus_context: str | None = None,
    structure: list[dict[str, Any]] | None = None,
) -> tuple[PlanGraph, Any]:
    """Emit the dependency graph, using the corpus structure as a prior when there is one.

    corpus.md: "a course's structure is a **prior for the plan graph**, so the route
    through the material matches the route the exam assumes." plan.md turns that into
    ``course_sequence`` edges with ``provenance: course``.
    """

    blocks: list[str] = []
    if structure:
        outline = "\n".join(
            f"{entry.get('ordinal')}. {'  ' * max(0, int(entry.get('level') or 1) - 1)}"
            f"{entry.get('title')}  ({entry.get('source_title')})"
            for entry in structure[:120]
        )
        blocks.append(
            "## Course structure (an alignment prior, not the truth)\n\n"
            "This is the learner's own material, in its own order. Use it for scope, "
            "notation and sequence: emit `course_sequence` edges with "
            "`provenance: course` where it constrains the order. It does not settle "
            "correctness.\n\n```\n" + outline + "\n```"
        )
    task = (
        "Emit the plan graph for this goal: 10-40 nodes, one checkable thing per node, "
        "typed edges with provenance on every edge. Include at least one "
        "`misconception_for` node if the domain has strong wrong intuitions, and at least "
        "two `transfer_related` edges. Node titles use the notation the alignment sources "
        "use. Do not invent node ids - titles only."
    )

    def _compose(extra_task: str = ""):
        return pack.compose(
            "plan",
            domain=(extras or {}).get("domain") or goal.get("domain"),
            goal_block=goal_block(goal, extras),
            corpus_context=corpus_context,
            extra_user_blocks=blocks,
            task=task + extra_task,
        )

    minimum = plan_min_nodes()
    plan, meta = _generate(_compose(), PlanGraph)
    plan.warnings = []

    if len(plan.nodes) < minimum:
        # Measured 2026-09-05: llama3.1:8b returned 5 nodes where plan.md asks for 10-40.
        # One retry with the count stated as the requirement, because a short graph is a
        # summary of the subject rather than a route through it — and because asking again
        # is cheaper than teaching from a curriculum with the middle missing.
        retry, retry_meta = _generate(
            _compose(
                f"\n\nYour previous attempt returned {len(plan.nodes)} nodes. That is too "
                f"few: emit at least {minimum} nodes, and 10-40 is the target. Break each "
                "broad node into the separate checkable things it is made of."
            ),
            PlanGraph,
        )
        retry.warnings = []
        if len(retry.nodes) > len(plan.nodes):
            plan, meta = retry, retry_meta

    if len(plan.nodes) < minimum:
        # Returned anyway, with the shortfall stated. Refusing would leave the learner with
        # no plan at all, and the graph is reviewable and revisable by hand.
        plan.warnings = [f"plan has {len(plan.nodes)} nodes; plan.md asks for 10–40"]

    return plan, meta


def verify_plan(
    nodes: list[dict[str, Any]],
    *,
    goal_id: str,
    settings: Any,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    """``cite_or_abstain`` per node claim. Abstention is an outcome, not a failure.

    Returns ``[]`` when the goal has no corpus at all — there is nothing to cite against,
    and a row of abstentions would read as if the corpus had been consulted and had
    nothing to say.
    """

    try:
        from ..corpus import open_store as open_corpus
        from ..corpus.cite import cite_or_abstain
        from ..corpus.store import list_sources
    except ImportError:  # pragma: no cover - corpus is a hard dependency of the gateway
        return []

    out: list[dict[str, Any]] = []
    with open_corpus(settings) as store:
        if not list_sources(store, goal_id):
            return []
        for node in nodes:
            title = str(node.get("title") or "")
            result = cite_or_abstain(store, title, goal_id, min_score=min_score)
            out.append(
                {
                    "node_id": node.get("node_id"),
                    "claim": title,
                    "status": result.get("status", "abstain"),
                    "citations": result.get("citations", []),
                    "proves": result.get("proves", []),
                    "reason": result.get("reason"),
                }
            )
    return out


def corpus_context_for(
    settings: Any,
    goal_id: str,
    *,
    max_tokens: int = 6000,
    query: str | None = None,
    k: int = 6,
) -> tuple[str | None, list[dict[str, Any]], bool]:
    """``(rendered_text, structure, corpus_exists)``.

    With no ``query`` this is the whole-corpus-in-context path corpus.md's ``/context``
    endpoint describes (right for the plan phase, where the shape of the course matters).
    With a ``query`` it retrieves per node, which is what teaching needs.
    """

    try:
        from ..corpus import open_store as open_corpus
        from ..corpus.sanitize import render_for_context
        from ..corpus.search import search
        from ..corpus.store import get_structure, list_chunks, list_sources
    except ImportError:  # pragma: no cover
        return None, [], False

    with open_corpus(settings) as store:
        if not list_sources(store, goal_id):
            return None, [], False
        structure = get_structure(store, goal_id)
        if query:
            hits = search(store, goal_id, query, k=k)
            chunks = hits if isinstance(hits, list) else []
        else:
            chunks = list_chunks(store, goal_id)
        if not chunks:
            return None, structure, True
        rendered = render_for_context(chunks, max_tokens=max_tokens)
        return rendered.get("text"), structure, True


# --------------------------------------------------------------------------- items


def generate_item(
    pack: PromptPack,
    *,
    phase: str,
    goal: dict[str, Any],
    extras: dict[str, Any] | None = None,
    node_title: str,
    depth: str,
    corpus_context: str | None = None,
    avoid: list[str] | None = None,
    surface_form: str | None = None,
    schema: type[ProbeItem] = ProbeItem,
) -> tuple[ProbeItem, Any]:
    """One graded multiple-choice item on ``node_title``."""

    task = (
        f"Write ONE graded multiple-choice item on the node '{node_title}' at Bloom depth "
        f"'{depth}'. Four content options plus 'E. I do not know' as the last option. "
        "Label the options 'A. ', 'B. ', 'C. ', 'D. ', 'E. '. `answer` is the letter of "
        "the correct option. Every wrong option is mapped in "
        "`distractor_misconceptions` to a NAMED belief its chooser holds - no filler "
        "options. `explanation` is one sentence saying why the key is right, for the "
        "learner to read AFTER an attempt."
    )
    if surface_form:
        task += f" Use a different surface form from anything already practised: {surface_form}."
    blocks = []
    if avoid:
        blocks.append(
            "## Already asked on this node (do not repeat)\n\n"
            + "\n".join(f"- {stem}" for stem in avoid[:8])
        )
    composed = pack.compose(
        phase,
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        corpus_context=corpus_context,
        extra_user_blocks=blocks,
        task=task,
    )
    return _generate(composed, schema)


def blind_solve(item: ProbeItem) -> tuple[SolverVerdict | None, str]:
    """Answer the item with role ``solver``, shown only the stem and the options.

    Returns ``(verdict, note)``. A ``SameSolverError`` is not an exception here: it means
    the configured solver is the tutor, so there is no independent evidence to be had, and
    the item must stay ``TEACHING_ONLY``. The note says which happened.
    """

    prompt = (
        "Answer this multiple-choice question.\n\n"
        f"{item.stem}\n\n" + "\n".join(item.options) + "\n\n"
        "Reply with the letter of the single best option and one line of reasoning. If the "
        "question is ambiguous or has no single best answer, set ambiguous to true.\n\n"
        + _schema_hint(SolverVerdict)
    )
    try:
        verdict, _meta = generate_structured(
            prompt, SolverVerdict, role="solver", retries=1, cache=True
        )
    except SameSolverError as exc:
        return None, f"blind solve skipped: {exc}"
    except Exception as exc:  # a solver that errors is a failed validation, not a crash
        return None, f"blind solve failed: {exc}"
    return verdict, ""


def _letter(value: str) -> str:
    text = (value or "").strip()
    return text[0].upper() if text else ""


def author_item(
    pack: PromptPack,
    learner: Any,
    *,
    phase: str,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_id: str,
    node_title: str,
    depth: str,
    corpus_context: str | None = None,
    avoid: list[str] | None = None,
    author: str = "tutor",
    idempotency_key: str | None = None,
    surface_form: str | None = None,
) -> dict[str, Any]:
    """Author → blind solve → validate. One regeneration on a solver disagreement.

    ``surface_form`` asks for a transfer variant: the same concept in a form the learner has
    not practised (passed to :func:`generate_item`, kept on the regeneration too).

    The item exists in learner-svc either way. A disagreement leaves it ``TEACHING_ONLY``,
    which means it can be *taught with* and can never write evidence — exactly the
    lifecycle IDEA.md *Adopted now* describes.
    """

    item, _meta = generate_item(
        pack,
        phase=phase,
        goal=goal,
        extras=extras,
        node_title=node_title,
        depth=depth,
        corpus_context=corpus_context,
        avoid=avoid,
        surface_form=surface_form,
    )
    created = learner.post(
        "/v1/items",
        {"node": node_id, "spec": item.spec(), "author": author},
        idempotency_key=idempotency_key,
    )
    item_id = created.get("item_id")
    notes: list[str] = []
    attempts = 0

    while attempts < 2:
        attempts += 1
        verdict, note = blind_solve(item)
        if verdict is None:
            notes.append(note)
            break
        agreed = _letter(verdict.answer) == _letter(item.answer) and not verdict.ambiguous
        if agreed:
            validation = learner.post(
                f"/v1/items/{item_id}/validate",
                {
                    "by": "blind-solver",
                    "result": "pass",
                    "notes": f"solver picked {verdict.answer!r}, matched the key, no "
                    "ambiguity flag",
                    "evaluation_method": "blind_solver",
                },
            )
            return {
                "item_id": item_id,
                "item_version_id": created.get("item_version_id"),
                "status": validation.get("status", "PRACTICE_EVIDENCE"),
                "spec": item.spec(),
                "explanation": item.explanation,
                "validated": True,
                "solver": verdict.model_dump(),
                "notes": notes,
                "regenerated": attempts > 1,
            }
        notes.append(
            f"solver picked {verdict.answer!r}"
            + (" and flagged AMBIGUOUS" if verdict.ambiguous else "")
            + f" against key {item.answer!r}"
        )
        learner.post(
            f"/v1/items/{item_id}/validate",
            {
                "by": "blind-solver",
                "result": "fail",
                "notes": notes[-1],
                "evaluation_method": "blind_solver",
            },
        )
        if attempts >= 2:
            break
        # regenerate once, as a new version of the same item
        item, _meta = generate_item(
            pack,
            phase=phase,
            goal=goal,
            extras=extras,
            node_title=node_title,
            depth=depth,
            corpus_context=corpus_context,
            avoid=(avoid or []) + [item.stem],
            surface_form=surface_form,
        )
        created = learner.post(
            "/v1/items", {"node": node_id, "spec": item.spec(), "author": author, "item": item_id}
        )

    return {
        "item_id": item_id,
        "item_version_id": created.get("item_version_id"),
        "status": created.get("status", "TEACHING_ONLY"),
        "spec": item.spec(),
        "explanation": item.explanation,
        "validated": False,
        "solver": None,
        "notes": notes,
        "regenerated": attempts > 1,
    }


# --------------------------------------------------------------------------- teaching


def generate_teach_step(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    strategy: str,
    node_state: dict[str, Any] | None,
    corpus_context: str | None = None,
) -> tuple[TeachStep, Any]:
    """One reasoning step, in the strategy the orchestrator already chose.

    The strategy is an **input**, not a decision the model makes: expertise reversal is a
    rule (orchestrator.choose_strategy), and handing it to the model as a free choice is
    how "you are a visual learner" gets back in.
    """

    state_line = json.dumps(
        {
            k: v
            for k, v in (node_state or {}).items()
            if k
            in (
                "state",
                "independent_passes",
                "assisted_passes",
                "fails",
                "uncertainty",
                "active_misconception",
            )
        }
    )
    task = (
        f"Teach ONE reasoning step of '{node_title}' using the '{strategy}' strategy, "
        "which has already been chosen for you by the learner's evidence - do not pick a "
        "different one and do not mention learning styles. Concrete instance first, "
        "abstract form second. At most two new symbols or terms. End with the "
        "self-explanation prompt in `self_explanation_prompt`. Put a Mermaid diagram in "
        "`mermaid` ONLY if removing it would lose a relationship the prose has to spell "
        "out; otherwise leave it null. Put display mathematics in `latex` (without the "
        "$$ delimiters) if there is any. Never state or hint at the answer to the "
        "checkpoint that follows.\n\n"
        f"The learner's evidence on this node: {state_line}"
    )
    composed = pack.compose(
        "teach-step",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        corpus_context=corpus_context,
        task=task,
    )
    return _generate(composed, TeachStep)


def generate_interrupt_answer(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    strategy: str,
    step_markdown: str,
    question: str,
    checkpoint_stem: str = "",
    corpus_context: str | None = None,
) -> tuple[InterruptAnswer, Any]:
    """Answer one mid-step "wait, why?" in the context of the step already given.

    teach-step.md *Interrupts mid-step*: "answered inline in at most five lines, adds no
    node, opens no tangent, and then you **resume the same step from where it stopped**".
    The step it resumes is passed in rather than regenerated — the learner is reading the
    markdown the gateway already served, and a second generation would answer a question
    about a step that was never shown.
    """

    task = (
        "The learner interrupted the step below to ask a question. Answer it inline, in at "
        "most five lines, in the context of that step. Do not restart or re-explain the "
        "step, do not open a tangent, do not introduce a new concept as a new topic, and "
        "do not summarise what comes next.\n\n"
        "You may NOT state, hint at, or narrow down the answer to the checkpoint that "
        "follows the step — not even if the question circles it. If answering honestly "
        "would give the checkpoint away, answer the part that does not and say plainly "
        "that the rest is what the checkpoint is for.\n\n"
        "If the question reveals a missing prerequisite, say so in one line rather than "
        "teaching over the hole.\n\n"
        f"The step the learner is reading (strategy: {strategy}):\n\n{step_markdown}\n\n"
        + (f"The checkpoint that follows (do not answer it):\n\n{checkpoint_stem}\n\n"
           if checkpoint_stem else "")
        + f"The learner's question:\n\n{question}"
    )
    composed = pack.compose(
        "teach-step",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        corpus_context=corpus_context,
        extra_user_blocks=[f"## Node\n\n{node_title}"],
        task=task,
    )
    return _generate(composed, InterruptAnswer, cache=False)


#: checkpoint.md's fixed openers. The frame is fixed so an assistance level means the same
#: thing across sessions and harnesses; the content inside it is the model's.
HINT_FRAMES = {
    1: "Encouragement only. No content: you may not name the concept, the method, or the "
    "first step.",
    2: "Conceptual hint: name the idea that applies. Not the method, not the step.",
    3: "Strategic hint: name the approach to take. Not the first step's result.",
    4: "Procedural hint: give the first step, but never its result.",
    5: "Partial solution: most of it, with exactly one gap left for the learner.",
    6: "Worked solution (the reveal). This is recorded as a non-pass.",
}


def generate_hint(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    stem: str,
    options: list[str],
    level: int,
    attempts: int,
) -> tuple[HintText, Any]:
    frame = HINT_FRAMES.get(level, HINT_FRAMES[1])
    task = (
        f"The learner has made {attempts} attempt(s) on this checkpoint and is at hint "
        f"level {level}.\n\n{frame}\n\nCheckpoint stem:\n{stem}\n\nOptions:\n"
        + "\n".join(options)
        + f"\n\nWrite the level-{level} hint and nothing else. Do not exceed the level."
    )
    composed = pack.compose(
        "checkpoint",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        extra_user_blocks=[f"## Node\n\n{node_title}"],
        task=task,
    )
    return _generate(composed, HintText, cache=False)


def generate_feedback(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    stem: str,
    options: list[str],
    response: str,
    correct: bool,
    assistance_level: int,
    explanation: str = "",
    reveal_allowed: bool = False,
) -> tuple[Feedback, Any]:
    """Feedback after an attempt. Never contains the key unless the reveal is allowed."""

    task = (
        f"The learner answered {response!r}. That is "
        + ("correct" if correct else "not correct")
        + f". Highest assistance level reached: {assistance_level}.\n\n"
        + (
            "You MAY state the correct answer and why: an attempt has been made and the "
            "reveal is allowed.\n"
            if reveal_allowed and not correct
            else "You may NOT state or hint at the correct answer.\n"
        )
        + (
            "Say plainly that a pass at assistance level 5 or 6 is recorded but does not "
            "count toward mastery.\n"
            if correct and assistance_level >= 5
            else ""
        )
        + "Two or three sentences, plain language, no praise for effort that produced "
        "nothing.\n\n"
        f"Stem:\n{stem}\n\nOptions:\n" + "\n".join(options)
        + (f"\n\nWhy the key is right (for your use): {explanation}" if explanation else "")
    )
    composed = pack.compose(
        "checkpoint",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        extra_user_blocks=[f"## Node\n\n{node_title}"],
        task=task,
    )
    return _generate(composed, Feedback, cache=False)


def grade_teach_back(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    explanation: str,
) -> tuple[TeachBackGrade, Any]:
    """Score 0–3 against the frozen anchors in ``teach-back-rubric.md``.

    Recorded by learner-svc as ``evaluation_method: rubric`` with the rubric version this
    pack declares — a frozen versioned artefact is exactly what that method names.
    """

    task = (
        f"Grade this teach-back of '{node_title}' against the rubric above. Use the "
        "anchors as written; put the anchor you matched in `anchor` (for example "
        "'2 - Correct but bounded'). `feedback_markdown` is at most two sentences: what "
        "was right, and the one thing missing. Never read the rubric out loud and never "
        "give a percentage.\n\n"
        f"The learner's explanation:\n\n{explanation}"
    )
    composed = pack.compose(
        "teach-back",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        task=task,
    )
    return _generate(composed, TeachBackGrade, cache=False)


def generate_misconception_probe(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    claim: str,
    step: str,
    history: list[dict[str, Any]] | None = None,
) -> tuple[MisconceptionProbe, Any]:
    task = (
        f"Node: '{node_title}'. Suspected belief: {claim!r}. You are running step "
        f"'{step}' of the three-step confirmation.\n\n"
        "Write the exact thing to say to the learner for this step, per the protocol "
        "above. Do not tell the learner they have a misconception. Do not skip ahead.\n\n"
        f"Steps run so far: {json.dumps(history or [])}"
    )
    composed = pack.compose(
        "misconception",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        task=task,
    )
    return _generate(composed, MisconceptionProbe, cache=False)


def judge_misconception_step(
    pack: PromptPack,
    *,
    goal: dict[str, Any],
    extras: dict[str, Any] | None,
    node_title: str,
    claim: str,
    step: str,
    learner_response: str,
) -> tuple[MisconceptionStepOutcome, Any]:
    """Did this step's evidence match the suspected belief?

    The *sequence* is code (orchestrator.misconception_next_step); this is the one thing
    only a reader of free text can do — say whether what the learner wrote is the belief
    or something else. misconception.md is explicit that "dropped" is the common case, and
    the default on a failure to decide is ``dropped``: a hypothesis dies cheaply, and a
    belief recorded on thin evidence is exactly the noise the protocol exists to prevent.
    """

    task = (
        f"Node: '{node_title}'. Suspected belief: {claim!r}. Step: '{step}'.\n\n"
        f"The learner said:\n\n{learner_response}\n\n"
        "Decide `held` (what they said IS the suspected belief, applied) or `dropped` "
        "(they meant something else, misread, guessed, or - at the counterexample step - "
        "accepted the counterexample and restated the correct model). When in doubt, "
        "`dropped`. `reply_markdown` is what to say to the learner now: at steps "
        "reasoning and prediction, never tell them they have a misconception."
    )
    composed = pack.compose(
        "misconception",
        domain=(extras or {}).get("domain") or goal.get("domain"),
        goal_block=goal_block(goal, extras),
        task=task,
    )
    return _generate(composed, MisconceptionStepOutcome, cache=False)


# --------------------------------------------------------------------- render loop


def render_diagram(
    mermaid: str | None,
    render: Any,
    *,
    pack: PromptPack | None = None,
    goal: dict[str, Any] | None = None,
    extras: dict[str, Any] | None = None,
    theme: str = "default",
) -> dict[str, Any]:
    """write → check → fix once → render. Degrades to "not rendered", never to an error.

    render-svc is optional: with ``LT_RENDER_URL`` unset the step still teaches, it just
    ships the Mermaid source unrendered. A diagram that will not parse after one fix is
    dropped rather than shown broken.
    """

    if not mermaid:
        return {"mermaid": None, "svg": None, "checked": False, "errors": [], "fixed": False}
    if not getattr(render, "configured", False):
        return {
            "mermaid": mermaid,
            "svg": None,
            "checked": False,
            "errors": [],
            "fixed": False,
            "skipped": "LT_RENDER_URL is not set; the diagram is not checked or rendered",
        }

    fixed = False
    try:
        check = render.check(mermaid)
    except Exception as exc:
        return {
            "mermaid": mermaid,
            "svg": None,
            "checked": False,
            "errors": [{"message": str(exc)}],
            "fixed": False,
            "skipped": f"render-svc unreachable: {exc}",
        }

    if not check.get("ok") and pack is not None:
        errors = check.get("errors") or []
        task = (
            "This Mermaid diagram does not parse. Fix it and return the corrected source "
            "only. Keep the same nodes and relationships.\n\n"
            f"Errors: {json.dumps(errors)}\n\n```mermaid\n{mermaid}\n```"
        )
        composed = pack.compose(
            "teach-step",
            domain=(extras or {}).get("domain") or (goal or {}).get("domain"),
            goal_block=goal_block(goal or {}, extras),
            task=task,
        )
        try:
            repaired, _meta = _generate(composed, MermaidFix, cache=False)
            if repaired.mermaid.strip():
                candidate = repaired.mermaid
                recheck = render.check(candidate)
                if recheck.get("ok"):
                    mermaid, check, fixed = candidate, recheck, True
                else:
                    check = recheck
        except Exception as exc:  # the fix is best-effort by design
            check = {"ok": False, "errors": [{"message": f"fix attempt failed: {exc}"}]}

    if not check.get("ok"):
        return {
            "mermaid": None,
            "svg": None,
            "checked": True,
            "errors": check.get("errors") or [],
            "fixed": fixed,
            "skipped": "the diagram did not parse after one fix and was dropped",
        }

    try:
        rendered = render.render(mermaid, theme=theme)
    except Exception as exc:
        return {
            "mermaid": mermaid,
            "svg": None,
            "checked": True,
            "errors": [{"message": str(exc)}],
            "fixed": fixed,
        }
    return {
        "mermaid": mermaid,
        "svg": rendered.get("svg"),
        "checked": True,
        "errors": [],
        "fixed": fixed,
    }


def check_latex(latex: str | None, render: Any) -> dict[str, Any]:
    """``/latex/check``. Unchecked (render-svc unset) reports ``ok`` with ``checked: False``."""

    if not latex:
        return {"ok": True, "checked": False, "errors": []}
    if not getattr(render, "configured", False):
        return {"ok": True, "checked": False, "errors": []}
    try:
        result = render.latex_check(latex)
    except Exception as exc:
        return {"ok": True, "checked": False, "errors": [{"message": str(exc)}]}
    return {"ok": bool(result.get("ok")), "checked": True, "errors": result.get("errors") or []}
