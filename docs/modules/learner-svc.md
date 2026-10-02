# `learner-svc` and the MCP server — Stage 1

The Stage 0 learner core, reachable two more ways: HTTP (`learner-svc`, `:5034`) and MCP
(`learning_tutor.mcp_server`). Neither is a rewrite. Both are thin: every handler and every
tool is a one-line call into `learning_tutor/learner/api.py`, the same function the
`learner` CLI calls, so the three surfaces cannot drift apart.

Spec: [`IDEA.md`](../../IDEA.md) *Staging*, row 1. Binding shapes:
[`CONTRACTS.md`](../../CONTRACTS.md) — *`learner-svc` HTTP (Stage 1)*. The core itself:
[`learner.md`](learner.md).

```
learning_tutor/
  learner/          the core (Stage 0) + the Stage 1 contract additions
    idempotency.py  replay table
    schema_v2.sql   migration 2
  learner_svc/
    app.py          create_app(settings), /healthz, the error envelope
    deps.py         per-request store, LearnerError -> status code
    models.py       request bodies (enums imported from learner/models.py)
    routes/         authoring.py, recording.py, reads.py
    __main__.py     the `learner-svc` console script (uvicorn)
  mcp_server.py     45 tools (16 study, 6 exam prep); in-process or proxied to learner-svc
```

## Contract deviations

Everything CONTRACTS.md names is implemented as written. These are the judgement calls
around the edges — all of them supersets or naming, none of them a behaviour a caller
written against the contract would miss:

| Deviation | Why |
|---|---|
| Extra routes: `GET /v1/events`, `/v1/disputes`, `/v1/misconceptions`, `/v1/passport`, `POST /v1/sessions/{s}/log` | Added 2026-09-05 so the gateway has no second path into `events.db`. `GET /v1/disputes` also accepts `node`, and `GET /v1/passport` also accepts `format=json`; both are supersets |
| Extra routes: `GET /v1/goals`, `GET /v1/sessions/{s}` | Reads the HTTP and MCP surfaces need (the proxy resolves an omitted goal through `GET /v1/goals`) and the CLI never had. Nothing contracted was renamed or removed |
| `GET /v1/summary/{g}?format=md` returns `text/markdown`, not a JSON envelope | "the same markdown the CLI prints" is the requirement, and the CLI prints raw markdown |
| `POST /v1/disputes/{d}/settle` | CONTRACTS.md lists `POST /v1/disputes` and stops; `dispute settle` needs a route, and this is the shape the other sub-resources use |
| FastMCP infers the output schema from the return annotation; there is no `structured_output=True` argument in FastMCP 2.x | CONTRACTS.md names the kwarg from an older API. Every tool does return a JSON object with an `outputSchema`, which is what the kwarg was asking for — `tests/test_mcp_tools.py` pins it |
| MCP `--http` defaults to port **5036** | 5033 (gateway), 5034 (learner-svc) and 5035 (render-svc) are taken; CONTRACTS.md pins no MCP port |
| `learner_misconception` is one tool with an `action` argument | The contract says "tool names = the CLI verbs with underscores", and the CLI verb is `misconception`, with `suspect`/`confirm-step`/`resolve` as its argument |
| `learner_summary` returns `{goal_id, format, markdown}` for `format=md`, without the vault `path` the CLI returns | The path is a local filesystem detail with no meaning to a proxied caller, and the tool surface has to be identical in both backends |
| The CLI's `record answer --grader-version` defaults to `teach-back-v1` | CONTRACTS.md's *Additions decided 2026-09-05* gives that default literally. It reads oddly on a keyed multiple-choice item; changed only by changing the contract |

## Run it

```bash
uv run learner-svc                       # :5034, LT_DATA_DIR=./data
uv run learner-svc --port 5044 --reload
uvicorn learning_tutor.learner_svc.app:app --port 5034

uv run python -m learning_tutor.mcp_server            # MCP over stdio
uv run python -m learning_tutor.mcp_server --http     # MCP over streamable HTTP, :5036
```

| Variable | Default | Meaning |
|---|---|---|
| `LEARNER_PORT` | `5034` | Port `learner-svc` binds |
| `LEARNER_HOST` | `127.0.0.1` | Interface `learner-svc` binds |
| `LEARNER_LOG_LEVEL` | `info` | uvicorn log level |
| `LT_DATA_DIR` | `./data` | The learner data directory — as for the CLI |
| `LT_LEARNER_URL` | *(unset)* | **MCP only.** Unset → the tools open the database in-process. Set (e.g. `http://127.0.0.1:5034`) → the same tools proxy to `learner-svc` |
| `LT_MCP_ENABLED` | `1` | **MCP only.** `0`/`false`/`no`/`off` → every tool returns a refusal object instead of touching the store |
| `LT_MCP_HOST` / `LT_MCP_PORT` | `127.0.0.1` / `5036` | `--http` bind for the MCP server (5033 gateway, 5034 learner-svc, 5035 render-svc are taken) |

