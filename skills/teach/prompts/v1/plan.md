# plan.md — the plan phase (`teach/v1`)

The graph's stated purpose is to show the learner what is coming. Its real purpose is to stop
you winging it. Commit to a structure before you teach a single step.

## Inputs, in priority order

1. **Course structure from `data/sources/<goal>/`**, if documents exist — chapter order, slide
   sequence, headings, the order worked examples appear in. This is an *alignment* prior: it
   fixes scope, notation and sequence, because it is the route the exam assumes. Emit these as
   `course_sequence` edges with `provenance: course`.
2. **Prerequisite structure you know** — the real dependencies, regardless of the course order.
   `strict_prerequisite` edges with `provenance: model` (or `reference` if a named authority
   source states it).
3. **The goal contract** — depth, purpose and assessment format decide where the graph *stops*.
   "Recognize" needs fewer nodes than "apply"; "pass this exam" prunes anything the syllabus
   does not touch; "build this" keeps the implementation nodes a reading goal would drop.
4. **Existing learner state** — one graph, many goals. Nodes the learner already has from other
   goals stay in the graph, marked known; do not re-derive them under new names. Use `aliases`
   so "dual vector", "covector" and "1-form" resolve to one node.

## Node rules

- **10–40 nodes.** Under 10 and you are hand-waving; over 40 and the probe cannot cover it and
  the plan is a syllabus, not a path.
- One node = one thing that can be **checked with a single item**. If you cannot write a
  checkpoint item for it, it is not a node — it is a chapter, so split it.
- Node titles are what the learner would call them, in the notation the alignment sources use.
- `aliases` carry every other name in play, including the textbook's and the course's.
- Nodes get ids from the CLI (`n_<slug>_<4hex>`). You supply titles; you never invent ids.

## Edge rules

| Type | Means |
|---|---|
| `strict_prerequisite` | B is not learnable before A. The teaching order constraint |
| `recommended_background` | Easier with A, possible without |
| `course_sequence` | The course does A before B. Alignment, not logic |
| `co_requisite` | A and B only make sense together; teach as a pair |
| `supports` | A makes B stick — an analogy anchor, a special case |
| `misconception_for` | A is a known wrong model people bring to B |
| `transfer_related` | Same structure, different surface. Where transfer items come from |

Provenance on every edge: `course, reference, model, learner_evidence, human`. Do not label a
model-derived edge as `course` because it feels authoritative.

Add at least one `misconception_for` node per domain with strong wrong intuitions (physics,
probability, statistics, anything with an everyday word that means something else technically).
Add at least two `transfer_related` edges — these are where the delayed-transfer metric lives.

## Emit

`data/tmp/graph.json`:

```json
{
  "nodes": [
    {"title": "Covectors", "aliases": ["dual vectors", "1-forms", "linear functionals"]}
  ],
  "edges": [
    {"from": "Dual space", "to": "Covectors", "type": "strict_prerequisite", "provenance": "model"}
  ]
}
```

`from`/`to` may use a title or an alias; the CLI resolves them to ids and versions the write.

```bash
learner graph import --goal "$GOAL" --file data/tmp/graph.json
learner graph show --goal "$GOAL" --format mermaid
```

## Manual review — required at Stage 0, not optional

Show the mermaid. Ask exactly these four, numbered, and wait:

1. Anything here you already know cold?
2. Anything missing that your course or exam covers?
3. Any arrow wrong — does A really need B first?
4. Anything here **not** on your exam, or not needed for your purpose?

Then revise. `data/tmp/ops.json` carries split / merge / add / remove operations; the CLI
migrates existing evidence across the revision and bumps the graph version.

```bash
learner graph revise --goal "$GOAL" --ops data/tmp/ops.json
learner graph show --goal "$GOAL" --format mermaid       # show the revised graph again
```

Answers to (1) do **not** mark nodes known — they open `I already know this` disputes and go
through the two-item check in `probe.md`. Answers to (3) and (4) are the learner's call and are
applied directly; they are the domain expert on their own exam.

Loop review -> revise until the learner says the graph is right. Do not start the probe on an
unreviewed graph.

## Feasibility, stated out loud

```
nodes_remaining x PACE_MIN   vs   minutes_per_session x sessions_left
```

`PACE_MIN = 12` minutes per node is a Stage-0 planning constant — an assumption, not a
measurement. Recalibrate from `learner metrics --goal G` once real sessions exist, and say which
one you are using.

If it does not fit, show the arithmetic and offer exactly three options:

- **Shallower depth** — drop the Bloom level (apply -> explain) and say what that costs: they
  will be able to explain it and not to do it.
- **A narrower sub-goal** — a subtree of the graph, finished properly.
- **More sessions** — the honest number.

Never silently speed up. Speed is the mastery bar, not a slider; lowering the bar is a decision
the learner makes explicitly, having been told it is riskier.
