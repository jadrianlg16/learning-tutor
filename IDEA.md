# Learning Tutor — one-to-one AI teaching

Status: Built 2026-09-05 — Stages 0–2 code complete and tested; pedagogical gates not yet passed by a real learner (see README)
Category: AI / Learning
Last updated: 2026-09-03

## Concept

A single AI teacher that keeps an evidence-backed, uncertainty-aware estimate of what you can
currently do, plans a path from there to what you want to understand, and walks it one reasoning
step at a time — quizzing as it goes so neither of you can bluff.

Three phases:

1. **Probe** — graded multiple-choice questions, starting broad, then binary-searching the edge of
   your understanding on every strand the lesson depends on. Output: a map of what you actually
   know.
2. **Plan** — reason the whole arc out, run independent verification/fact-check passes, emit a
   Mermaid dependency graph. The graph's stated purpose is to show you what's coming. Its *real*
   purpose is to stop the model winging it.
3. **Teach** — walk the graph one node at a time, deliberately slow, with generated visuals and a
   checkpoint quiz after each step.

Plus an optional **grounding** step, once per goal, before the probe: your slides, PDFs and notes
and an approved research pass become the corpus the plan aligns to and the tutor cites. Without
it the tutor teaches from what the model already knows — fine for canonical topics, wrong for
your professor's notation.

## Where this came from

A video essay whose argument is worth keeping intact, because the argument *is* the product.

Normal learning is **many-to-many** — each outlet teaches many students, each student uses many
outlets — and both directions leak.

- **One outlet, many students.** Content built for everyone is optimal for no one. Optimal
  teaching depends entirely on the learner's current state: which path, and how each step is
  phrased. The ideal path teaches nothing you already hold and nothing you can't yet reach — it
  runs along the edge of your understanding.
- **One student, many outlets.** Switching between styles, notations and interfaces costs effort
  that never reaches the material. The deeper cost is **trust**: the brain hedges on an unfamiliar
  source. The same explanation lands better from a channel you already trust.

Objection: one teacher means one perspective. Answer: that conflates a **source** with an
**interface**. The teacher doesn't reduce the number of sources — it aggregates them and delivers
through one interface. And with AI, trust isn't earned over months; it's **engineered**, via
verification and fact-checking.

The two leaks give the two design principles:

1. **Optimized teaching** — teach at the edge, always.
2. **Optimized allocation of mental resources** — *not* less difficulty. Maximum struggle, all of
   it concentrated in the material itself. Logistics, sequencing, resource hunting and
   fact-checking are the system's job to absorb.

## The honest problem (devil's advocate — read this first)

**The source system is a skill file, two extensions and two helper agents inside a harness the
video's author already had.** Call it 500 lines of markdown and glue. Everything below is a 20×
build on top of that, and it buys exactly three things:

1. A learner model that **compounds across sessions** — the fix for the demo's painfully long
   probe phase, which the video's author admits to on camera.
2. The tutor reachable from a browser or phone **without a harness**.
3. One scheduler **that other learning apps could share**.

If you don't want all three, install the `teach` skill into an agent host and stop. That's a real
outcome, not a consolation prize — Stage 0 below is exactly it.

Four more things that could sink this:

- **The probe phase is the demo's actual UX failure.** Binary-searching every dependency strand up
  front means many questions before any value. Mitigations: seed from the learner model, accept a
  pasted "here's what I already know", cap the question count, and probe **lazily per node**
  during teaching instead of exhaustively before it.
- **Verification carries the central claim and is the weakest link.** "Trust is engineered in"
  only holds if verification catches real errors. With no retrieval corpus it is theater — and the
  video's author notes math barely needs fact-checking, which is precisely the case where a broken
  verifier still looks like it works. Rule: **cite or abstain, and make abstention visible in the
  UI.**
- **FSRS over concepts is a HYPOTHESIS, not a finding.** FSRS is calibrated on atomic recall
  items. "Understands what a covector is" is not one. Likely fix: run FSRS on the atomic checkable
  facts the teaching step already emits as quiz questions, and keep node-level mastery as a
  separate, coarser signal. Do not assume the copy-paste works.
- **Model quality is a product constraint, not a detail.** The demo ran a max-tier model and the
  video's author says teaching quality depends on it. Inside a harness you get that from a subscription
  you already pay for. In a browser you pay per session, or you ship a worse tutor. That asymmetry
  decides which mode is the real product.

## The fork: two modes, ~80% shared code

| | Mode B — harness is the tutor | Mode A — service is the tutor |
|---|---|---|
| Brain | an agent host such as Claude Code | `tutor-svc` calling `llm-svc` |
| Services are | instruments (quiz, store, render, verify, log) | instruments **+** the loop |
| Model quality | best available, already paid for | whatever you're paying per call |
| Reach | inside a harness only | browser, phone, cron |
| Marginal cost | ~zero | per session |

**Build B first.** It's what the source actually validated, it's cheaper, and A is then just
"gateway runs the same prompt pack through `llm-svc`". The learner store, quiz, render and verify
services are identical in both.

