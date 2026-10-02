# Learning Tutor — interface contracts

This file pins the shapes the parts of the system share: the repo and data layout, the event
schema, the `learner` CLI, the learner-svc and gateway HTTP routes and response shapes, and the
hard rules every module keeps. The reasoning behind them is in [IDEA.md](IDEA.md).

## Stack decisions (made 2026-09-05)

- **Python 3.11** for everything except `render-svc` (Node) and `web-ui` (Next.js). The
  learner CLI core *is* `learner-svc`'s core — one package, no rewrite between stages.
- Package name `learning_tutor`, managed with `uv` (`pyproject.toml` at repo root). Tests with
  `pytest`. Lint with `ruff`.
- FSRS = the `fsrs` PyPI package (py-fsrs 6.x). Applied to **items only**. Concept state is
  derived by rules from item evidence (rule-based evidence model first; BKT is a candidate
  behind a feature flag, never the default).
- SQLite is the event store from Stage 0. `events.jsonl` is an **export**, not the truth.
- Container split at Stage 2 by the extraction rule (different runtime / security boundary /
  scaling / second consumer): `gateway` (Python: gateway + llm + tutor + corpus modules in one
  process, each a FastAPI router that can be lifted out later), `learner-svc` (Python, the
  only stateful service, own volume), `render-svc` (Node), `web-ui` (Next.js). Four
  containers. Not six — see IDEA.md *Held, not adopted*.
- No machine-specific values. Everything configurable comes from env with a default.

## Repo layout

```
learning-tutor/
  IDEA.md CONTRACTS.md README.md
  pyproject.toml  uv.lock
  learning_tutor/
    __init__.py
    config.py            env → settings (LT_DATA_DIR, LT_VAULT_DIR, LT_GOAL, …)
    learner/             Stage 0 core. store.py (sqlite), schema.sql, events.py, graph.py,
                         items.py, evidence.py (rules), fsrs_sched.py, misconceptions.py,
                         disputes.py, holdouts.py, views.py (learner.md), export.py
    cli.py               `learner` entry point (argparse or typer)
    learner_svc/         Stage 1. FastAPI app over learner core. app.py, routes/*.py
    mcp_server.py        Stage 1. FastMCP server over learner core (same tool names as HTTP)
    llm/                 Stage 2. ai_providers.py (copied) + structured.py
    tutor/               Stage 2. prompts/<pack>/<version>/*.md, orchestrator.py (decisions)
    corpus/              Stage 2. ingest.py, store.py (FTS5 + embeddings), cite.py, roles.py
    gateway/             Stage 2. app.py (:5033), state machine probe→plan→teach
  skills/teach/SKILL.md  Stage 0. Plus skills/teach/prompts/*.md, rubric.md
  telegram/              Stage 1. Optional reminders: push a due question, record the tap
                         (run by any scheduler)
  render-svc/            Stage 2. Node: POST /render {mermaid} → svg; look-and-fix loop
  web-ui/                Stage 2. Next.js
  docker-compose.yml .env.example Dockerfile.gateway Dockerfile.learner
  .mcp.json.example      copy to .mcp.json to register the MCP server with an agent host
  tests/                 pytest; one file per learner module + api tests
  docs/                  INDEX.md, modules/*.md
```

## Data directory (`LT_DATA_DIR`, default `./data`)

```
data/
  learner/events.db      SQLite: events, items, item_versions, nodes, node_aliases, edges,
                         graph_versions, misconceptions, disputes, sessions, goals, holdouts
  learner/state.json     derived, regenerated on every write
  learner/notes.md       prose; model-editable; never numbers
  learner/events.jsonl   export only (`learner export`)
  sources/<goal_id>/     uploaded documents + sources.md + research output (Stage 0: read whole)
  corpus/corpus.db       Stage 2 FTS5 + embeddings
```

`LT_VAULT_DIR` (Obsidian vault, optional): `learner.md` and session logs
`sessions/<date>-<goal>.md` are written there when set; otherwise under `data/vault/`.

## Event schema (minimum; add columns only with a migration)

```
events(
  event_id TEXT PK (uuid7 or ulid), ts TEXT ISO8601 UTC, session_id TEXT, goal_id TEXT,
  node_id TEXT, item_version_id TEXT NULL, kind TEXT
    ('answer','teach_back','probe_answer','dispute','graph_revision','session_start',
     'session_end','misconception_step','note'),
  response TEXT NULL, correct INTEGER NULL (0/1), confidence INTEGER NULL (1-5 or NULL),
  idk INTEGER NOT NULL DEFAULT 0,
  assistance_level INTEGER NOT NULL DEFAULT 0 (0-6, see IDEA.md),
  channel TEXT ('claude-code','agent','telegram','web'),
  context TEXT ('in-session','delayed','transfer','probe'),
  prompt_version TEXT, grader_version TEXT, payload TEXT JSON NULL
)
```