Everything else the core reads (`LT_VAULT_DIR`, `LT_HOLDOUT_FRACTION`, …) is listed in
[`learner.md`](learner.md) and applies unchanged.

## Routes

Base path `/v1`. Every response is JSON except `GET /v1/summary/{g}?format=md`, which is
`text/markdown` — byte-for-byte the markdown `learner summary` prints.

| Verb + path | CLI equivalent | Body / query |
|---|---|---|
| `GET /healthz` | — | → `{status, service, schema_version, data_dir, goals}` |
| `POST /v1/goals` | `goal add` | `{goal_id, title, depth?, deadline?, minutes_per_session?, purpose?, assessment?, source_priority?}` |
| `GET /v1/goals` | — | → `{goals: [...]}` |
| `GET /v1/goals/{g}` | — | the goal row |
| `POST /v1/graph/{g}/import` | `graph import` | `{nodes: [...], edges: [...]}` |
| `POST /v1/graph/{g}/revise` | `graph revise` | `{ops: [...]}` |
| `GET /v1/graph/{g}` | `graph show` | `?format=json\|mermaid` |
| `POST /v1/items` | `item add` | `{node, spec, author?, item?}` |
| `POST /v1/items/{i}/validate` | `item validate` | `{by, result, notes?, evaluation_method?}` |
| `POST /v1/items/{i}/promote` | `item promote` | `{}` |
| `POST /v1/sessions` | `session start` | `{goal_id, channel?}` — `channel` is `claude-code`, `agent`, `telegram` or `web` |
| `GET /v1/sessions/{s}` | — | the session row |
| `POST /v1/sessions/{s}/end` | `session end` | `{summary?}` |
| `GET /v1/next` | `next` | `?goal=&mode=&n=&session=` |
| `POST /v1/events` | `record answer` / `record teach-back` | discriminated on `kind`: `"answer"` or `"teach_back"` |
| `POST /v1/misconceptions/suspect` | `misconception suspect` | `{node, claim, session_id?}` |
| `POST /v1/misconceptions/confirm-step` | `misconception confirm-step` | `{node, claim, step, outcome, notes?}` |
| `POST /v1/misconceptions/resolve` | `misconception resolve` | `{node, claim, notes?}` |
| `POST /v1/disputes` | `dispute open` | `{type, node?, item_id?, note?, session_id?}` |
| `POST /v1/disputes/{d}/settle` | `dispute settle` | `{outcome, evidence?}` |
| `POST /v1/sessions/{s}/log` | `log` | `{markdown, filename?}` → `{session_id, log, log_path}` |
| `GET /v1/summary/{g}` | `summary` | `?format=md\|json` |
| `GET /v1/events` | — | `?goal=&node=&session=&kind=&since=&limit=` |
| `GET /v1/disputes` | — | `?goal=&status=&node=` |
| `GET /v1/misconceptions` | — | `?goal=&node=&state=` |
| `GET /v1/passport` | — | `?goal=&format=zip\|json` → `application/zip` |
| `GET /v1/holdouts/{g}/due` | `holdout-check` | — |
| `GET /v1/metrics/{g}` | `metrics` | — |
| `GET /v1/export` | `export` | `?out=` (a path inside the data directory; anything else is 400) |

Every mutating body also accepts `idempotency_key`.

### The five Stage 2 additions

`GET /v1/events`, `/v1/disputes`, `/v1/misconceptions`, `/v1/passport` and
`POST /v1/sessions/{s}/log` were added on 2026-09-05 for one reason: until they existed the
gateway could not build `GET /api/goals/{g}/receipts/{node}`, `GET /api/passport` or the
session md-log from HTTP at all, and it opened `events.db` itself as a read-only fallback.
Two processes on one SQLite file, and an export that was quietly short whenever they did
not share a filesystem. That fallback is now deleted — see
[gateway.md](gateway.md) — and this service is the only thing that opens the database.

* **`GET /v1/events`** returns rows, **newest first**, `limit` capped at 1000 (default 200).
  Newest-first is done in SQL, not by reversing afterwards: with a `LIMIT`, an ascending
  query returns the *oldest* n, which is the opposite of what "the last 50 events" means.