The stateful half — the learner model — is harness-independent and is the actual moat. The
pedagogy is a prompt. So: **portable pedagogy (SKILL.md + prompt pack), stateless instruments, one
durable store.**

## Reused code

Some plumbing is reused from earlier work of mine rather than written fresh: the LLM provider
layer (`ai_providers.py`: six providers — Google, OpenAI, Claude, Ollama, LM Studio, a custom
endpoint — behind one interface and a factory), the structured-generation pattern (a Pydantic
class, JSON extraction from the reply, two retries, an argument-keyed cache), and the shape of
the hybrid FTS + semantic search. FSRS comes from the `fsrs` Python package (CONTRACTS.md
*Stack decisions*).

One `SKILL.md` and one MCP server cover every agent host that reads skill files or speaks MCP.
There is no per-host integration.

## Architecture

Stateless below the gateway, two durable stores: the learner model and the corpus.

```
learn-gateway   :5033   FastAPI. Only public surface. Owns the probe->plan->teach state
                        machine. Serves HTTP; MCP wraps this and nothing else.
llm-svc         int     ai_providers.py verbatim + generate_structured.
tutor-svc       int     The pedagogy. Prompt packs + teaching philosophy as versioned
                        files. Swappable — that is the entire point of the project.
corpus-svc      int     The material: ingest (PDF / slides in-process; audio / YouTube
                        via optional services), SQLite FTS + embeddings, page-level
                        citations. Also the verifier — "cite or abstain" cites against
                        this. Stateful.
render-svc      int     mermaid->SVG, LaTeX check, and the write->render->look->fix loop.
                        Node toolchain, so it does not belong in a Python box.
learner-svc     int     SQLite volume. Append-only event log -> derived concept graph, mastery
                        state, FSRS schedules -> generated learner.md. The only stateful service.
web-ui          :5033   Next.js.
vault           mount   VAULT_DIR bind-mounted into the Obsidian vault. Session markdown
                        with LaTeX + Mermaid + SVG embeds lands here (the demo's md-log).
```

**Why microservices for a single-user tool.** Not load — lifecycle and toolchain differ per
service. But the real justification is `learner-svc`: put FSRS + daily caps + the review queue
behind HTTP and other learning apps could share one scheduler instead of each carrying its own
copy. A service kills the copies.

Keep it at these six. Resist a seventh.

## Surfaces — one contract, four doors

```
                     +- web-ui ---------> HTTP
learn-gateway  <-----+- mcp_server.py --> Claude Code (.mcp.json) or any MCP client
                     |
                     +- scheduled job <-> Telegram: due question out, tap answer back

teach/SKILL.md ----->  Claude Code's skills folder, or any agent host that reads SKILL.md
```

The Telegram door is nearly free and is the one an SRS-backed tool most wants: a scheduled job
(any scheduler) asks `learner-svc` for the next due question, pushes it with tap buttons, and the
tap comes back as an event (see *Downtime retrieval over Telegram* below).

## Design deep-dive

Added 2026-09-03 after a round of "how does it actually know?" questions. In the source video every
one of them is answered by *"the model decides, steered by a prompt."* Each subsection below is:
what the source does → what the real machinery is → what goes in here.

**Evidence status:** the research named in this section is cited from memory of the literature,
not re-read for this document. The named findings are well-established in the field; the effect
sizes are marked *(verify)* and must be checked against the papers before anyone quotes them.

### The probe — is there an algorithm?

**Source:** no. "Binary search" is a metaphor; the model picks questions by feel and the resulting
map is its opinion.

| Method | What it does | Fit |
|---|---|---|
| Knowledge Space Theory (what ALEKS runs on) | Concepts in prerequisite order; each question is chosen to halve the set of states the learner could be in | The formal version of the video's hand-wave. Right tool for *which subject next* |
| IRT / adaptive testing (GRE, NCLEX) | Every item has a difficulty; estimate ability; ask the most informative item; stop when confident | Needs calibrated items — LLM-generated ones aren't. Partial fit |
| Bayesian Knowledge Tracing | Per-skill mastery probability, updated per answer with slip/guess rates | 4 numbers per node, interpretable. Good fit |
| Deep knowledge tracing | Neural net over response history | Needs thousands of learners. Skip |

**Decision:** KST for structure, BKT for per-node state, the LLM only *writes* the questions.

The probe, concretely:

1. Goal → model emits the prerequisite graph (10–40 nodes).
2. Learner model marks nodes already known from past sessions.
3. Pick the unknown node that best splits the remaining uncertainty.
4. Ask 1–2 questions there. Update.
5. Stop when every node on the path is confidently known (>0.85) or unknown (<0.15), or the
   budget hits (~12 questions — pick a number and defend it).
6. Teaching starts at the lowest unknown node whose prerequisites are known. That is "the edge."

Three cheap upgrades the video doesn't have:

- **Diagnostic distractors.** Each wrong option maps to a named misconception, so a wrong answer
  says *what* the learner believes, not just that they missed. This is how physics concept
  inventories work.