Item status lifecycle: `TEACHING_ONLY → PRACTICE_EVIDENCE → MASTERY_ELIGIBLE`. Only
`PRACTICE_EVIDENCE` and above write evidence. Holdout = flag on a `MASTERY_ELIGIBLE` item;
holdouts are never served by `next` for teaching/review, only by `holdout-check`.

Misconception states: `suspected → active → weakened → resolved → recurred`.

Node state (derived, four colours + detail): `unknown | fragile | known | misconception`, plus
`independent_passes, assisted_passes, fails, last_delayed, transfer_passes, uncertainty`.

Edge types: `strict_prerequisite, recommended_background, course_sequence, co_requisite,
supports, misconception_for, transfer_related`. Provenance: `course, reference, model,
learner_evidence, human`. Every graph write bumps `graph_versions`; node ids are stable
(`n_<slug>_<4hex>`), aliases resolve names → ids.

## `learner` CLI (Stage 0) — the skill shells out to this; JSON in/out

```
learner init [--data-dir D]
learner goal add --id G --title T [--depth recognize|explain|apply|analyze] [--deadline YYYY-MM-DD] [--minutes-per-session N] [--purpose ...]
learner graph import --goal G --file graph.json        # {nodes:[{id?,title,aliases[]}], edges:[{from,to,type,provenance}]}
learner graph show --goal G [--format json|mermaid]    # mermaid colours nodes by state
learner graph revise --goal G --ops ops.json           # split/merge/add/remove with evidence migration
learner item add --node N --file item.json             # {stem,options[],answer,distractor_misconceptions{},kind,components[]}
learner item validate --item I --by <model-or-solver> --result pass|fail --notes ...   # → PRACTICE_EVIDENCE
learner item promote --item I                          # → MASTERY_ELIGIBLE (rule-checked)
learner session start --goal G --channel C → {session_id}
learner session end --session S [--summary "..."]
learner next --goal G [--session S] [--n 1] [--mode probe|review|teach]  # picks node/item; never a holdout
learner record answer --session S --item I --response R --correct 0|1 [--confidence 1-5] [--idk] --assistance 0-6 --context in-session|delayed|transfer|probe
learner record teach-back --session S --node N --score 0-3 --rubric-version V --assistance 0-6 [--notes ...]
learner misconception suspect|confirm-step|resolve --node N --claim "..." [--step reasoning|prediction|counterexample --outcome held|dropped]
learner dispute open --type "I already know this|ambiguous question|misclick|not on my exam|this edge is wrong|test me instead" --node N [--item I] --note ...
learner dispute settle --dispute D --outcome upheld|rejected --evidence ...
learner summary --goal G [--format md|json]            # regenerates learner.md and prints it
learner holdout-check --goal G                         # serves due holdout items (delayed/transfer metric)
learner metrics --goal G                               # 7-day holdout success, false-mastery, item rejection rate
learner export [--out events.jsonl]
learner log --session S --file session.md              # copies md-log into the vault
```

Additions decided 2026-09-05 after the skill was written (Stage 1 closes them):
`goal add` also takes `--assessment <text>` and `--source-priority alignment|authority`;
`record answer` / `record teach-back` / `misconception *` take `--prompt-version` and
`--grader-version` (defaults `teach/v1`; `mc-key-v1` for answers, `teach-back-v1` for teach-backs); `record answer` takes
`--evaluation-method host_llm|blind_solver|rubric|human` (default `host_llm`).

Exit code non-zero + `{"error": ...}` on stderr for every failure. All commands accept
`--json` (default when stdout is not a TTY).

## `learner-svc` HTTP (Stage 1) — same verbs, `/v1/...`

`POST /v1/goals`, `GET /v1/goals/{g}`, `POST /v1/graph/{g}/import|revise`, `GET /v1/graph/{g}`,
`POST /v1/items`, `POST /v1/items/{i}/validate|promote`, `POST /v1/sessions`,
`POST /v1/sessions/{s}/end`, `GET /v1/next?goal=&mode=&n=`, `POST /v1/events` (answer /
teach-back; body mirrors the CLI flags), `POST /v1/misconceptions/...`, `POST /v1/disputes`,
`GET /v1/summary/{g}?format=md|json`, `GET /v1/holdouts/{g}/due`, `GET /v1/metrics/{g}`,
`GET /v1/export`, `GET /healthz`.

Added 2026-09-05 (Stage 2), so that nothing but `learner-svc` ever opens `events.db` — the
gateway's receipts and passport are built from these, not from a second connection to the
database:

```
GET  /v1/events?goal=&node=&session=&kind=&since=&limit=   raw event rows, newest first, limit capped at 1000
GET  /v1/disputes?goal=&status=&node=                      dispute rows (`node` is an addition; `goal` filters by that goal's nodes)
GET  /v1/misconceptions?goal=&node=&state=                 misconception rows with their confirmation steps
GET  /v1/passport?goal=&format=zip|json                    application/zip: events.jsonl, state.json, learner.md,
                                                           notes.md, goals.json, graph.json, disputes.json,
                                                           artifacts/sessions/*.md, MANIFEST.json
POST /v1/sessions/{s}/log  {markdown, filename?}           -> {session_id, log, log_path} — the CLI's `learner log`,
                                                           by value rather than by path
```

MCP gains the matching tools `learner_events`, `learner_disputes`, `learner_misconceptions`
and `learner_log` (23 tools in total).

Adopted after reviewing the tool contract of the open-source Tutor MCP server
(github.com/ArnaudGuiovanna/tutor-mcp),
binding from Stage 1: every mutation accepts an optional `idempotency_key` (same key + same
body → the first response replayed; same key + different body → 409); and every evidence
event carries `evaluation_method` (`host_llm` | `blind_solver` | `rubric` | `human`) — only
`blind_solver`, `rubric` and `human` evaluations can move an item past `TEACHING_ONLY` or a
node to `known`. Stage 1 adds the column by migration. Pydantic models live in `learning_tutor/learner/models.py`
and are shared by CLI, HTTP and MCP. Default port 5034 internal (`LEARNER_PORT`).

MCP tool names = the CLI verbs with underscores: `learner_next`, `learner_record_answer`,
`learner_summary`, … `structured_output=True`. One server file, stdio transport by default,
`--http` flag for the gateway.

## Gateway HTTP (Stage 2) — `:5033`

`POST /api/goals` (goal contract), `POST /api/goals/{g}/sources` (upload), `POST
/api/goals/{g}/research` (returns proposed source list; `POST .../research/approve`),
`POST /api/goals/{g}/probe/start|answer`, `POST /api/goals/{g}/plan` (→ graph + mermaid),
`POST /api/goals/{g}/teach/next|answer|teach-back|hint`, `GET /api/goals/{g}/map`
(curriculum map + learner path), `GET /api/goals/{g}/receipts/{node}`, `GET
/api/passport` (export zip/json), `POST /api/render` (proxy to render-svc), `GET
/api/health`. The state machine lives in `gateway/state.py`; decisions (next question,
pass/fail, strategy change, session end, graph revision) live in `tutor/orchestrator.py`
and are the same code path the skill's instructions describe — `tutor-svc` supplies
prompts, never decisions.

### Gateway response shapes (pinned 2026-09-05)

All responses JSON; errors `{"error": str, "code": str}` with 400/404/409/502. Every mutating
route accepts `Idempotency-Key` header.

```
POST /api/goals            body GoalContract {goal_id?, title, concept, depth, purpose, deadline?,
                           minutes_per_session, sessions_per_week?, assessment?, source_priority?,
                           target_capabilities?[], transfer_required?: bool, domain: math-cs|empirical|procedural}
                           → {goal: Goal, sources_dir, phase: "grounding"}
GET  /api/goals            → {goals: [Goal & {phase, node_count, known, fragile, unknown, misconception}]}
GET  /api/goals/{g}        → {goal, phase: grounding|plan|probe|teach|done, session: Session|null, summary_md}
POST /api/goals/{g}/sources    multipart file(s) + role → {sources: [Source]}
GET  /api/goals/{g}/sources    → {sources: [Source], sources_md: str|null}
POST /api/goals/{g}/research   {topic?, guidelines?} → {proposal_id, sources: [{title,url,role,why}]}
POST /api/goals/{g}/research/approve {proposal_id, accept: [url]} → {approved: n}
POST /api/goals/{g}/plan       {} → {graph_version, mermaid, nodes:[Node], edges:[Edge], course_prior_used: bool, verification: [{node_id, status: cited|abstain, citations[]}], warnings?: [str]}
                           (plan uses the corpus structure as a prior; every node claim is cite-or-abstain)
                           (`warnings` is present only when something is wrong with the plan itself —
                            today: a graph still under LT_PLAN_MIN_NODES nodes after one retry)
POST /api/goals/{g}/plan/approve {ops?: []} → {graph_version}   (learner's one manual review)
POST /api/goals/{g}/probe/start → {session_id, budget, question: Question|null, done: bool}
POST /api/goals/{g}/probe/answer {session_id, item_id, response, confidence?, idk?} → {recorded: Event, node_state, next: Question|null, done: bool, asked: n, budget}
POST /api/goals/{g}/teach/next {session_id} → {node: Node, step: {strategy, markdown, mermaid?, svg?, latex_ok: bool, citations[]}, checkpoint: Question, assistance_level: 0}
POST /api/goals/{g}/teach/hint {session_id, item_id, level} → {level, hint_markdown}   (level ≤5; 6 only after an attempt → 409 otherwise)
POST /api/goals/{g}/teach/answer {session_id, item_id, response, confidence?, idk?, assistance_level} → {correct, recorded: Event, node_state, feedback_markdown, reveal_allowed: bool, misconception_suspected: {claim}|null, decision: continue|repeat|back_up|switch_strategy|teach_back_due|end_session}
POST /api/goals/{g}/teach/teach-back {session_id, node_id, explanation} → {score 0-3, rubric_version, feedback_markdown, recorded: Event}
POST /api/goals/{g}/misconception/step {session_id, node_id, claim, step, learner_response} → {state, next_step|null}
POST /api/goals/{g}/dispute    {type, node_id, item_id?, note} → {dispute_id, check: {items: [Question]}|null}
POST /api/goals/{g}/session/end {session_id, summary?} → {session, log_path}
GET  /api/goals/{g}/map        → {curriculum: {mermaid, nodes, edges}, path: {mermaid, order: [node_id]}, states: {node_id: NodeState}}
GET  /api/goals/{g}/receipts/{node_id} → {node, state, evidence: [Event], why: [str], misconceptions: [], disputes: []}
GET  /api/goals/{g}/metrics    → learner-svc metrics passthrough
GET  /api/passport             → application/zip: learner-svc's `GET /v1/passport` zip (events.jsonl, state.json,
                           learner.md, notes.md, goals.json, graph.json, disputes.json, artifacts/sessions/)
                           plus gateway-state.json and any rendered artifacts/*.svg
POST /api/render               {mermaid, theme?} → proxy to render-svc
GET  /api/health               → {ok, services: {learner, render, llm, corpus: ok|down|unconfigured|degraded}}

Question = {item_id, item_version_id, node_id, node_title, stem, options: [{key, text}], allow_idk: true,
            ask_confidence: bool, kind, assistance_level}
NodeState = learner-svc node state (state, independent_passes, assisted_passes, self_graded_passes,
            fails, last_delayed, transfer_passes, uncertainty, review_priority)
```