* **`GET /v1/disputes`** takes `node` as well as the contracted `goal` and `status`,
  because a per-node receipt needs it. A dispute with no node belongs to no goal and is
  therefore *excluded* by a `goal` filter rather than silently attached to one.
* **`GET /v1/passport`** streams the zip described above. It regenerates `state.json` and
  `learner.md` first (the views are recomputed by code, never edited) but it does **not**
  rewrite `events.jsonl` — `GET /v1/export` is the command that writes that file; an export
  route that had a file-writing side effect would make two callers fight over it.
* **`POST /v1/sessions/{s}/log`** is `learner log` by value. `api.log_session` now takes
  either a `file` path (the CLI) or `markdown` text (HTTP, MCP, the gateway) and writes the
  same `<date>-<goal>.md` into the vault, appending the same `note` event either way. An
  optional `filename` must be one path segment ending in `.md` — the vault is a directory
  of session logs and a name arriving over HTTP must not be able to escape it.
* It returns both `log` (the name the CLI and MCP use) and `log_path` (the name
  CONTRACTS.md's gateway shape uses) for the same string, rather than renaming one surface.

### Errors

Always `{"error": "..."}`, never a bare string or FastAPI's `{"detail": ...}`:

| Status | When |
|---|---|
| 400 | A rejected operation: a duplicate goal, an out-of-order misconception step, an unknown dispute type, a promotion that fails its rules |
| 404 | A named thing that does not exist: `unknown goal 'x'`, `unknown item 'x'`, `no such file` |
| 409 | An idempotency-key conflict — same key, different body |
| 422 | A malformed body (FastAPI validation), reshaped into the same envelope |

The core raises one exception type (`LearnerError`), so 404-vs-400 is decided by one
regular expression in `learner_svc/deps.py`: `unknown <noun> '<id>'` for the six nouns that
name a stored thing is 404, and everything else — including `unknown dispute type 'x'`,
which names a *qualifier* before the value — is 400. That is a deliberate, localised
heuristic; the alternative is a `NotFound` subclass at ~25 raise sites in modules the CLI
already depends on.

## Idempotency

Every mutating call — CLI (`--idempotency-key`), HTTP (`idempotency_key` in the body, or an
`Idempotency-Key` header) and MCP (`idempotency_key` argument) — takes an optional key.

* **no key** → run it, store nothing
* **key, first time** → run it, store `(key, sha256(canonical body), response)`
* **key, same body** → replay the stored response **verbatim**, run nothing
* **key, different body** → `IdempotencyConflict` → HTTP 409

The body hash is over a canonical JSON rendering (sorted keys), so argument order never
changes the digest. A call that *raised* stores nothing, so a retry after a failure runs
again. The table is `idempotency` in `events.db`.

Why: the reminder job and agent hosts retry. The event table is append-only, so a retried
`record answer` without this would write a second event, and no later fold could tell the
duplicate from a genuine second attempt.

```bash
curl -X POST :5034/v1/events -H 'content-type: application/json' \
  -d '{"kind":"answer","item_id":"i_01…","correct":true,"idempotency_key":"push-42"}'
# same command again -> the same event_id, and still one row in events
```

## `evaluation_method` — the self-graded rule

This rule and the idempotency keys above were adopted after reviewing the tool contract of
an open-source tutoring MCP server (github.com/ArnaudGuiovanna/tutor-mcp), which updates
every BKT/FSRS/IRT estimate on `host_llm` grades — the same model that wrote the question
grading its own learner.

Every evidence event now carries `evaluation_method`:

| Value | Meaning | Counts toward mastery |
|---|---|---|
| `host_llm` | the tutoring model decided the answer was right — **the default** | **no** |
| `blind_solver` | an independent solver, not shown the key | yes |
| `rubric` | scored against a frozen, versioned rubric | yes |
| `human` | a person judged it | yes |

A `host_llm` pass is fully recorded: it is a real event, it schedules the item through
FSRS, it can suspect a misconception. It simply is not an *independent pass*, so it can
never satisfy the "two independent passes" clause of the `known` rule. It is not a failure
either — `learner.md` shows it on its own line:

```
Fragile:  Covectors — 1 independent pass; 1 self-graded (not counted); no delayed retrieval yet; …
Self-graded: Wedge product — 2 passes judged by the tutor itself, none counted
```

`record teach-back` is always recorded as `rubric`: `--rubric-version` is required, and a
frozen versioned rubric is exactly what the `rubric` method names.

`item validate` also accepts `--evaluation-method`. It is **optional and unstated by
default**, which keeps Stage 0 behaviour (the guarantee there is the author ≠ validator
identity check). When it is stated, a `host_llm` validation is recorded but does not
promote the item to `PRACTICE_EVIDENCE`.

Events written before migration 2 carry no method at all. They keep their Stage 0 meaning
and still count: a migration must not retroactively invalidate a history it knows nothing
about. Every event written from Stage 1 on carries an explicit method, because all three
surfaces default it to `host_llm`.

## Schema migration 1 → 2

`learner/schema_v2.sql`, applied automatically the first time any Stage 1 code opens a
Stage 0 database. Forward-only; migration 1 (`schema.sql`) is never edited.

```sql
ALTER TABLE events           ADD COLUMN evaluation_method TEXT;
ALTER TABLE goals            ADD COLUMN assessment        TEXT;
ALTER TABLE goals            ADD COLUMN source_priority   TEXT;
ALTER TABLE item_validations ADD COLUMN evaluation_method TEXT;
CREATE TABLE idempotency (key TEXT PRIMARY KEY, operation TEXT, body_sha256 TEXT,
                          response TEXT, created_at TEXT);
```

No row is rewritten — `events` is append-only and stays that way, enforced by the two
triggers from migration 1. `store.CURRENT_SCHEMA_VERSION` is the version this build
expects; `/healthz` reports the version the database is actually at.

## MCP tools

45 tools: the 23 below, the 16 study tools of 2026-09-24
([study-tools.md](study-tools.md)) and the 6 exam-prep tools of 2026-09-26
(*Exam-prep tools*, below). Named for the CLI verbs with underscores, each returning a JSON
object
(structured output, inferred from the return annotation). Four of them have no single CLI
verb behind them — they are the Stage 2 reads and the log write above, so an agent-host
session (Claude Code or any other MCP client) can show receipts and write a session log
without a second database connection either.

| Tool | CLI verb |
|---|---|
| `learner_goal_add` | `goal add` |
| `learner_graph_import` / `learner_graph_show` / `learner_graph_revise` | `graph …` |
| `learner_item_add` / `learner_item_validate` / `learner_item_promote` | `item …` |
| `learner_session_start` / `learner_session_end` | `session …` |
| `learner_next` | `next` |
| `learner_record_answer` / `learner_record_teach_back` | `record …` |
| `learner_misconception` | `misconception suspect\|confirm-step\|resolve` (one tool, `action` argument) |
| `learner_dispute_open` / `learner_dispute_settle` | `dispute …` |
| `learner_summary` | `summary` |
| `learner_holdout_check` | `holdout-check` |
| `learner_metrics` | `metrics` |
| `learner_export` | `export` |
| `learner_events` | — (`GET /v1/events`) |
| `learner_disputes` | — (`GET /v1/disputes`) |
| `learner_misconceptions` | — (`GET /v1/misconceptions`) |
| `learner_log` | `log` (by value: `markdown`, not `--file`) |

### Exam-prep tools (2026-09-26)

CONTRACTS.md, *Exam blueprint, mixed practice and sealed mock exams*. The rules live in
`learner/blueprint.py` and `learner/exam.py`; these are wrappers like the rest.

| Tool | CLI verb | learner-svc |
|---|---|---|
| `learner_goal_blueprint` | `goal blueprint [--file]` | `GET` / `PUT /v1/goals/{g}/blueprint` — get, or replace when `blueprint` is passed |
| `learner_progress` | `progress` | `GET /v1/progress/{g}` |
| `learner_mock_list` | `mock list` | `GET /v1/mocks/{g}` |
| `learner_mock_start` | `mock start [--n] [--minutes]` | `POST /v1/mocks/{g}` (409 while one is open) |
| `learner_mock_show` | `mock show --session` | `GET /v1/mock/{s}` |
| `learner_mock_submit` | `mock submit --session --file` | `POST /v1/mock/{s}/submit` (409 once submitted) |

Three study tools gained an argument: `learner_practice_next` `focus` (an area code or a
concept; `?focus=`), `learner_practice_answer` `order` (the served `order`, passed back
unchanged; CLI `--order 2,0,1`) and `learner_study_import` `pool` (`practice|mock`; CLI
`--pool`). Their descriptions — and the server's `INSTRUCTIONS` — tell the model the
options are shuffled, to show them with the letters given, never to state a key before an
answer, and that a mock gives no feedback until submit.

Two parity details. `learner_goal_blueprint` sends only `exam`, `source` and `areas`, so a
blueprint read back from the tool (with its totals and shares) can be sent again as is and
both backends validate the same spec. `learner_mock_start` defaults `channel` to
`claude-code` (the service's default is `web`, the browser's); both backends receive it
explicitly.

Two backends, one surface. `LT_LEARNER_URL` unset → the tools open `events.db` in this
process (the agent-host setup, e.g. Claude Code: nothing to run). Set → the same tools proxy to
`learner-svc` (the Stage 2 setup, where one container owns the volume). The arguments and
the returned JSON are identical either way.

A rejected operation comes back as an MCP `ToolError` carrying the core's message, not as a
successful result with an error field in it.

### Register it with Claude Code

`.mcp.json` is not tracked. Copy [`.mcp.json.example`](../../.mcp.json.example) to
`.mcp.json` and set `cwd` to wherever you cloned the repo:

```json
{
  "mcpServers": {
    "learner": {
      "command": "uv",
      "args": ["run", "python", "-m", "learning_tutor.mcp_server"],
      "cwd": "/absolute/path/to/learning-tutor",
      "env": { "LT_DATA_DIR": "./data", "LT_MCP_ENABLED": "1" }
    }
  }
}
```

Swap `LT_DATA_DIR` for `LT_LEARNER_URL: "http://127.0.0.1:5034"` to talk to a running
`learner-svc` instead.

### Other MCP clients

Any MCP client that can launch a stdio server can use the same `command`, `args` and `env`
as `.mcp.json.example`. Both backends are covered by `tests/test_mcp_tools.py`, and the
Claude Code registration has been run for real.

## Tests

| File | Covers |
|---|---|
| `tests/test_learner_stage1.py` | Migration v1→v2 on a real Stage 0 database, the idempotency semantics, the `evaluation_method` rule, the new goal fields — against the core, where they are implemented |
| `tests/test_svc_http.py` | Every route, happy path and one failure each; the error envelope and status codes; the `Idempotency-Key` header; that `summary?format=md` is byte-identical to what the CLI prints; the five Stage 2 additions — newest-first ordering, the 1000-row cap, each filter, the zip's members, that the passport does not rewrite `events.jsonl`, and that a log filename cannot escape the vault |
| `tests/test_mcp_tools.py` | The tool list against the contract (45: 23 + 16 study + 6 exam prep); `learner_summary`, `learner_record_answer` and the four Stage 2 tools in **both** backends (the proxy is pointed at a `TestClient`); the `LT_MCP_ENABLED` gate |
| `tests/test_study_surfaces.py` | The study and exam-prep tools and CLI verbs: every tool in both backends with the same JSON (ids and timestamps normalised; a mock's session id pinned, since it seeds the shuffle), the reads agreeing on one store, refusals as tool errors, answers given by the served letters and `order`, and no key before an attempt or from an open mock |

## Known limitations

* **One writer.** SQLite with WAL and a 5-second busy timeout, and `learner-svc` opens a
  connection per request. That connection is opened with `check_same_thread=False`:
  FastAPI runs a sync dependency's setup and teardown on whichever threadpool worker is
  free, so under concurrent requests a connection can be opened on one thread and closed on
  another, which without that flag is a 500. It is never shared between concurrent
  requests; `tests/test_learner_store.py` pins it. Fine for one UI, one cron and one CLI; it is not a multi-writer
  server, and nothing here changes that.
* **No authentication.** `learner-svc` binds `127.0.0.1` by default and has no auth of any
  kind. It is an internal service behind the gateway, and anything that can reach it can
  rewrite the learner model.
* **The 404/400 split is a regular expression** over the core's error messages. A new
  message worded differently would land in the wrong bucket; the test suite pins the ones
  that exist today.
* **The enum has no `deterministic` value.** CONTRACTS.md pins four names
  (`host_llm|blind_solver|rubric|human`); the tutor-mcp enum it was adapted from also had `deterministic`,
  which is the honest label for grading a multiple-choice answer against its stored key.
  Until that changes, key-graded answers are recorded as `rubric` (graded against a frozen
  artefact).
* **`GET /v1/passport` builds the whole zip in memory** and returns it in one response.
  At personal-tool volumes that is a few hundred kilobytes; there is no streaming, no
  paging and no cap, so a database with years of events in it would be held twice in RAM.
* **`GET /v1/events` has no cursor.** `limit` and `since` are the only knobs, so paging
  backwards through more than 1000 events means moving `since` yourself.
* **`GET /v1/summary/{g}?format=md` returns `text/markdown`, not JSON.** Deliberate — it is
  the same bytes the CLI prints — but a client that assumes every response parses as JSON
  will trip on it.
* **Idempotency keys never expire.** The table grows by one row per keyed mutation, forever.
  At personal-tool volumes that is nothing; there is no sweeper.
* **`grader_version` defaults to `teach-back-v1` on `record answer`**, which CONTRACTS.md
  asks for literally but reads oddly on a multiple-choice item; changing it means changing
  the contract first.