- **Confidence with every answer.** High-confidence wrong = a misconception *hypothesis* —
  confirmed by the sequence in *Review response* before it enters durable state, then attacked
  first. Low-confidence right = fragile, reinforce. Highest signal per zero cost of anything
  here; ask for confidence on diagnostic and mastery-gate items, not every tiny question.
- **"I don't know" as a first-class answer.** It's data, and it removes guessing noise.

### Goals, speed, time frames

**Source:** you type "solid introduction to X." Nothing else.

- **Goal = concept + depth + purpose.** Depth on Bloom's levels (recognize → explain → apply →
  analyze). "Read this paper" vs "pass this exam" vs "build this" produce different graphs and
  different quizzes.
- **Time budget → feasibility check.** Minutes per session + deadline. If the path is 40 nodes and
  there are 5 sessions, say so and offer a shallower depth or a sub-goal. Honest math, not a
  silent pace.
- **Speed is the mastery bar, not a slider.** No advancing until the bar is cleared. "Thorough /
  balanced / survey" just moves the bar — lower bar = faster = riskier, and it should say so.
- **Step size adapts.** High-confidence passes → merge two nodes. Failures → split one.

### Adapting to the person

The popular version of this — visual / auditory / kinesthetic "learning styles" — **has no
evidence behind it.** Pashler et al. (2008) reviewed it and found no support; nothing since has
changed that. Do not build a "you're a visual learner" profile.

What *does* vary per person and is worth modeling:

| Factor | Why it's real | Use |
|---|---|---|
| Prior knowledge | Biggest single predictor. It is what the probe measures | Everything |
| Expertise reversal (Kalyuga) | Novices learn better from worked examples; experts from solving problems. Same material, opposite format | Step format depends on mastery *in that area*. This is the legitimate per-person adaptation |
| Cognitive load | How much new notation per step before the learner drowns | Chunk size |
| Preferences (modality, tone, pace) | Affect whether you keep showing up, not how well you learn | Honor them; don't confuse with efficacy |
| What actually worked for *you* | The only honest "learns your style": measured outcomes | Below |

The outcome-based version: 4–6 explanation strategies (analogy-first, formal-first,
example-first, visual-first, Socratic). The tutor picks one per step. The reward is **delayed
retention** — did the 7-day review succeed — *not* the immediate quiz, which is fooled by fluency.
A simple bandit (Thompson sampling) over that small action space. Converges slowly; needs dozens
of sessions. **HYPOTHESIS** until there is data.

### Skills it employs

| Skill | Research it rests on | When |
|---|---|---|
| Graded MC + confidence + IDK | Retrieval practice (testing effect) | Stage 0 |
| Diagram generation with look-and-fix loop | Dual coding; Mayer's multimedia principles — including *no decorative visuals* | Stage 0 |
| Mermaid plan graph | Forces the model to commit | Stage 0 |
| LaTeX + markdown session log | Persistence | Stage 0 via Obsidian |
| **Teach-back** — learner explains it, model grades the explanation | Strongest form of retrieval; Feynman technique | Stage 0. Cheap, high value, **missing from the video** |
| Worked → faded → full problems | Cognitive load theory (Sweller, Renkl) | Prompt pack |
| Self-explanation prompts ("why does that step follow?") | Chi et al. | Prompt pack |
| Analogies with an explicit "where this breaks" | Prevents analogy-induced misconceptions | Prompt pack |
| FSRS scheduling | Spacing effect | Stage 1 |
| Interleaved review — old nodes mixed into new quizzes | Interleaving | Stage 1 |
| Telegram micro-retrieval — one tap-answer question in dead time | Spacing + testing effects; out-of-context retrieval is a desirable difficulty | Stage 1 |
| Code execution for math/CS | Generation effect | Stage 2 |
| TTS / STT | Accessibility; the "talk your reasoning out loud" input | Browser Web Speech API; not built yet |
| Corpus retrieval + citation (`corpus-svc`) | The trust claim — now with a corpus to cite | Stage 0 as a `sources/` folder in context; Stage 2 as a service |
| Document ingestion — PDF / slides / audio / YouTube → corpus | Grounding; the course's own structure is a prior for the plan graph | Stage 0 folder; Stage 2 service |

### Teaching methods and their basis

**Source:** the video's author's personal philosophy, from an earlier video I don't have. The tool knows
teaching methods only through what is written into the prompt pack. The model has read the
pedagogy literature; *has read* ≠ *applies reliably under pressure.* So the pack names each
technique and its trigger.

- **Bloom's 2-sigma (1984)** — one-on-one mastery tutoring ≈ 2 SD better than classroom
  *(verify)*. This is the video's whole premise. **VanLehn (2011)** meta-analysis brought it down
  to earth: human tutors ≈ 0.79 SD, intelligent tutoring systems ≈ 0.76 SD *(verify)*. Point:
  software tutors nearly matched humans a decade before LLMs. The win was mastery + immediate
  feedback, not charisma.