Web UI reads the gateway only (`NEXT_PUBLIC_GATEWAY_URL`, default same origin `/api`).

### Gateway response shapes — additions (added 2026-09-05; the block above is unchanged)

```
POST /api/goals/{g}/teach/interrupt {session_id, node_id, question}
                           → {answer_markdown, citations: [Citation], step_unchanged: true,
                              refused_checkpoint_answer: bool}
                           "Wait, why?" mid-step (IDEA.md *What's missing* item 10; SKILL.md
                           *Interrupts and disputes*). Answered inline against the step already
                           served: no step advance, no assistance-level change, no evidence
                           written. `step_unchanged` is always true — it is the client's licence
                           to keep rendering the step it has.
                           Citations are `cite_or_abstain` on the answer's leading claim; an
                           abstention is visible **in `answer_markdown`** as a one-line
                           "No source found for this — <reason>" note, never a silently empty list.
                           `refused_checkpoint_answer: true` replaces the answer with
                           checkpoint.md's hint-ladder rule when the interrupt is asking for the
                           checkpoint's key ("what is the answer", "which option is correct").
                           409 `not_teaching` outside the teach phase, 409 `wrong_session` for a
                           session other than the open one, 409 `not_current_node` for any node
                           but the one being taught.

POST /api/goals/{g}/plan   → … + feasibility: Feasibility|null
                           Present when the goal has a session budget (`minutes_per_session > 0`);
                           `null` when there is none to divide by, and web-ui then falls back to
                           its own estimate (`src/lib/feasibility.ts`).

Citation   = corpus `cite_or_abstain` citation, verbatim: {source_id, chunk_id, title,
              locator, quote, role, proves: alignment|correctness|learner_evidence,
              score, support, flagged} — the same objects `teach/next` returns in
              `step.citations`
Feasibility = {sessions_needed: int, sessions_available: int|null,
               verdict: comfortable|tight|not-feasible|unknown, assumption: str,
               nodes_remaining, pace_min, minutes_needed, minutes_per_session,
               sessions_left, minutes_available, fits: bool|null, statement, options: [str]}
              (the first four are web-ui's `types.ts::Feasibility` names, matched exactly;
               `sessions_available` is `sessions_left` under the UI's name. The rest is the
               arithmetic that produced them — PACE_MIN is an assumption, and `assumption`
               says so in words.)
```

### Gateway response shapes — holdouts (added 2026-09-05; the blocks above are unchanged)

Until now the gateway never called `POST /v1/items/{i}/promote` and had no holdout route, so
nothing the product does could put an item into the hidden holdout pool or serve one from
it — the delayed/transfer metrics (`learner/metrics.py`, IDEA.md *Staging* gates) had a
denominator of zero by construction. Two routes and one orchestrator rule close that.

