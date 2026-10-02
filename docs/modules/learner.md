# `learner` — the Stage 0 core and CLI

The learner model: an append-only SQLite event store, a stable concept graph, a versioned
item bank, rule-based evidence, FSRS scheduling on items, misconceptions, disputes, hidden
holdouts, and the two derived views (`learner.md`, `state.json`).

Stage 1 added three things to this core without changing its shape: an `evaluation_method`
on every evidence event, an idempotency key on every mutating call, and two goal-contract
fields — all at schema version 2. The same core is now also served over HTTP and MCP; see
[`learner-svc.md`](learner-svc.md).

Everything a tutor writes goes through this CLI. **The model never edits numbers** — it
calls a command, and code recomputes state and regenerates the views.

Spec: [`IDEA.md`](../../IDEA.md). Binding shapes: [`CONTRACTS.md`](../../CONTRACTS.md).

## Contract deviations

None. Every command, flag, JSON field and exit code in CONTRACTS.md is implemented as
written. The following are **additions** — supersets that cannot break a caller written
against the contract:

| Addition | Why |
|---|---|
| `--data-dir` and `--json` are accepted *anywhere* on the command line, not only where the contract shows them | The skill sometimes appends `--json`; both are stripped before parsing |
| `--goal` is optional on `next`, `graph show`, `summary`, `metrics`, `holdout-check` when exactly one goal exists | `skills/teach` writes `learner summary --format md` in prose; with two or more goals the error lists them |
| `--mode` defaults to `auto` on `next` (review → probe → teach, in that order of preference) | The contract shows `--mode` as optional but names no default |
| `item add --item I` adds a **new version** of an existing item; `item add --author X` names the author | The contract requires versioning on every stem edit and an author distinct from the validator, but shows no flag for either |
| `record answer --channel/--prompt-version/--grader-version` | Columns the event schema requires; the contract's flag list omits them |
| `record teach-back --context`, `misconception suspect --session`, `dispute open --session` | Same: fields the event schema carries |
| `item add`/`validate`/`promote` also return `item_version_id` (a mirror of `current_version_id`); `record answer`/`teach-back` also return `node_state_name` (the bare state string next to the full `node_state` object) | The skill's transcript reads those key names |
| `goal add` also creates and returns `sources_dir` — `data/sources/<slugified goal id>/` | The data-directory layout in CONTRACTS.md has `sources/<goal_id>/`; nothing else created it |
| Extra modules `ids.py`, `models.py`, `selection.py`, `metrics.py`, `api.py` inside `learner/` | `models.py` is required by CONTRACTS.md; the others are internal splits. No contract file was renamed or removed |
| Extra tables `schema_version`, `node_goals`, `item_validations`, `fsrs_state`, `idempotency` | The contract fixes the `events` columns and lists the required tables; these are additive |
| `item validate --evaluation-method` | CONTRACTS.md gates *both* "past `TEACHING_ONLY`" and "a node to `known`" on the trusted methods, but names the flag only on `record answer`. Optional here, and unstated by default, so Stage 0 behaviour is unchanged; when stated, a `host_llm` validation does not promote |
| `record teach-back --prompt-version/--grader-version`; `--grader-version` defaults to `--rubric-version` | The contract adds both flags to `record teach-back`, which already had a required `--rubric-version` that *is* the grader version. Overwriting it with the constant `teach-back-v1` would lose the only version tag that matters there |
| `record teach-back` writes `evaluation_method = rubric`, always | The contract puts `--evaluation-method` on `record answer` only. A teach-back is scored against a frozen versioned rubric by definition of the command, which is exactly what `rubric` names |
| `--idempotency-key` on every mutating command; `api.*` takes `idempotency_key` | CONTRACTS.md requires it of "every mutation" from Stage 1 and shows it on the HTTP surface; the CLI is a mutation surface too |
| `goal_list`, `session_get` in `api.py`; `GET /v1/goals`, `GET /v1/sessions/{s}` | Reads the HTTP surface needs and the CLI never had. Additive |
| Extra module `idempotency.py`, extra migration file `schema_v2.sql` | Internal splits; no contract file was renamed or removed |

One judgement call worth flagging: CONTRACTS.md lists five misconception states and none of
them means "the hypothesis was dropped during confirmation". A dropped hypothesis is stored
as `resolved` with `resolution_reason = "dropped at <step>"` rather than inventing a sixth
state.

