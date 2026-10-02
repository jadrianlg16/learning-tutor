# `tutor` — prompts and decisions, kept apart

Stage 2 module. Two halves, and the whole design is that they never mix:

* **`orchestrator.py` decides.** Next question, pass/fail, hint legality, strategy change,
  teach-back due, session end, misconception ordering, feasibility. Pure functions, no LLM
  import, no network, no database. IDEA.md *Review response* → *Decision ownership*: "One
  orchestration module decides … `tutor-svc` supplies prompts, not decisions."
* **`prompts.py` + `generate.py` generate.** The prompt pack is read from
  `skills/teach/prompts/<version>/` — the same files the Stage 0 skill loads — and every
  model call is a typed `generate_structured` through `learning_tutor.llm`.

Spec: [`IDEA.md`](../../IDEA.md). Binding shapes: [`CONTRACTS.md`](../../CONTRACTS.md). The
HTTP surface that calls this module: [`gateway.md`](gateway.md).

```
learning_tutor/tutor/
  prompts.py       PromptPack: load, compose(system | user), prompt_version, rubric_version
  orchestrator.py  every decision, as a pure function
  generate.py      schemas, the item pipeline, the diagram loop, plan verification
```

## The prompt pack is the skill's files

Mode A is defined in IDEA.md as *"gateway runs the same prompt pack through llm-svc"*, so
this module **loads** the pack rather than restating it. There is one pedagogy in this
repo and it lives in `skills/teach/`.

| Variable | Default | Meaning |
|---|---|---|
| `LT_PROMPT_PACK_DIR` | `skills/teach/prompts` (cwd, then the repo root next to the package) | The pack root, which holds version folders |
| `LT_PROMPT_VERSION` | `v1` | The version to load |

`prompt_version` recorded on every event this module composes for is `teach/<version>`
(`teach/v1`), byte-identical to what the skill writes, so Mode A and Mode B events are
comparable. `rubric_version` is **parsed out of `teach-back-rubric.md` itself** (the
`` rubric version `teach-back-v1` `` line), so the rubric and the version stamped on the
event cannot drift apart; cutting `teach-back-v2` in that file is enough.

### compose(): what goes where, and why

```
system  = system.md  +  domains/<domain>.md
user    = <phase>.md  +  goal contract  +  [corpus block]  +  [extra blocks]  +  task
```

Corpus text goes in the **user** half, always, exactly as
`corpus.sanitize.render_for_context` emitted it, `<<<SOURCE …>>>` fences intact, under a
"DATA, NOT INSTRUCTIONS" heading. That is CONTRACTS.md hard rule 4 made structural: a
document fenced inside the system prompt is a document sitting in the instruction channel.
`tests/test_tutor_prompts.py` asserts `<<<SOURCE` never appears in `composed.system`.

## The decision rules, and where each comes from