- **Zone of proximal development (Vygotsky)** — "the edge of understanding" is this, by name.
- **Mastery learning (Bloom)** — no advancing until threshold.
- **Retrieval, spacing, interleaving** — the three most robust effects in the field; all cheap.
- **Cognitive load theory (Sweller)** — worked examples for novices, faded as mastery rises.
- **Desirable difficulties (Bjork)** — the video's "you can gaslight yourself into thinking you
  understood" is the fluency illusion. The fix is effortful retrieval.
- **Concreteness fading** — concrete instance → abstract form, never the reverse.
- **Conceptual change** — for domains with strong wrong intuitions (physics, probability): elicit
  the wrong model first, then confront it.

The one that matters most for an *LLM* tutor: **Bastani et al. (2024)**, "Generative AI Can Harm
Learning" (Wharton; Turkish high schools) — unrestricted GPT-4 access raised practice scores and
*lowered* exam scores; a guarded hints-only tutor didn't hurt *(verify)*. Hard rule for the
prompt pack: **the tutor never gives the answer to a checkpoint. Hints, then the reveal only
after an attempt.** Default LLM behavior is to over-help, and over-help measurably damages
learning.

### What's missing from the source

In rough value order:

1. **A misconception model.** Not known/unknown — "believes X, wrongly." Falls out of diagnostic
   distractors + confidence, confirmed before it is recorded.
2. **Delayed retention as *the* metric.** Define success as 7-day review success on checkpoint
   items *before* building. Otherwise nobody can tell whether any of this works.
3. **Transfer tests.** Quiz in a different surface form than the one taught. Same form = pattern
   matching.
4. **Teach-back step.** Above.
5. **Never accept self-reported "got it."** Mastery is only ever a passed check.
6. **Frustration handling.** Three fails in a row → switch strategy, back up a node, or end the
   session. Cap session length.
7. **Domain packs.** Math/CS (executable, provable), empirical (needs citations), procedural
   (needs practice) are different tutors. One prompt pack each.
8. **One graph, many goals.** Goals share prerequisites. The learner model is one graph, not one
   per goal.
9. **Show the learner their own map.** Coloured graph: known / fragile / unknown / misconception.
   Calibration is itself a skill worth teaching.
10. **Interrupts.** "Wait, why?" mid-step has to work. It is a conversation, not a slideshow.

### Learner model storage

Question raised 2026-09-03: *"a constantly-edited truth markdown doc for the student's knowledge
level?"* Answer: a living markdown doc is the right **view** but the wrong **source of truth**.

Why a single prose doc rots: models are bad at editing numbers in prose — fifty hand-edits of
mastery probabilities and FSRS state produce contradictions and dropped lines; prose can't be
queried ("what's due today?", "which unknown node has all prerequisites known?"); and three
writers (web UI, an agent host, the reminder job) on one file clobber each other. Spaced-repetition
apps make the same call: structured, versioned storage, code computing the scheduling numbers,
no prose in the state.

**Shape:**

```
learner/
  events.db      SQLite, one append-only table. Every question, answer, confidence, assistance
                 level, node, item version, channel, timestamp, session. Never edited. This is
                 the truth. (events.jsonl is an export of it — see Review response.)
  state.json     derived by code: graph + per-node mastery + FSRS state. Recomputed from events.
  learner.md     derived by code: compact projection loaded into context each session. Lands
                 in the Obsidian vault.
  notes.md       prose: preferences, goals, deadlines, qualitative observations. The model
                 writes here — small, low-churn, prose is the right format.
```

**Rule:** the model never edits numbers. It calls a tool; the tool appends an event; code
recomputes state and regenerates the view.

What it buys: no drift (state is a fold over the log — change a BKT parameter later and recompute
from history); the delayed-retention metric is a query, not a feature; SQLite transactions keep
three writers honest where a JSONL file would not; and it is the checkpoint-and-resume pattern.

**What the model reads at session start** — the generated `learner.md`, about a dozen lines:

```
# Learner — 2026-09-03
Goal: differential forms · apply · deadline 2026-10-01 · 3 sessions left of ~6 needed !
Known:    line integrals, divergence, classical Stokes, Faraday — independent, delayed passes
Fragile:  covectors — 1 independent pass, 1 fail; last delayed retrieval failed at 6 days;
          no transfer evidence; uncertainty high; review due 09-04
Unknown:  wedge product, k-forms, exterior derivative, generalized Stokes
Misconception (active): "E and B are frame-invariant" — confirmed 09-01 by reasoning +
          reworded prediction; clinic not yet run
Due today: 2 items
Prefs: formal-first over analogy · short steps · no sports analogies
Last session: 09-01, 40 min, stopped at covector fields, mean assistance 1.4, no frustration
+41 known in linear algebra, vector calculus
```