## Install

```bash
uv sync                       # dependencies + the project itself
uv run learner --help         # run it from the repo
uv tool install .             # or install `learner` on the PATH, globally
```

Configuration is entirely environment-driven; there are no machine-specific values in the
code.

| Variable | Default | Meaning |
|---|---|---|
| `LT_DATA_DIR` | `./data` | Everything durable lives here |
| `LT_VAULT_DIR` | `<data>/vault` | Obsidian vault: `learner.md` and `sessions/` land here |
| `LT_HOLDOUT_FRACTION` | `0.2` | Share of `MASTERY_ELIGIBLE` items held back as holdouts |
| `LT_PROBE_BUDGET` | `12` | Probe-context answers allowed per session |
| `LT_KNOWN_THRESHOLD` / `LT_UNKNOWN_THRESHOLD` | `0.85` / `0.15` | Reserved for the BKT model; the rule model uses evidence counts, not probabilities |
| `LT_EVIDENCE_MODEL` | `rules` | `bkt` raises `NotImplementedError` on purpose |
| `LT_PROMOTE_MIN_USES` | `3` | Uses required before `item promote` |
| `LT_HOLDOUT_DELAY_DAYS` | `7` | Holdout delay, and the metrics window |
| `LT_MINUTES_PER_NODE` | `15` | Feasibility arithmetic |
| `LT_SESSIONS_PER_WEEK` | `3` | Feasibility arithmetic |
| `LT_DESIRED_RETENTION` | `0.9` | FSRS target retention |

`learner-svc` and the MCP server add `LEARNER_PORT`, `LEARNER_HOST`, `LT_LEARNER_URL` and
`LT_MCP_ENABLED`; see [`learner-svc.md`](learner-svc.md).

## Modules

| File | What it owns |
|---|---|
| `store.py` | SQLite connection, WAL, forward migrations, `LearnerError`, `IdempotencyConflict`, timestamps |
| `schema.sql` | Migration 1: every table, plus the two triggers that make `events` append-only |
| `schema_v2.sql` | Migration 2 (Stage 1): `evaluation_method`, the two goal fields, the `idempotency` table |
| `idempotency.py` | Replay for mutating calls: same key + same body replays, different body is a 409 |
| `ids.py` | Monotonic ULIDs, `n_<slug>_<4hex>` node ids, the holdout hash |
| `models.py` | Pydantic models shared by CLI now and HTTP + MCP at Stage 1 |
| `events.py` | Append and query. Never updates, never deletes |
| `graph.py` | Import, show (json / mermaid), revise (add / remove / split / merge) with evidence migration |
| `items.py` | The item bank and its lifecycle rules |
| `evidence.py` | Rule-based node state derivation (the tables below) |
| `fsrs_sched.py` | py-fsrs over items; the rating mapping |
| `misconceptions.py` | The `suspected → active → weakened → resolved → recurred` machine |
| `disputes.py` | The six typed disputes |
| `holdouts.py` | Which holdouts are due, and marking them checked |
| `selection.py` | `next` in probe / review / teach mode |
| `metrics.py` | The three Stage 0 numbers |
| `views.py` | `learner.md` and `state.json` |
| `export.py` | `events.jsonl` and the passport dump |
| `api.py` | The transport-free service layer the CLI, `learner_svc` and `mcp_server` all call |

## The data directory

```
data/
  learner/events.db      SQLite — the truth
  learner/state.json     derived, rewritten on every write
  learner/notes.md       prose, model-editable, never numbers (the Prefs bullets land in learner.md)
  learner/events.jsonl   export only (`learner export`)
  sources/<goal_id>/     uploaded documents (Stage 0: read whole into context)
  vault/learner.md       the dozen lines the model reads at session start
  vault/sessions/        `<date>-<goal>.md` session logs
```

## The evidence rules

Node state is derived, never stored. Only these events count as evidence for a node:

* `answer` / `probe_answer` on an item that is `PRACTICE_EVIDENCE` or `MASTERY_ELIGIBLE`.
  A `TEACHING_ONLY` item teaches; its answers are logged and write **no** evidence
  (`wrote_evidence: false` in the response says so).
* `teach_back`, scored 0-3 against a versioned rubric; 2 or more is recorded as correct.