```
GET  /api/goals/{g}/holdouts/due
                           → {goal_id, due: n, picks: [Question & {context: delayed|transfer, reason}],
                              note}
                           A proxy of learner-svc `GET /v1/holdouts/{g}/due`. Each pick is the
                           contract's `Question` (no key) plus the check's `context` — `transfer`
                           when the item version carries a `surface_form`, `delayed` otherwise —
                           and learner-svc's `reason`. Serving is the only side effect learner-svc
                           has (none here: `next` never returns holdouts, and this route never
                           writes). Any phase after `plan`; the phase machine is not touched.

POST /api/goals/{g}/holdouts/answer {item_id, response, context: delayed|transfer,
                                     session_id?, confidence?, idk?}
                           → {correct, context, recorded: Event, node_state, counts_toward_mastery}
                           Grades against the key the gateway stored when it authored the item
                           (409 `unknown_item` otherwise — a holdout promoted outside the gateway
                           has no key here) and records through `POST /v1/events` with the given
                           `context` and assistance 0 (holdout checks have no hint ladder). No
                           feedback markdown, no decision, no step, no phase change. Accepts the
                           `Idempotency-Key` header like every mutating route: same key + same body
                           replays learner-svc's receipt, so a retried answer is one event.
```

**Orchestrator rule (auto-promote).** After `POST /api/goals/{g}/teach/answer` records a
checkpoint, the gateway reads the item's recorded uses from learner-svc (`GET /v1/events`
on the node, rows of kind `answer`/`probe_answer` for that item) and, when
`uses >= LT_PROMOTE_MIN_USES` (default 3, the same variable learner-svc's own rule reads)
and the item is `PRACTICE_EVIDENCE`, calls `POST /v1/items/{i}/promote` with the request's
`Idempotency-Key` scoped `:promote`. learner-svc keeps the rule (`items.promote`: validated,
enough uses, at least one correct, not all IDK, no open ambiguity dispute) and the holdout
assignment; the gateway only decides *when to ask*. A refusal (400) is reported in the
answer's `promotion` key, never raised. The response gains
`promotion: {attempted: bool, promoted: bool, status, holdout: bool|null, uses, min_uses,
reason}`. The CLI/MCP path is unchanged; this makes the same call from the product.

## Study tools (added 2026-09-24; nothing above is changed)

Four tools that run **without any model**: fix a goal's date/cadence, import a question bank,
flashcards, and comparison/definition tables. Anything that needs judgement — writing new
cards or tables, blind-solving imported questions — is done by the agent host (Mode B, e.g.
Claude Code) through the CLI/MCP verbs below, never by the gateway calling an LLM.

**Rules they keep.** Keys never reach a client before an attempt (practice grades
server-side; a card's back comes from a separate `reveal` call). A flashcard flip is
**self-report**: kind `card_review`, `evaluation_method = self_report`, schedules FSRS,
never evidence. Imported questions start `TEACHING_ONLY` like every item; they count as
evidence only after a blind check by a solver that is not their author. Table fill-in is
practice and is not recorded. A question whose latest blind check failed is held back
from practice and from the Anki export (its key may be wrong) and listed by `bank review`.

**Core additions.** Migration 3 (`learner/schema_v3.sql`): `study_tables`;
`items.source_key` (dedupe on re-import, unique when set); `item_versions.explanation`,
`item_versions.source`. Event kind `card_review` (not an evidence kind). Evaluation method
`self_report` (valid, never trusted). Item kind `card` (`stem` = front, `answer` = back,
no options; cannot be validated). Settings: `LT_PRACTICE_NEW_PER_DAY` (20),
`LT_CARDS_NEW_PER_DAY` (20), `LT_DELAYED_MIN_HOURS` (20 — an answer on an item last
answered at least this long ago is recorded with context `delayed`; a judgement, not a
measurement).

**Markdown the importer reads** (the format of a bank file under `data/sources/<goal>/`,
and ordinary notes): questions `N. stem … [tag]` with options `A) …` on the following
lines, answer keys `| N | tag | LETTER | explanation |`, node titles from headings
`## <tag> <title>`, flashcards from paragraphs that open with a bold term
(`**Term.** definition …`, following bullets included), tables from GFM pipe tables
(title = the nearest headings).

### `learner` CLI / MCP additions

```
learner goal update --goal G [--deadline D|""] [--minutes-per-session N] [--title T] [--purpose P] [--assessment A] [--depth D]
learner study import --goal G --file notes.md [--key-file key.md] [--what questions,cards,tables] [--author A] [--dry-run]
learner bank pending --goal G [--limit N]         # unchecked questions, stem + options, NO keys
learner bank review --goal G                      # questions that failed a blind check, WITH keys
learner item blind-check --item I [--answer B] --by SOLVER [--ambiguous] [--notes ...]   # key compared server-side
learner practice next --goal G [--n N]            # due first, then new (daily cap); no keys
learner practice answer --item I --response B [--confidence 1-5] [--idk]
learner cards add --goal G --file cards.json      # [{front, back, node}] authored by the agent host
learner cards next --goal G [--n N] | cards reveal --item I | cards review --item I --rating again|hard|good|easy
learner cards export --goal G [--format tsv|csv] [--include cards|questions|all] [--out F]
learner table list --goal G | table show --table T | table save --goal G --file table.json | table cards --table T
```

MCP tools, same verbs with underscores: `learner_goal_update`, `learner_study_import`,
`learner_bank_pending`, `learner_bank_review`, `learner_item_blind_check`,
`learner_practice_next`, `learner_practice_answer`, `learner_cards_add`,
`learner_cards_next`, `learner_card_reveal`, `learner_card_review`, `learner_cards_export`,
`learner_tables_list`, `learner_table_get`, `learner_table_save`, `learner_table_cards`.

### learner-svc additions (`/v1`)

`PATCH /v1/goals/{g}`, `POST /v1/study/{g}/import`, `GET /v1/study/{g}`,
`GET /v1/bank/{g}/pending`, `GET /v1/bank/{g}/review`, `POST /v1/items/{i}/blind-check`,
`GET /v1/practice/{g}/next`, `POST /v1/practice/answer`, `POST /v1/cards/{g}`,
`GET /v1/cards/{g}/next`, `POST /v1/cards/{i}/reveal`, `POST /v1/cards/{i}/review`,
`GET /v1/cards/{g}/export` (text), `GET /v1/tables/{g}`, `POST /v1/tables/{g}`,
`GET /v1/tables/{g}/{t}`, `POST /v1/tables/{g}/{t}/cards`. Bodies mirror the CLI flags.

### Gateway additions (`:5033`) — what web-ui builds against

```
PATCH /api/goals/{g}     {deadline?: "YYYY-MM-DD"|"" , minutes_per_session?, sessions_per_week?,
                          title?, purpose?, assessment?, depth?}   absent = unchanged, deadline "" = clear
                         → {goal: Goal (merged, as GET /api/goals/{g}), changed: {field: {from, to}},
                            feasibility: Feasibility|null}   (stored plan feasibility is recomputed)

GET  /api/goals/{g}/study → {bank: BankCounts, cards: CardCounts, tables: [TableSummary],
                             importable: [{path, name, bytes}]}   (markdown files under the goal's sources dir)
     BankCounts = {total, checked, unchecked, rejected, due_now, new_available, new_today, new_limit}
     CardCounts = {total, due_now, new_available, new_today, new_limit}

POST /api/goals/{g}/study/import
     multipart: file (markdown, required), key_file (markdown, optional),
                what (repeatable: questions|cards|tables; default all), dry_run ("1"|"0")
     or JSON {path, key_path?, what?: [..], dry_run?}   (paths relative to the goal's sources dir)
     → ImportReport {source, dry_run,
                     questions: {parsed, imported, skipped_existing, problems: [str]} | null,
                     cards: {parsed, imported, skipped_existing} | null,
                     tables: {parsed, imported, skipped_existing} | null,
                     nodes_created: [{node_id, title}]}

GET  /api/goals/{g}/practice/next?n=1
     → {questions: [PracticeQuestion], counts: BankCounts, done: bool, note: str|null}
     PracticeQuestion = {item_id, item_version_id, node_id, node_title, stem (markdown),
                         options: [{key, text}], allow_idk: true, checked: bool,
                         reason: "due"|"new", context: "delayed"|"in-session"}
POST /api/goals/{g}/practice/answer {item_id, response: option key, confidence?: 1-5, idk?: bool}
     → {item_id, correct, idk, your_answer: {key, text}|null, correct_answer: {key, text},
        explanation: str|null, checked, counts_toward_mastery, context,
        node_state: NodeState, schedule: {due, rating}|null, note: str|null}
GET  /api/goals/{g}/practice/review
     → {items: [{item_id, node_title, stem, options: [{key, text}], answer: {key, text},
                 solver_answer: {key, text}|null, ambiguous, notes, explanation}]}
     (questions a blind solver disagreed with — shown to the learner to judge, keys included)

GET  /api/goals/{g}/cards/next?n=1
     → {cards: [{item_id, node_id, node_title, front (markdown), reason: "due"|"new"}],
        counts: CardCounts, done: bool}
POST /api/goals/{g}/cards/reveal {item_id} → {item_id, front, back (markdown), source}
POST /api/goals/{g}/cards/review {item_id, rating: again|hard|good|easy}
     → {item_id, rating, schedule: {due, state}, counts_toward_mastery: false, note}
GET  /api/goals/{g}/cards/export?format=tsv|csv&include=cards|questions|all
     → text/tab-separated-values (or text/csv) attachment, Anki-importable: front, back, tags

GET  /api/goals/{g}/tables → {tables: [TableSummary]}
     TableSummary = {table_id, title, node_id|null, node_title|null, columns: [str],
                     row_count, source|null, author, created_at}
GET  /api/goals/{g}/tables/{t} → TableSummary & {rows: [[str]]}
POST /api/goals/{g}/tables/{t}/cards → {parsed, imported, skipped_existing}
```

Errors as everywhere else: `{"error", "code"}`; 404 unknown goal/item/table, 400 a rejected
request (e.g. a `card` item sent to `practice/answer`), 409 idempotency conflict.

## Exam blueprint, mixed practice and sealed mock exams (added 2026-09-26; nothing above changes meaning)

Still no model anywhere. Four additions aimed at a fixed-date exam:

* **Blueprint** — the official item count per concept (for EGEL Plus ISOFT: 4 areas, 14
  subáreas, 143 items). Stored per goal as data, never as a constant. A row names its concept
  by a `ref` (the source's own tag, e.g. `3.2`) that is resolved through the graph's aliases
  at read time, so a later merge or split of that node keeps the row pointing at the right
  concept.
* **Mixed, weighted practice** — new questions are no longer served in import order. Each
  pick goes to the concept furthest below its blueprint share of the questions seen so far
  (equal shares when the goal has no blueprint), so areas interleave from the first day.
  `focus` (an area code or a concept) narrows practice to that area for a first pass.
* **Shuffled options** — every served question carries `order`, the stored option indexes
  in the order shown (A = `order[0]`). Keys are letters of *that* order. The answer call
  takes the key plus `order` back (or the option's own text); the event records
  `payload.shown_order`. Without `order`, the server uses the order it would serve today
  (a per-item, per-local-day shuffle), so a client that forgets it is still graded right on
  the same day. Stored option order never changes.
* **Sealed mock exams** — items in pool `mock` are never served by practice, cards or the
  Anki export until they have been answered in a mock. A mock draws checked sealed items
  weighted by the blueprint, shuffles questions and options, gives no feedback while open,
  and grades everything on submit. Its answers are ordinary `answer` events (`rubric`,
  `bank-key-v1`, `payload.mock = session_id`); an unanswered item is recorded as `idk`.
  After the mock those items join practice rotation like any other.

**Core additions.** Migration 4 (`learner/schema_v4.sql`): table `blueprints (goal_id, ref,
area, area_title, title, exam_items, position, exam, source, updated_at)`, primary key
`(goal_id, ref)`; column `items.pool` (`practice` | `mock`, default `practice`). Graph
`merge`/`split` also move `study_tables.node_id` to the surviving node. Setting
`LT_MOCK_MINUTES_PER_ITEM` (2.9 — the ISOFT Disciplinar timetable: 420 min for 143 items).
`api.record_answer` takes an optional `extra` dict merged into the event payload.

### `learner` CLI / MCP additions

```
learner goal blueprint --goal G [--file blueprint.json]   # set (replace) with --file; show without
learner study import ... [--pool practice|mock]           # mock = sealed; questions only
learner practice next --goal G [--n N] [--focus 3|3.2|<node>]
learner practice answer --item I --response B [--order 2,0,1] [--confidence 1-5] [--idk]
learner progress --goal G                                  # the Progress shape below
learner mock start --goal G [--n N] [--minutes M]         # N defaults to every checked sealed item (≤ 60)
learner mock show --session S                              # open: questions, no keys; submitted: result
learner mock submit --session S --file answers.json        # [{item_id, response, order?, confidence?}]
learner mock list --goal G
```

Blueprint file: `{"exam": str, "source": str, "areas": [{"code": "1", "title": str,
"subareas": [{"ref": "1.1", "title": str, "items": 12}, ...]}, ...]}`. Every `ref` must
resolve to a live concept of the goal, or nothing is written.

MCP tools: `learner_goal_blueprint` (get, or set with `blueprint`), `learner_progress`,
`learner_mock_start`, `learner_mock_show`, `learner_mock_submit`, `learner_mock_list`;
`learner_practice_next` gains `focus`, `learner_practice_answer` gains `order`,
`learner_study_import` gains `pool`.

### learner-svc additions (`/v1`)

`GET|PUT /v1/goals/{g}/blueprint`, `GET /v1/progress/{g}`, `GET /v1/mocks/{g}`,
`POST /v1/mocks/{g}` `{n?, minutes?, channel?}`, `GET /v1/mock/{s}`,
`POST /v1/mock/{s}/submit` `{answers}`; `GET /v1/practice/{g}/next?n=&focus=`;
`POST /v1/practice/answer` gains `order`; `POST /v1/study/{g}/import` gains `pool`.

### Gateway additions (`:5033`)

```
GET  /api/goals/{g}/blueprint → Blueprint     (404 {"error","code"} when the goal has none)
     Blueprint = {goal_id, exam, source, total_items,
                  areas: [{code, title, exam_items, share,          // share of total_items, 0..1
                           subareas: [{ref, title, node_id|null, node_title|null,
                                       exam_items, share}]}]}

GET  /api/goals/{g}/practice/next?n=1&focus=      focus: "" (mixed) | area code | concept ref/id
     → as before, plus counts.focus: {kind: "mixed"|"area"|"node", label}
       and each PracticeQuestion gains {order: [int], ref: str|null,
                                         area: {code, title}|null}
POST /api/goals/{g}/practice/answer {item_id, response: key, order: [int], confidence?, idk?}
     → as before (your_answer / correct_answer keys are letters of the shown order)

GET  /api/goals/{g}/progress → Progress
     Progress = {goal_id, today: "YYYY-MM-DD", deadline: str|null, days_left: int|null,
       has_blueprint: bool,
       totals: Tally,                        // over every concept of the goal
       disciplinar: {weighted_accuracy: int|null, coverage: number,  // 0..1 of exam weight with ≥1 first attempt
                     note: str|null},        // weighted by blueprint share; null until every area has ≥5 first attempts
       areas: [AreaProgress],                // blueprint order; [] without a blueprint
       unassigned: [NodeProgress],           // concepts not in the blueprint
       activity: [{date, answers, correct, cards}],   // the last 28 local days, oldest first
       forecast: [{date, due}],              // questions + cards coming due per local day, today..deadline (≤ 60 days)
       mocks: [MockSummary]}
     Tally = {bank: int, checked: int, sealed: int, seen: int,
              first_attempts: int, first_correct: int, low: int|null, high: int|null,  // 95% Wilson range, percent
              attempts: int, correct: int, due_now: int}
     AreaProgress = {code, title, exam_items, share, ...Tally, subareas: [NodeProgress]}
     NodeProgress = {ref: str|null, node_id, title, exam_items: int|null, share: number|null,
                     state: "unknown"|"fragile"|"known"|"misconception", ...Tally}

GET  /api/goals/{g}/mocks → {sealed_available: int, sealed_unchecked: int,
                             sealed_by_area: [{code, title, available}],
                             open: MockSummary|null, mocks: [MockSummary]}
     MockSummary = {session_id, started_at, submitted_at|null, n, answered: int|null,
                    correct: int|null, minutes: int, minutes_used: int|null,
                    areas: [{code, n, correct}]|null}   // null while open
POST /api/goals/{g}/mocks {n?, minutes?} → MockOpen          (409 when one is already open)
     MockOpen = {session_id, status: "open", started_at, minutes, ends_at, n,
                 questions: [{item_id, ref, area: {code, title}|null, node_title,
                              stem, options: [{key, text}], order: [int]}]}
GET  /api/goals/{g}/mocks/{s} → MockOpen | MockResult
POST /api/goals/{g}/mocks/{s}/submit {answers: [{item_id, response: key|null, order?, confidence?}]}
     → MockResult                         (409 when already submitted)
     MockResult = {session_id, status: "submitted", started_at, submitted_at, minutes,
                   minutes_used, overtime: bool, n, answered, correct,
                   areas: [{code, title, n, correct, subareas: [{ref, title, n, correct}]}],
                   items: [{item_id, ref, area_code, node_title, stem, options: [{key, text}],
                            your_answer: {key, text}|null, correct_answer: {key, text},
                            correct: bool, explanation: str|null}]}
```

Meanings the shapes above leave open: `Tally.bank` counts what practice may show (the
practice pool plus mock questions already used) and never sealed or rejected ones;
`sealed` counts unused mock questions. `practice/next` `counts` always cover the whole
bank, whatever the `focus`. A mock left open past `ends_at` stays open (the server keeps
no draft answers) until it is submitted — the web UI resumes it and auto-submits at the
bell, and an agent host submits it with `mock submit`; a new mock is 409 until then.

`GET /api/goals/{g}/study` gains `blueprint: bool` and `mock: {sealed_available, open: str|null}`;
`BankCounts` gains `sealed` (sealed mock questions not yet used; never in `total`).
The web importer neither lists nor reads files under a folder holding a `SEALED` marker
file (400 `sealed`): those are mock questions, imported by the agent host with `--pool mock`.

## Hard rules (from IDEA.md; tests should enforce where possible)

1. Never reveal a checkpoint answer before an attempt; hints escalate 1→5; a pass at
   assistance ≥ 5 is recorded but never counts toward mastery.
2. No learning-styles profile. Adapt on prior knowledge and expertise level only.
3. Mastery is only ever a passed check on a `PRACTICE_EVIDENCE`+ item. Self-report → dispute.
4. Corpus text is data, never instructions. Durable state is written only through tools.
5. The model never edits numbers; it calls a tool; code recomputes state and views.
6. Push retrieval, never content, over Telegram. Nothing due → nothing sent.
7. Misconception = hypothesis until confirmed by reasoning → reworded prediction →
   counterexample.