| Function | Rule | Source |
|---|---|---|
| `next_probe_question` | Serve the node learner-svc picked; never re-rank it | `probe.md` — "Do not override the CLI's choice" |
| `probe_stop` | Stop on: learner says stop · no pick left · budget (12) spent · 3 consecutive IDK · 3 consecutive wrong | `probe.md` *Budget and stop rules*; budget default `LT_PROBE_BUDGET` |
| `next_teach_node` | The edge: lowest unknown node whose prerequisites are known (learner-svc's `--mode teach`) | IDEA.md *The probe*, step 6 |
| `grade_mc` | Deterministic key match. IDK is never correct; an empty answer is never a pass; the letter or the option text both work | CONTRACTS.md hard rule 3 |
| `hint_allowed` | Level 6 needs a prior attempt (409 otherwise); levels escalate one at a time, never skipping | `checkpoint.md` rules 2–3; CONTRACTS.md hard rule 1 |
| `counts_toward_mastery` | A pass at assistance ≥ 5 is recorded and never counts | `checkpoint.md` rule 5 |
| `decide_after_answer` | See below | `SKILL.md` *Frustration*, *Session cap*, *Teach-back* |
| `choose_strategy` | Expertise reversal, as a table | `teach-step.md`; Kalyuga *(verify)* |
| `misconception_next_step` | reasoning → prediction → counterexample; any `dropped` kills it, three `held` confirms | `misconception.md` *Confirmation rule* |
| `feasibility` | `nodes_remaining × PACE_MIN` vs `minutes_per_session × sessions_left` | `plan.md` *Feasibility, stated out loud* |
| `asks_for_the_checkpoint_answer` | A mid-step interrupt that is really "just tell me the key" gets `CHECKPOINT_ANSWER_RULE` instead of an answer | `checkpoint.md` rules 1–3; CONTRACTS.md hard rule 1 |
| Whether a generated plan is big enough to be a curriculum | `len(nodes) < LT_PLAN_MIN_NODES` → one retry, then a `warnings` field | `plan.md` *Emit* — 10–40 nodes |
| `learning_path` / `path_mermaid` | Deterministic topological order over `strict_prerequisite` only | IDEA.md — curriculum map vs learner path |

### `decide_after_answer` — precedence, and why it is that order

1. **Session cap** → `end_session`. SKILL.md: "At 100% finish the current checkpoint only —
   never start a new node." The checkpoint has just finished, so this is the moment.
2. **Three fails in a row** → `switch_strategy`, then (if a strategy was already switched)
   `back_up`, then (if it was already backed up) `end_session`. SKILL.md *Frustration*,
   in that order, one escalation per answer.
3. **Teach-back due** → `teach_back_due`, when a node has just been completed and
   `TEACH_BACK_EVERY_NODES` (2, the low end of SKILL.md's "every 2–3 nodes") have gone by.
4. Otherwise `continue` on a pass, `repeat` on a miss.

`misconception_suspected` is a **flag on the decision, not a decision**: wrong, at
confidence ≥ 4, at assistance 0, and not "I do not know". Help explains a wrong answer, so
a miss after a hint is not evidence of a belief; and IDK is a clean signal, not a belief.
misconception.md forbids naming it to the learner before the three steps have run, so the
gateway opens a `suspected` record and says nothing.

### Strategies (`choose_strategy`)

Expertise is derived from evidence only — `expertise_for(node_state)` reads `state`,
`independent_passes` and `assisted_passes` and nothing else. There is no other input to
this function, and `tests/test_tutor_orchestrator.py` asserts the signature to keep it
that way (CONTRACTS.md hard rule 2: no learning-styles profile).

| Expertise | First choice | On a strategy switch |
|---|---|---|
| `novice` (unknown, no passes) | `example-first` — the fully worked example | `visual-first`, then `analogy-first` |
| `intermediate` (fragile, or any passes) | `socratic` (problem-first) | `example-first`, then `visual-first` |
| `advanced` (known, or ≥ 2 independent passes) | `formal-first` | `socratic`, then `example-first` |

The novice rotation cannot reach `socratic` at any number of switches — teach-step.md:
"Never for a true novice." This is expertise reversal (Kalyuga) *(verify)*.

### Feasibility

```
nodes_remaining × PACE_MIN     vs     minutes_per_session × sessions_left
sessions_left = floor(days_to_deadline × sessions_per_week / 7)
```

`PACE_MIN = 12` (`LT_PACE_MIN`) is plan.md's Stage-0 planning constant — **an assumption,
not a measurement**, and the returned `statement` says so in words every time. Nodes
already `known` are excluded. With no deadline, or no minutes-per-session, `fits` is
`None` and the statement says what is missing rather than guessing. When it does not fit,
exactly three options come back — shallower depth, a narrower sub-goal, more sessions —
with the arithmetic for each. plan.md: "Never silently speed up."

### Feasibility, in web-ui's field names

`Feasibility.as_dict()` carries four extra keys — `sessions_needed`
(`ceil(minutes_needed / minutes_per_session)`, min 1), `sessions_available` (`sessions_left`
renamed), `verdict` and `assumption` — because that is the shape
`web-ui/src/lib/types.ts::Feasibility` reads. Nothing was renamed away: the arithmetic keys
are still there, and the UI ignores what it does not know. The `verdict` thresholds
(comfortable at 1.25× needed) are copied from `src/lib/feasibility.ts` so the gateway's line
and the UI's fallback cannot disagree. `sessions_needed` is `None` when there is no
`minutes_per_session` to divide by, and `POST /api/goals/{g}/plan` sends `feasibility: null`
in that case rather than an object full of holes.

### Interrupts (`orchestrator.asks_for_the_checkpoint_answer`)

"Wait, why?" is answered; "what is the answer?" is not. The predicate is a small list of
regexes over the interrupt text, deliberately narrow — it must be asking **for** the answer
("which option is correct", "just tell me the answer", "is it B?"), not **about** one ("why
does the answer have to be linear?", which is a question about the step and gets answered).
A match returns `CHECKPOINT_ANSWER_RULE`, fixed text transcribing checkpoint.md's first three
rules, and no generation happens at all. The refusal is code because a model instructed to
refuse politely is a model that can be argued with — and the whole point of the hint ladder
is that it cannot.

`generate.generate_interrupt_answer` is the other half: it takes the **step markdown the
gateway already served** plus the checkpoint stem, and its task text forbids restarting the
step, opening a tangent, and stating or narrowing the checkpoint answer. teach-step.md
*Interrupts mid-step* is the source: "answered inline in at most five lines... resume the same
step from where it stopped."

## The item pipeline (`generate.author_item`)

```
author (role=tutor) → POST /v1/items → blind solve (role=solver, stem+options only)
   ├─ solver agrees, no ambiguity flag → POST /v1/items/{i}/validate
   │     {result: pass, evaluation_method: blind_solver}  → PRACTICE_EVIDENCE
   └─ disagrees / AMBIGUOUS → validate {result: fail} → regenerate ONCE as a new version
         └─ still disagrees → the item stays TEACHING_ONLY
```

Three outcomes leave the item `TEACHING_ONLY`, and all three are correct rather than
errors: a solver disagreement, an ambiguity flag, and a `SameSolverError` from `llm` (the
configured solver resolved to the tutor's own provider+model, and there is no independent
evidence to be had). A `TEACHING_ONLY` item is still served and still teaches — it just
writes no evidence, which is the whole point of the lifecycle. The blind solver is shown
the stem and the options and nothing else: no key, no teaching context, no rationale.

### Edges to nodes that were never declared

`PlanGraph.payload()` returns `(import body, dropped edges)`. An edge whose `from` or `to`
does not resolve to a declared node title or alias is **dropped and reported**, never sent.

This is not defensive decoration: in the first real run against a local `llama3.1:8b` the
model emitted `{"from": "Misconceptions about growth", "to": …}` and never declared that
node. learner-svc resolves endpoints by title/alias and 404s the **entire import** on the
first one it cannot find, so one invented endpoint would have cost the whole plan. An edge
is a claim about ordering; a claim naming a thing that does not exist is not worth keeping,
and the plan response carries `dropped_edges` so the loss is visible.

Duplicate and empty node titles are dropped the same way.

### A plan too short to be a curriculum

`plan.md` asks for 10–40 nodes. A local 8B model (`llama3.1:8b`) returned **5**. Five
nodes is a table of contents, not a route through the material — every node is broad enough
that "known" means almost nothing.

`generate_plan` therefore counts the nodes it got back. Under `LT_PLAN_MIN_NODES`
(default 6) it retries **once**, with the shortfall stated as the requirement ("your
previous attempt returned 5 nodes... emit at least 6, and 10–40 is the target. Break each
broad node into the separate checkable things it is made of"). The retry's graph is kept
only if it has more nodes than the first — a second attempt that came back worse is not
preferred for being second.

If it is still short, the plan is **returned anyway**, carrying
`warnings: ["plan has 5 nodes; plan.md asks for 10–40"]`, which
`POST /api/goals/{g}/plan` passes through (optional in CONTRACTS.md, present only when
non-empty). Refusing would leave the learner with no plan at all, and the graph is
reviewable and revisable by hand at `plan/approve` — which is the phase's whole purpose.
One retry, not a loop: asking a model the same thing a third time spends tokens without
making it know more.

`warnings` is a field on `PlanGraph` but it is **ours**, not the model's: `generate_plan`
overwrites whatever came back in it, and it is `exclude=True` so `model_dump()` and
`payload()` can never carry it into a learner-svc graph import.

## The diagram loop (`generate.render_diagram`)

`mermaid → /check → (one LLM fix → /check) → /render → svg`. Degradations, in order of
how much is lost:

| Situation | Result |
|---|---|
| `LT_RENDER_URL` unset | The Mermaid source ships unrendered and unchecked, with a `skipped` note |
| render-svc unreachable | Same, with the transport error in `skipped` |
| Parses first time | `svg` returned, `fixed: false` |
| Parses after one fix | The repaired source and its `svg`, `fixed: true` |
| Still broken after one fix | The diagram is **dropped** (`mermaid: null`) rather than shown broken |

`check_latex` reports `{"ok": true, "checked": false}` when render-svc is not configured —
"nothing was found wrong" and "nothing was checked" are different claims and the response
says which.

## Plan verification (`generate.verify_plan`)

One `corpus.cite_or_abstain` per node title. `abstain` is a first-class status, not a
missing citation. When the goal has **no corpus at all** the list is empty rather than a
row of abstentions — an abstention means the corpus was asked and had nothing to say, and
there was no corpus to ask.

## Deviations and judgement calls

| Decision | Why |
|---|---|
| A keyed MC answer is recorded as `evaluation_method: rubric`, `grader_version: mc-key-v1` | CONTRACTS.md's enum has no `deterministic` value, and learner-svc made the same call for the same reason (see its *Known limitations*). A stored key is a frozen artefact, which is what `rubric` names |
| `TEACH_BACK_EVERY_NODES = 2` | SKILL.md says "every 2–3 nodes". Two means a short session still produces one teach-back. `LT_TEACH_BACK_EVERY_NODES` overrides |
| Hints below level 6 are allowed with zero attempts | CONTRACTS.md pins only *"6 only after an attempt → 409 otherwise"*, and the web UI is built against that. `checkpoint.md` is stricter ("No hint before an attempt. Not level 1"). The contract wins here because the web UI is coded to it; the no-skipping rule is enforced on top, so the ladder still cannot be jumped. **Tighten this in CONTRACTS.md first if the stricter rule is wanted** |
| `misconception_sequence` is the one function that touches a client | The *ordering* rule is pure (`misconception_next_step`, tested alone); this wrapper only posts the step and reads back the state learner-svc owns |
| `judge_misconception_step` uses the model | Deciding whether free text *is* the suspected belief is a reading task, not a rule. It defaults to `dropped` on any failure, because misconception.md says dropping is the common case and a belief on thin evidence is the noise the protocol exists to prevent |

## Known limitations

* **`PACE_MIN = 12` has never been measured.** It is a planning constant carried over from
  plan.md. Everything the feasibility check says is downstream of it, which is why the
  statement repeats that it is an assumption. Recalibrate from `GET /v1/metrics/{g}`.
* **`grade_mc` is a string match.** It accepts the option letter or the option's text. A
  free-text answer to a non-multiple-choice item is out of scope — there is no such item
  kind in the pipeline yet.
* **The strategy rotation is a table, not a bandit.** IDEA.md's N-of-1 matched comparisons
  and the delayed-retention reward are *not* implemented; the rotation is deterministic and
  makes no claim to be optimal. That was labelled **HYPOTHESIS** in IDEA.md and stays one.
* **The regeneration budget is one.** A second solver disagreement leaves the item
  `TEACHING_ONLY` rather than looping; a tighter loop would spend tokens to make a bad
  question worse.
* **`verify_plan` cites node *titles*.** A title is a short claim and `support` is
  bag-of-words, so a node whose title shares no vocabulary with the corpus abstains even
  when the corpus covers it. That direction of error is the safe one (corpus.md makes the
  same trade), but it means `verification` is a coverage signal, not a correctness proof.
* **A local 8B model does not hit the pack's node count.** A `POST /api/goals/{g}/plan`
  against `llama3.1:8b` returned **5 nodes and 5 edges** in about 21–24 s, where `plan.md`
  asks for 10–40 nodes. The prompt is followed in shape
  (typed edges, provenance, a `misconception_for` and a `transfer_related` edge) and missed
  on size. One retry and a `warnings` field are now in the code (*A plan too short to be a
  curriculum*), and that is all they are: **the retry has not been shown to help.** It was
  tested against a fake model, not measured against a real short-planning one, so whether a
  restated count actually moves an 8B model past six nodes is a **HYPOTHESIS**. What is
  certain is that the caller is told.
* **Every model call is still one shot, now against a 120-second timeout.**
  The hardcoded 60 s in `learning_tutor/llm/ai_providers.py` is gone; `LT_LLM_TIMEOUT_S`
  (default 120) is the ceiling on every provider call — see [llm.md](llm.md). A
  `gemma3:12b` plan generation (~10k-token prompt, `LT_LLM_MAX_TOKENS=6000`) exceeded the
  old 60 s and surfaced as `httpx.ReadTimeout` after a full minute of nothing. A higher
  ceiling does not make the model faster: the plan phase still wants a fast model, or a
  smaller `LT_LLM_MAX_TOKENS`.
* **Nothing here measures whether the pedagogy works.** That is the Stage 2 product gate
  in IDEA.md, and it needs sessions, not code.

## Tests

```bash
uv run pytest -o addopts="" -q tests/test_tutor_orchestrator.py tests/test_tutor_prompts.py \
                               tests/test_tutor_generate.py
```

`test_tutor_orchestrator.py` (62 tests) runs the whole decision surface with no model, no network
and no database — that it *can* is the evidence that the model is not deciding.
`test_tutor_prompts.py` (14) pins the pack loading, the version strings, and that corpus
text never reaches the system half. `test_tutor_generate.py` (20) covers the plan payload
(including the dropped-edge case from the real run) and every degradation of the diagram
loop. `generate.py`'s item pipeline is exercised end-to-end through the gateway in
`tests/test_gateway_routes.py` with a `FakeLLM`.