Counters per node: `independent_passes` (correct, assistance 0-1), `assisted_passes`
(correct, assistance 2-4), `unearned_passes` (correct at assistance 5-6 — recorded, never
counted), `self_graded_passes` (correct, but judged by the tutoring model itself — recorded,
never counted), `fails` (incorrect or "I don't know"), `last_delayed` (outcome of the most
recent `delayed` event), `transfer_passes` (independent passes in `transfer` context).

State, first rule that matches:

1. an `active` (or `recurred`) misconception on the node → **misconception**
2. no evidence at all → **unknown**
3. no pass of any kind → **unknown**
4. all four of: ≥ 2 independent passes; the last delayed retrieval did not fail; at least
   one delayed **or** transfer pass; no failure in the last three attempts → **known**
5. otherwise → **fragile**

Since Stage 1, a pass only counts as *independent* if it was judged independently.
`evaluation_method = host_llm` — the default, meaning the tutoring model decided its own
learner was right — is a `self_graded` outcome: recorded, scheduled through FSRS, able to
suspect a misconception, but never a pass. It is not a failure either, so it does not break
the "no failure in the last three attempts" clause and it does not change `last_delayed`.
Only `blind_solver`, `rubric` and `human` count. Events written before migration 2 carry no
method and keep their Stage 0 meaning — a migration must not retroactively invalidate a
history it knows nothing about. Full table in
[`learner-svc.md`](learner-svc.md#evaluation_method--the-self-graded-rule).

Rule 4's third clause is the important one: in-session fluency alone never reaches `known`,
because fluency is what the fluency illusion is made of.

Uncertainty is a label (`low` / `medium` / `high`), never a decimal in a view. With
`weight = 2*independent + assisted + fails + transfer + (1 if any delayed evidence)` and
`conflict = fails > 0 and independent > 0`: no evidence → `high`; `weight ≥ 6`, no conflict
and a passing last delayed retrieval → `low`; `weight ≥ 3` → `medium`; else `high`.

`LT_EVIDENCE_MODEL=bkt` raises `NotImplementedError` by design — IDEA.md requires BKT to
beat these rules on hidden-item prediction before it is adopted, and that comparison needs
data nobody has yet.

## The FSRS rating mapping

FSRS runs on **items only**; concepts are never scheduled as flashcards. One `fsrs.Card`
per item lives in `fsrs_state`.

| Answer | Rating |
|---|---|
| "I don't know", or incorrect (at any confidence) | `Again` |
| correct at assistance ≥ 5 (partial or worked solution) | `Again` |
| correct at assistance 2-4 (a hint was needed) | `Hard` |
| correct at assistance 1 with confidence 1-2 | `Hard` |
| correct at assistance 1 otherwise | `Good` |
| correct at assistance 0 with confidence 4-5 | `Easy` |
| correct at assistance 0 otherwise | `Good` |

A pass at assistance ≥ 5 never counts toward mastery (hard rule 1), so it must not lengthen
an interval either: it is scheduled as a lapse. Confidence only ever moves a *correct*
answer between `Good` and `Easy` — a confident wrong answer spends its signal on the
misconception machinery, not on the schedule.

## Item lifecycle

`TEACHING_ONLY → PRACTICE_EVIDENCE → MASTERY_ELIGIBLE`.

* `item add` creates version 1, `TEACHING_ONLY`. Editing a stem or its options
  (`item add --item I`) creates a new version and sends the item **back** to
  `TEACHING_ONLY`: a reworded question is a different question.
* `item validate --by X` refuses `X == author` (case- and whitespace-insensitive). A pass
  promotes to `PRACTICE_EVIDENCE`; a fail leaves it `TEACHING_ONLY` and counts toward the
  item rejection rate.
* `item promote` requires `PRACTICE_EVIDENCE`, at least `LT_PROMOTE_MIN_USES` recorded
  uses, at least one correct answer among them, not-all-"I don't know", and no open
  `ambiguous question` dispute on the item.
* Promotion assigns holdout membership deterministically:
  `sha256(item_id)[:8] / 2^32 < LT_HOLDOUT_FRACTION`. The flag is a pure function of the id,
  so it never flips, and re-running the assignment is a no-op.

Holdouts are **never** returned by `next` in any mode. `holdout-check` is their only door.
A holdout is due when its node has evidence (state is not `unknown`) and it has not been
served for `LT_HOLDOUT_DELAY_DAYS`, counted from the last check or from promotion. A holdout
whose version carries a `surface_form` is served as a `transfer` check, everything else as
`delayed`.

## `next` — selection

**probe** (KST-style). Candidates are the `unknown` nodes. A question about node *n* splits
the space of possible knowledge states: a pass implies its prerequisites, a fail implies
everything downstream. Each candidate scores
`min(#unknown ancestors incl. self, #unknown descendants incl. self)`; the highest score
wins, ties broken toward nodes whose strict prerequisites are already known, then
topological order, then id — so the pick is deterministic. On a five-node chain with nothing
known, that is the middle node. The session's probe budget caps probe-context answers; when
it is spent, `next` returns no picks and says `probe budget spent for this session`.

**review**, in priority order: FSRS-due items → fragile nodes → misconception checks (an
item whose distractor names the active claim, when one exists) → transfer variants for known
nodes with no transfer evidence. This is the same priority order the optional Telegram
reminders use ([`telegram.md`](telegram.md)).

**teach**: the lowest node in topological order that is not `known` and whose strict
prerequisites all are — "the edge".

**auto** (the default): review if anything is due, else probe if the budget allows and
anything is unknown, else teach.

A pick with no validated item comes back with `item_id: null` and a reason ending
`no validated item yet — author and validate one`. That is the signal to write one.

## Graph revisions

Every write bumps `graph_versions` and appends a `graph_revision` event. Node ids are
stable; `node_aliases` resolves names, aliases and old titles to ids.

Evidence migration is append-only, because the event table is:

* **split**: every evidence event on the parent is *copied* to each child with
  `payload.migrated = true`, `migrated_from`, `source_event_id`, `original_ts`. The parent
  is retired, its edges are copied to the children, the children are chained in the order
  given (`"chain": false` disables that), and items move to the first child unless the op
  carries `"items": {"<child>": ["<item_id>", ...]}`.
* **merge**: evidence from every source is copied into the target (a union), items are
  repointed, aliases are repointed, sources are retired.

Nothing is ever rewritten, so `events.jsonl` still replays to the same state.

## CLI examples

```bash
uv run learner init
uv run learner goal add --id differential-forms --title "Differential forms" \
    --depth apply --deadline 2026-10-01 --minutes-per-session 45 --purpose "read Spivak ch.4" \
    --assessment "closed-book exam on Stokes" --source-priority alignment

uv run learner graph import --goal differential-forms --file data/tmp/graph.json
uv run learner graph show --goal differential-forms --format mermaid

S=$(uv run learner session start --goal differential-forms --channel claude-code | jq -r .session_id)
uv run learner next --goal differential-forms --session "$S" --mode probe

uv run learner item add --node n_covectors_8999 --file data/tmp/item.json --author claude
uv run learner item validate --item i_01m1… --by claude-code-subagent --result pass \
    --notes "solver picked the key, no ambiguity flag"
# --evaluation-method defaults to host_llm: recorded, but never counted toward mastery
uv run learner record answer --session "$S" --item i_01m1… --response "a covector" \
    --correct 1 --confidence 4 --assistance 0 --context probe \
    --evaluation-method blind_solver --idempotency-key push-42
uv run learner record teach-back --session "$S" --node n_covectors_8999 --score 2 \
    --rubric-version rubric-v1 --assistance 1
uv run learner item promote --item i_01m1…

uv run learner misconception suspect --node n_wedge_448d --claim "wedge is ordinary multiplication"
uv run learner misconception confirm-step --node n_wedge_448d \
    --claim "wedge is ordinary multiplication" --step reasoning --outcome held
uv run learner dispute open --type "I already know this" --node n_wedge_448d --note "did this at uni"
uv run learner dispute settle --dispute d_01m1… --outcome rejected --evidence "failed both checks"

uv run learner graph revise --goal differential-forms --ops data/tmp/ops.json
uv run learner session end --session "$S" --summary "covered covectors"
uv run learner log --session "$S" --file data/tmp/session-log.md
uv run learner summary --goal differential-forms --format md
uv run learner holdout-check --goal differential-forms
uv run learner metrics --goal differential-forms
uv run learner export --out data/learner/events.jsonl
```

Output is JSON whenever stdout is not a TTY, and whenever `--json` appears anywhere. Every
failure prints `{"error": "..."}` on stderr and exits non-zero (1 for a rejected operation,
2 for a bad command line).

Input file shapes:

```jsonc
// graph import --file
{"nodes": [{"id": "optional", "title": "Covectors", "aliases": ["dual vectors"], "domain": "vector calculus"}],
 "edges": [{"from": "Vectors", "to": "Covectors", "type": "strict_prerequisite", "provenance": "course"}]}

// item add --file
{"stem": "...", "options": ["a", "b"], "answer": "a",
 "distractor_misconceptions": {"b": "the named wrong belief"},
 "kind": "mc", "components": ["knowledge component"], "surface_form": "optional; marks a transfer variant"}

// graph revise --ops   (a bare list works too)
{"ops": [
  {"op": "add", "node": {"title": "Pullbacks"}, "edges": [{"from": "k-forms", "to": "Pullbacks"}]},
  {"op": "remove", "node": "Pullbacks", "reason": "off-syllabus"},
  {"op": "split", "node": "Covectors", "into": [{"title": "Covector basics"}, {"title": "Covector algebra"}]},
  {"op": "merge", "nodes": ["Wedge product", "k-forms"], "into": {"title": "Wedge and k-forms"}}
]}
```

## The three metrics

* **7-day holdout success rate** — holdout checks answered inside the window, and how many
  passed (correct, assistance below 5).
* **False-mastery rate** — nodes that were `known` *at the moment the holdout was served*
  and then failed it, over all nodes with any holdout check. The state is recomputed from
  events strictly before that answer, so a later downgrade cannot flatter the number.
* **Item rejection rate** — failing validations over all validations.

Every rate reports its denominator, and a rate over an empty denominator is `null`, never
zero. `metrics` also reports dispute counts and how often the learner turned out to be
right.

## Known limitations

* **Nothing measures whether any of this teaches.** The metrics are plumbing; the Stage 0
  gate is learning something real on hidden delayed-transfer items.
* **The evidence thresholds (2 independent passes, 3-attempt window, weight ≥ 6) are
  judgement, not measurement.** They are the transparent knobs IDEA.md asked for, and they
  are HYPOTHESIS until a real history exists to tune them against.
* **"`learner.md` shows no decimals" is a property of the generator, not of the file.** No
  computed number in the view is fractional, but node titles and the `Prefs` bullets are
  copied verbatim — a concept called "L2 norm to 0.5 precision" puts a decimal in the view.
* **`LT_KNOWN_THRESHOLD` / `LT_UNKNOWN_THRESHOLD` are read but unused** by the rule model.
  They exist for the BKT comparison the open questions require.
* **The probe's split score assumes the graph is roughly a DAG of strict prerequisites.** A
  flat graph with no edges degenerates to "every unknown node scores 1", and the tie-break
  (topological, then id) decides. Cycles fall back to id order deterministically.
* **Probe budget is per session, not per goal.** A learner who starts three sessions gets
  three budgets.
* **Feasibility arithmetic is crude**: `remaining nodes ÷ (minutes per session ÷
  LT_MINUTES_PER_NODE)` against `days to deadline × LT_SESSIONS_PER_WEEK ÷ 7`. It exists to
  say "this is tight", not to plan.
* **A split leaves the parent's aliases pointing at the retired parent**, since no child is
  the obvious heir. Merges repoint aliases; splits do not.
* **Concurrency is SQLite's**: WAL plus a 5-second busy timeout. Fine for one CLI, one cron
  and one UI; it is not a multi-writer server.
* **`teach` mode returns nodes that are `fragile` as well as `unknown`** when they sit
  lowest in the order — deliberate, but it means "the edge" is "the lowest node that is not
  known", not strictly "the lowest unknown node".
* **No `sources/` ingestion yet.** Stage 0 reads that folder whole into context; nothing in
  this module touches it.
* **A direct `api.record_answer` call with no `evaluation_method` still defaults to
  `host_llm`** — but an event whose method is *NULL* counts as trusted, because that is what
  a pre-migration row looks like. Nothing written from Stage 1 on can be NULL through the
  CLI, HTTP or MCP; a caller reaching past all three into raw SQL could forge one.
* **Idempotency keys never expire.** One row per keyed mutation, forever. There is no
  sweeper; at personal-tool volumes there does not need to be.