Only the subgraph relevant to active goals; everything else collapses to one line ("+41 mastered
in linear algebra, vector calc"). Full detail on demand through a tool, so context cost stays flat
as the model grows.

**Learner edits are claims, not facts.** "Actually, I know wedge products" triggers a two-question
check that confirms it or doesn't — the "never accept self-reported got-it" rule. The research
name is an *open learner model* (Bull & Kay, negotiated learner models — *(verify)*): the student
can challenge the system's belief and the disagreement is settled by evidence. `notes.md` is freely
editable; preferences and goals don't need proof.

**Obsidian-native variant:** one note per concept, numbers in frontmatter, prose in the body,
`[[links]]` as prerequisite edges — the graph view becomes the learner map for free and Dataview
can query "due today." Same architecture (frontmatter numbers still written by code), but it
couples the store to the vault layout. Treat it as a storage *format* for `state.json`, not a
different design. Precedent both ways: the Obsidian Spaced Repetition plugin stores SRS state
inside notes, and is known to be fragile.

**Stage mapping:** Stage 0 = a small CLI (`learner record …`, `learner summary`) owning the four
files; the skill shells out to it, identically in Claude Code or any other agent host (the CLI
is Python over the `fsrs` package — CONTRACTS.md *Stack decisions*). Stage 1 = that CLI's core
becomes `learner-svc`; same event and state shapes, the same SQLite file moves into a volume,
`learner.md` keeps landing in the vault. Nothing above it changes.

### Downtime retrieval over Telegram

Proposed 2026-09-03: push quiz questions or study material to Telegram from a scheduled job
through the day, so learning continues in dead time.

**Keep the channel, change the payload.** The intuition is right — short, spaced retrieval in
dead time is exactly what the spacing and testing effects reward. But the *"info to study"* half
is the weak half: rereading and passive exposure sit at the bottom of the effectiveness rankings
(Dunlosky et al. 2013 — *(verify)*), and unsolicited content is what gets a bot muted. So the
rule is: **push retrieval, never content.** Explanation enters only as 1–3 lines of feedback
after an attempt — where it has evidence, and where the "hints first" rule already puts it.

**Why Telegram and not the web UI:** two-way bots with tap buttons are simple to build; a
lock-screen question fits a 30-second gap. Mobile web push would need a PWA and a service worker
for less.

**What a push is.** One question, answerable in under 30 seconds, with tap buttons — the
options, *I don't know*, and *not now*. The answer is recorded as an event tagged
`channel: telegram` and feeds the same learner model as a session answer. Out-of-context
retrieval with no scaffolding around it *may* be a stronger signal of durable memory than an
in-session checkpoint — **HYPOTHESIS**; the channel and context tags exist so the data can decide
the weight, and until then it counts the same as a session answer.

**What gets picked, in priority order:**

1. **Due reviews** — FSRS items due today. The core.
2. **Fragile-node probes** — one question on a node at 0.5–0.85, where forgetting is likeliest.
3. **Misconception checks** — a question whose distractor is the recorded misconception; has it
   been dislodged?
4. **Transfer variants** — the taught concept in a different surface form. Micro-pushes are the
   natural vehicle for the transfer tests the deep-dive asks for.
5. **Pre-session priming** — the evening before a scheduled session, one question on the
   prerequisites of tomorrow's node. This is the only place "what to study next" appears, and it
   appears as a question.

Nothing else. If nothing is due and nothing is fragile, **send nothing** — silence is a feature.
Never generate a question to fill a slot.

**Policy (defaults, configurable):** budget 3 pushes/day; window 09:00–21:00; quiet hours
respected; at least 90 min between pushes; halve the budget on a day with a live session; *not
now* is data, not a wrong answer; 3 unanswered in a row → drop to 1/day; 7 days ignored → pause
and ask. Timing adapts: after two weeks, pushes move to the three hours with the best historical
response rate.

**Where the questions come from.** The item bank is the checkpoint questions the sessions already
produced — with their distractors and misconception tags — so it fills itself. Transfer variants
are generated at push time; in Mode B that is an agent-host turn running the `teach` skill, so
it costs nothing extra.

**Stage:** mechanically possible at Stage 0 (any scheduler can run the `learner` CLI and call the
Telegram Bot API), but its value depends on an item bank that only sessions produce — so it lands
in **Stage 1**, whose theme is retention anyway. Kill criterion: response rate below 30% after two weeks → cut
the cadence, then cut the feature.

**Devil's advocate:** notification fatigue is the whole risk, and a muted bot is a dead channel.
The budget, silence-by-default, and the *not now* button are the mitigations; if they aren't
enough, the feature dies rather than the channel.

### Grounding: where the material comes from

Question raised 2026-09-03: how does the tutor know the material? Upload slides / PDFs / DOCX /
MD into a RAG store, or run an initial research phase under user guidelines?

**Both — and they are the same store.** Open question #3 asked whether `verify-svc` has a corpus.
This is the corpus. Whatever the tutor teaches from is also what it cites against, so
`verify-svc` becomes **`corpus-svc`** — ingest, search, cite — and "cite or abstain" finally has
something to cite. Still six services.

**Three sources of material, not exclusive:**

| Source | Strength | Weakness |
|---|---|---|
| Parametric — what the model already knows | Deep on canonical topics; free; best at *explaining* | No provenance; notation may not match your course; thin on niche or recent material |
| Your documents — slides, PDFs, DOCX, PPTX, MD, textbook chapters | What you will actually be tested on; the course's notation and order; page-level provenance ("slide 14") | Terse, sometimes wrong, equations often images |
| Researched corpus — a pass under your guidelines | Fills gaps; adds cited sources for verification | Where invented sources enter; costs a phase; needs approval |

**Division of labour, as a rule (revised after review):** *alignment* sources — slides, syllabus,
past exams — constrain **scope and notation**; the **learner model chooses the route**;
*authority* sources — textbook, docs, standards — support **correctness**; the **model chooses
how to explain**. Conflicts are flagged to the learner, never silently resolved, with an
exam-mode vs truth-mode view. A tutor that treats slides as scripture teaches the professor's
typos, and citing the slide proves alignment, not truth.

**The non-obvious payoff:** a course's own structure — chapter order, slide sequence, headings —
is a *prior for the plan graph*. Ingestion extracts it and the plan phase aligns the tutor's DAG
to it, so the route through the material matches the route the exam assumes. The probe can also
draw exam-aligned questions straight from the corpus.

**Research pass.** Triggered, not default: by a niche or recent topic, by an explicit ask, or by
missing uploads on a topic the model is thin on. Runs once per goal and is cached. Input is a
per-goal `sources.md` — trusted and banned sources, preferred textbooks, notation preference
("use the slides' notation"), depth, language, exam format. Output is a **source list the learner
approves** before anything is ingested — negotiated sources, the same move as the open learner
model. In Mode B this is an agent-host research turn (e.g. web search in Claude Code); it costs
nothing extra.

**RAG, plainly.** For one person with dozens to hundreds of documents, a vector-database service
is a seventh container for no reason. SQLite FTS5 plus embeddings stored in SQLite is enough for
hybrid (FTS + semantic) search over exactly this shape. Two further honesty points:

- For a single course deck (a few hundred slides, tens of thousands of tokens) **retrieval may
  never be needed**: load the whole corpus into context for the plan phase and retrieve per node
  only for citations during teaching. Start there.
- Equation-heavy slides and PDFs extract badly as text. Model vision on the slide image is
  probably more reliable — **HYPOTHESIS**, test on your own slides.

**Ingestion paths:**

| Input | Path in | How |
|---|---|---|
| PDF, DOCX, PPTX | text + structure extraction | in-process extraction |
| Scanned PDFs, photographed notes | OCR | an optional OCR service (`LT_OCR_URL`) |
| Lecture audio / video | transcript | any compatible transcription service (`LT_TRANSCRIBE_URL`) |
| YouTube lectures | transcript | an optional YouTube-transcript service (`LT_YT_TRANSCRIPTS_URL`) |
| Equation slides | vision | model vision on the slide image — **HYPOTHESIS**, see above |
| Markdown notes | direct | the vault itself |

**Stage mapping.** Stage 0: a `sources/` folder in the vault; the skill reads whole documents
into context; research by the agent host lands in the same folder. No service.
Stage 2: `corpus-svc` with FTS + embeddings and page-level citations — pulled forward to Stage 1
only if a real course deck exceeds context or citations must be precise.

**Devil's advocate.** Uploading everything is a garbage-in problem — hence the division-of-labour
rule and flag-don't-resolve. The research pass is where invented sources enter — hence approval
and cite-or-abstain. And the RAG stack is the classic place this project would grow a seventh
service. Don't.

## Review response (2026-09-03)

An external design review of the blueprint and this file was analysed on 2026-09-03. Its central
claim is adopted: **the weak link is measurement validity, not pedagogy.** Every decision rests on
the chain *generated question → response → evidence → state → next decision*, and "the LLM only
writes the questions" had been treated as the safe part of that chain. It is the dangerous part.

Two facts were checked before weighing it. **Tutor MCP exists** —
[github.com/ArnaudGuiovanna/tutor-mcp](https://github.com/ArnaudGuiovanna/tutor-mcp): MIT, alpha
v0.5.0, 24 stars, one maintainer, SQLite + markdown learner memory, BKT / FSRS / IRT / PFA / KST.
Its README calls its numbers *"deterministic and auditable routing heuristics, not a claim of
psychometric or clinical validation"* that *"still depend on the LLM calling the tools and scoring
the learner faithfully."* The review's "60% of LLM items vs 74% of expert items" figure is **not**
in the abstract of the paper it cites (arXiv 2508.08314 says "comparably") — unverified.

### Corrections to this document

1. "Appends are concurrency-safe" was overclaimed. Three writers on a JSONL file can duplicate,
   interleave and partial-write. **The event store is a SQLite table from Stage 0; `events.jsonl`
   is an export.**
2. "No gate after Stage 2 — it's the product" was lazy. Deployment proves reachability. A
   **product gate** now follows Stage 2 (below).
3. One confident wrong tap became a misconception. Too eager, doubly so over Telegram. A
   misconception is a **hypothesis until confirmed** (sequence below).
4. Out-of-context Telegram answers "count for more" was asserted. It is a **HYPOTHESIS**; the
   channel tag exists so the data can decide the weight.
5. "Knows exactly what you understand" (Concept, and the page lede) was too strong. The system
   keeps an **evidence-backed, uncertainty-aware estimate of what you can currently do.**
6. The page said "six services below the gateway"; it is five, six with the gateway.

### Adopted now — schema-shaping, decided before Stage 0 writes its first event

| Decision | Spec |
|---|---|
| **Event store** | SQLite, one append-only table. Minimum fields: `event_id`, `ts`, `session_id`, `goal_id`, `node_id`, `item_version_id`, `response`, `correct`, `confidence`, `assistance_level`, `channel`, `context` (in-session / delayed / transfer), `prompt_version`, `grader_version`. JSONL is an export. |
| **Assistance level on every event** | `0` none · `1` encouragement · `2` conceptual hint · `3` strategic hint · `4` procedural hint · `5` partial solution · `6` worked solution. A pass at 5 is not a pass at 0; every mastery claim cites the level. |
| **Item status** | Every generated item starts `TEACHING_ONLY`. Becomes `PRACTICE_EVIDENCE` after a source check, an independent solve (a different model or a solver), an ambiguity check, component mapping and distractor rationale. Becomes `MASTERY_ELIGIBLE` after it has behaved reliably in use. Only `PRACTICE_EVIDENCE` and above write evidence. The same model is never sole author, solver and judge. |
| **Hidden holdouts** | A slice of `MASTERY_ELIGIBLE` items is never used in teaching or review. The 7-day and transfer metrics are computed on holdouts only. |
| **Stable graph** | Nodes carry ids, not names; a `concept_alias` table; every graph revision is versioned and evidence survives splits and merges via migration. Edge types: `strict_prerequisite`, `recommended_background`, `course_sequence`, `co_requisite`, `supports`, `misconception_for`, `transfer_related`. Edge provenance: `course`, `reference`, `model`, `learner_evidence`, `human`. |
| **Misconception confirmation** | High-confidence wrong → ask for the reasoning → a reworded prediction → a discriminating counterexample → confirm or drop. States: `suspected` → `active` → `weakened` → `resolved` → `recurred`. Only `active` appears in `learner.md`. |
| **`learner.md` shows evidence, not decimals** | Numbers stay internal. The view shows state, independent vs assisted evidence counts, last delayed retrieval, transfer evidence, uncertainty, review priority. Sample updated in *Learner model storage*. |
| **Corpus is data, never instructions** | Retrieved text cannot invoke tools; document instructions are quoted and isolated; durable learner state is written only through structured tools; source lineage on every memory write; suspicious content flagged. |

### Adopted into the design — build later

- **Three source roles.** *Alignment* (slides, syllabus, past exams) constrains scope and
  notation. *Authority* (textbook, docs, standards) supports correctness. *Learner* (your notes,
  solutions, code) is evidence about you. All may live in `corpus-svc`; they do not share an
  epistemic role — citing a slide proves alignment, not truth. Conflicts get an **exam-mode vs
  truth-mode** view: "for your exam the professor expects A; canonically B adds a condition."
- **Goal contract.** Structured, per goal: target capabilities (explain / trace / implement /
  analyse / select), deadline, hours per week, assessment format, target delayed performance on
  holdouts, transfer requirement, source priority. Resolves what "done" means and the goal-level
  stopping rule.
- **Learning claim as the core object.** *Learner L can perform C, under conditions X, with
  assistance ≤ A, after interval T, supported by evidence E, with uncertainty U.* Three layers:
  competency (what the map shows) → knowledge components (where evidence aggregates) → items
  (where FSRS schedules). Concept state is derived from component evidence, never scheduled as a
  flashcard. Resolves the "unit of mastery" question.
- **Multidimensional state under the four colours.** Acquisition, independence, retention,
  transfer, calibration, assistance dependence, uncertainty. The four colours are a projection.
- **N-of-1 matched comparisons replace the bandit.** Two matched concepts, two strategies
  (worked-example vs guided discovery; formal-first vs intuition-first; text vs diagram-first),
  same delay, hidden transfer items, reward = delayed independent gain per minute. A bandit only
  after enough observations exist.
- **Rule-based evidence model first, BKT as a candidate.** Coarse states from transparent rules
  over independent / assisted / delayed / transfer evidence. Compare BKT against it on hidden-item
  prediction before adopting it. Do not fit per-learner slip and guess on a handful of
  observations.
- **Decision ownership.** One orchestration module decides the next question, pass/fail, strategy
  change, session end and graph revision — regardless of whether the LLM runs in the harness
  (Mode B) or in `tutor-svc` (Mode A). `tutor-svc` supplies prompts, not decisions.
- **Product gate after Stage 2.** Delayed holdout performance; false-mastery rate; generated-item
  rejection rate; citation and source-conflict errors; learner-model disputes and how often the
  learner was right; cost and latency per session; export and deletion work; and it beats a plain
  AI study mode on hidden delayed transfer per minute in a within-person crossover.
- **Curriculum map vs learner path** as two visible graphs; **evidence receipts** on every state
  ("why is this fragile?"); **self-removing tutor** metrics (fewer hints, better calibration,
  unprompted transfer); a **learner passport** export (goals, evidence, misconceptions, artifacts,
  disputes) — the model-independence promise made concrete.
- **Typed disputes.** "I already know this", "ambiguous question", "misclick", "not on my exam",
  "this edge is wrong", "test me instead". Each creates a transparent dispute settled by evidence;
  none silently becomes mastery.

### Held, not adopted

- **Modular monolith vs services.** The review argues network separation isn't needed until
  another app consumes `learner-svc`. Recorded as the **extraction rule** for Stage 2: extract a
  service when it needs a different runtime, a different security boundary, independent scaling,
  or a second real consumer. Stages 0–1 are single-process already. Stage 2 shipped as four
  containers by that rule, not six (CONTRACTS.md *Stack decisions*); the compose target and port
  stay.
- **Product positioning.** This is a personal tool first. The review's wedge — university STEM and
  technical certifications — is adopted only as the *assumed learner profile* for domain packs
  and item types, because objective grading and real prerequisites make measurement easier.
- **The review's 20-field event schema.** The subset above; add fields when something needs them.
- **Its numeric scores.** Decorative.
- **Tutor MCP.** Stage 0 is unaffected. Before Stage 1: one hour auditing its MCP tool contract and
  event schema; borrow tool names and shapes where sensible so a later swap is cheap. Do not
  build on it now — its estimates depend on the host LLM grading faithfully, which is precisely
  what the item lifecycle exists to fix. Re-evaluate if it matures.

## Open questions

1. **BKT vs a rule-based evidence model.** FSRS-on-items and the three-layer unit of mastery are
   decided (*Review response*); whether BKT adds anything over transparent rules at this data
   volume is decided by hidden-item prediction, not preference.
2. **Tutor MCP audit.** Before Stage 1: its MCP tool contract and event schema. Borrow shapes;
   do not build on it. Done 2026-09-05: idempotency keys and the `evaluation_method` rule were
   adopted from it.
3. **`sources.md` defaults per domain.** The corpus question is answered (uploads + approved
   research, see *Grounding*); what is still open is which sources count as trusted by default
   per domain, and when the research pass triggers by itself.
4. **Probe budget.** Maximum questions before the first teaching step — pick a number and defend
   it. The deep-dive assumes ~12.
5. **When `learner-svc` gets extracted.** Per the extraction rule in *Review response*: when
   a second app actually calls it, or Stage 2 needs a separate runtime. ("Done" for a goal is now
   defined by the goal contract.)
6. **Disagreement and skipping.** What happens when the learner disagrees with the plan or wants
   to skip a node? The loop must allow it without corrupting the learner model.
7. **Per-session stopping rule.** Time cap, frustration trigger, or mastery of the day's nodes —
   pick one as the default.
8. **Downtime retrieval defaults.** 3/day, 09:00–21:00, 90-min spacing are guesses. Measure
   response rate and 7-day retention lift before trusting them.
9. **Modular monolith or six containers at Stage 2.** Stage 2 shipped as four containers by the
   extraction rule in *Review response* (CONTRACTS.md *Stack decisions*); whether to split
   further is still open. The compose target and port stay either way.

## Staging

| Stage | Build | Proves | Gate to the next |
|---|---|---|---|
| **0** | `teach/SKILL.md` + quiz tool (MC + confidence + IDK) + teach-back with a versioned rubric + `learner` CLI over a **SQLite event table** (assistance level, item status, hidden holdouts) → generated `learner.md` + a manually reviewed initial graph with stable ids + md-log → Obsidian vault + a `sources/` folder read whole into context. Runs in Claude Code or any agent host that loads SKILL.md. No containers. No Telegram. | The pedagogy works *for you*. | Actually learn something real — measured on hidden delayed-transfer items, not by feel. |
| **1** | `learner-svc` (the CLI's core behind HTTP: FSRS on validated items, concept state derived, graph migrations, misconception confirmation, item statistics, disputes) + MCP wrapper + Telegram micro-reviews once an item bank exists (push retrieval, never content) + the Tutor MCP audit. | Cross-session compounding; one scheduler behind HTTP that other apps could call. | Probe measurably shorter on session 2; false-mastery rate on holdouts acceptable. |
| **2** | `llm-svc` + `tutor-svc` + `corpus-svc` (three source roles, page-level citations) + `render-svc` + gateway + web UI (graph editor, evidence receipts, learner passport export). Compose stack on 5033, services extracted by the extraction rule. | Mode A; runs as one compose stack. | **Product gate** — see *Review response*. Deployment proves reachability, not value. |

Do not start Stage 2 until Stage 0 has taught you something.

Exam prep (added 2026-09-26: a per-goal exam blueprint, mixed and shuffled practice, sealed mock
exams, progress by area) is built on top of Stage 2's model-free study tools; see
[docs/modules/exam-prep.md](docs/modules/exam-prep.md).
