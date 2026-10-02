# `telegram` — the Stage 1 downtime micro-review push

Implements IDEA.md *Downtime retrieval over Telegram*: optional Telegram reminders
(micro-reviews). Any scheduler (cron, a systemd timer, Windows Task Scheduler) runs
`python -m telegram.job tick` every 30 minutes and `poll` every 5; `tick` decides whether to
push one due retrieval question, `poll` maps a tap back to `learner record answer` (or, for
"not now", to state only). This file is the design, the policy table, and how to run it
(*Running it*, below).

**Hard rule (CONTRACTS.md #6): push retrieval, never content.** The outbound message is the
stem and the options — nothing else. Feedback (1-3 lines: correct/incorrect, the correct
option, the matched misconception if one exists) is built by exactly one function,
`telegram/formatting.py::build_feedback_message`, and every caller reaches it only after a
tap has already been recorded. There is no code path from the outbound message builder to an
answer key — enforced by `tests/test_telegram_format.py`'s
`test_question_message_never_reveals_the_answer`.

## Code map

| File | Owns |
|---|---|
| `telegram/config.py` | Env -> `TelegramSettings`; `load_dotenv_if_present` (see *Env delivery gap* below) |
| `telegram/state.py` | `telegram/state.json` — `PushRecord` / `PushHistory`, atomic load/save |
| `telegram/policy.py` | Pure functions: `should_send`, `pick_next`, `kill_switch` |
| `telegram/cli_bridge.py` | Talks to the learner model: `learner` CLI subprocess, or `learner-svc` HTTP when `LT_LEARNER_URL` is set; the one read-only item-version lookup (see below) |
| `telegram/telegram_api.py` | Bot API client (`httpx`): `sendMessage`, `editMessageText`, `answerCallbackQuery`, `getUpdates`; every function honors `dry_run` |
| `telegram/formatting.py` | Message + keyboard construction; the one function that may mention an answer |
| `telegram/job.py` | `tick()`, `poll()`, `handle_callback()`, the `python -m telegram.job tick\|poll` entrypoint |

Nothing here writes to `learner/events.db` directly — every durable learner fact still goes
through the `learner` CLI (or `learner-svc`), per CONTRACTS.md hard rule 5. The one exception,
and why it isn't actually an exception, is next.

## Why there is a read-only SQLite read

`learner next` (and `GET /v1/next`) deliberately never return an item's answer key — that is
the same hard rule 1 that keeps the `teach` skill from revealing a checkpoint before an
attempt. But grading a Telegram tap, and writing the post-attempt feedback, needs the
correct option and its distractor misconceptions from *somewhere*. There is no
`learner item show` command, and no `GET /v1/items/{id}` route
(`learning_tutor/learner_svc/routes/authoring.py` has `POST /items`, `.../validate`,
`.../promote` — no read).

`cli_bridge.read_item_version` opens `learner/events.db` in SQLite's **read-only URI mode**
(`?mode=ro` — the connection cannot write even if the code had a bug) and reads
`stem, options, answer, distractor_misconceptions, surface_form` for one
`item_version_id`. This is the same read a `learner item show` command would do once one
exists, and does not violate "durable state is written only through tools" because nothing
is written. **Limitation:** this assumes the data directory is on the same filesystem as
this job, which is true for the single-box Stage 1 target in CONTRACTS.md but would need a
real HTTP read once `learner-svc` runs on a different host than the Telegram job.

Once a push is sent, the *positional* answer letter (see next section) is cached on the
`PushRecord` so grading a tap never needs a second read.

## Normalizing the answer key: `resolve_answer_letter`

The keyboard always labels options positionally — A for `options[0]`, B for `options[1]`,
and so on — regardless of whether the option text already carries its own "A. " prefix.
`item_versions.answer` is free text set by whoever authored the item: the canonical example
(`skills/teach/examples/item.example.json`) stores just the letter ("B"), but nothing in the
schema enforces that, and this module's own end-to-end test fixture
(`tests/conftest.py::item_spec`) stores the full option text instead. `resolve_answer_letter`
resolves either convention to the same positional letter once, at `tick()` time, so grading a
tap later is a single string equality check. `option_by_letter` is the inverse, used to build
feedback text and to look up a distractor's misconception (which, again, may be keyed either
way — `build_feedback_message` tries the option text first, then the bare letter).

## Flows

**`tick()`**

1. Load `telegram/state.json`. Expire a pending push older than `LT_TG_PENDING_EXPIRY_HOURS`
   (default 6h) to `expired` and bump the unanswered streak.
2. `policy.should_send(now, history, settings)`. If a 7-day-ignored pause just triggered,
   send one administrative message with a "Resume reviews" button (not a retrieval
   question — this is the one place the job speaks unprompted, and it says nothing about
   the material). Persist state either way and stop here if not eligible.
3. `cli_bridge.get_candidates` — `learner next --mode review --n 5` (or `GET /v1/next` over
   `LT_LEARNER_URL`), already in the priority order docs/modules/learner.md names as shared
   with this push: FSRS-due -> fragile -> misconception check -> transfer variant.
4. `policy.pick_next` — filters out picks with no validated item, or an item still pending
   or inside its `LT_TG_REPEAT_COOLDOWN_MIN` cooldown after a resolution; takes the first
   survivor. `None` -> `{"sent": false, "reason": "nothing due"}` and nothing is sent.
5. `cli_bridge.read_item_version` for the answer key and distractors; build the message
   (`formatting.build_question_message`); `telegram_api.send_message`; append a `pending`
   `PushRecord` (with the resolved positional answer key) to state; save.

**`poll()`**

1. `getUpdates(offset=state.update_offset)`. For each `callback_query`,
   `handle_callback()`.
2. `handle_callback` parses `lt:<kind>:<token>[:<letter>]`:
   - `lt:resume:_` — clear `paused`/`paused_reason`, reset the streak. Never touches a push.
   - unknown/foreign data, or a token that is missing or already resolved — answered with
     "This question has expired.", nothing recorded.
   - `lt:notnow:<token>` — mark the push `not_now`, reset the unanswered streak (a decline is
     still a response), edit the message to a plain "Skipped" line. **Never calls
     `learner record answer`** — this is the literal reading of IDEA.md's "not now is data,
     not a wrong answer": it is not an answer at all, so nothing is recorded as one. See
     *What "not now" actually writes* below for what durable trace it does leave.
   - `lt:idk:<token>` / `lt:opt:<token>:<letter>` — resolve correctness (idk is always
     incorrect; an option is correct iff its letter equals the push's stored answer key),
     call `cli_bridge.record_answer(..., channel="telegram", context=push.context,
     evaluation_method="human", idempotency_key=f"telegram-{token}")`, then edit the message
     with `formatting.build_feedback_message` and reset the streak.
3. Persist the new `update_offset` and state.

## What "not now" actually writes

CONTRACTS.md's event `kind` enum includes `note`, and the schema comment
(`learning_tutor/learner/schema.sql`) suggests it as the general-purpose free event. But the
only place the CLI or `learner-svc` currently *writes* a `note` event is
`api.log_session` (`learner log`, copying a session markdown file into the vault) — there is
no general-purpose `learner note add` (or `POST /v1/notes`) to hand a "not now" to. So a
"not now" is **kept in `telegram/state.json` only**
(`PushRecord.status = "not_now"`) and never reaches `events.db`. That is a real gap, not a
design choice: the durable event log has no record that a push was declined, only
`telegram/state.json`'s rolling window (capped at `MAX_PUSHES_KEPT = 200`, generous for the
14-day kill-switch window at a 3/day budget). If `learner note add` (or an HTTP equivalent)
is ever added, `handle_callback`'s `notnow` branch is the only place that needs to start
calling it.

## The policy, as implemented

| Rule (IDEA.md default) | Where | Note |
|---|---|---|
| Budget 3/day | `should_send` / `_effective_budget` | Counted by calendar day of `now`, not a rolling 24h |
| Window 09:00-21:00 | `should_send` / `_in_window` | Host-local time, or `LT_TG_TZ`; the scheduler runs `tick` every 30 min around the clock on purpose — see *Running it* |
| >=90 min apart | `should_send` (`min_gap_min`) | Measured from the last **send**, regardless of outcome |
| Halve on a live-session day | `_effective_budget` | `math.ceil(budget/2)` — **HYPOTHESIS**: IDEA.md says "halve" but not which way to round an odd budget; rounding up (fewer, not zero, pushes) was chosen over floor. Also HYPOTHESIS: nothing currently *writes* to `live_session_dates` — see *Troubleshooting* |
| "Not now" is data, not a wrong answer | `handle_callback` | Never calls `record answer`; resets the unanswered streak (a decline is a response) — see limitation above |
| 3 unanswered -> 1/day | `_effective_budget` (`backoff_after_unanswered`) | "Unanswered" = the push expired with **no tap of any kind**; a "not now" tap resets the streak to 0, it does not count against it — a judgement call: a decline proves the channel is alive, silence does not |
| 7 days ignored -> pause and ask | `_pause_reason` | Clock is "days since the last tap of any kind" (including not_now), not since the last send; `tick()` sends one "Resume reviews" message on the transition |
| Adaptive best-3-hours after 2 weeks | `_best_hours` | **HYPOTHESIS** (IDEA.md marks the whole reward/timing model HYPOTHESIS). Requires >= `LT_TG_ADAPT_AFTER_DAYS` distinct calendar days of resolved pushes *and* >= 3 hour-buckets with >= 3 samples each, else it does not restrict at all — an unproven signal should fail open, not narrow the window on thin data |
| Kill: response rate < 30% over 14 days | `kill_switch` | A response = any tap (option, IDK, *or* not now) — all three prove the channel is alive; only silence counts against it. A push still `pending` and younger than 6h is excluded from the denominator (too soon to call it ignored). `False` when the window has zero resolved pushes (no evidence either way, not "safe") |
| Nothing due -> nothing sent | `pick_next` returning `None` | No fallback question is ever generated |

Everything in the table not named by IDEA.md's own defaults (the repeat/not-now cooldown,
the pending-expiry window, the pending-grace period for the kill switch, the halving
rounding) is a named field on `TelegramSettings` with its default set here, in this file —
the same "transparent knob, not a hidden constant" treatment docs/modules/learner.md gives
the evidence-rule thresholds.

## Running it

The job is two commands, run from the repo root by whatever scheduler you already use:

```bash
uv run python -m telegram.job tick   # every 30 minutes, around the clock
uv run python -m telegram.job poll   # every 5 minutes
```

- **`tick`** does **not** need a schedule restricted to 09:00-21:00: `policy.should_send`
  already enforces `LT_TG_WINDOW` (default 09:00-21:00), so a tick outside the window is a
  fast, silent no-op. Keeping the window in one place (the policy, which is unit-tested)
  instead of two (also the schedule) is the reason to over-schedule and let the job gate
  itself.
- **`poll`** long-polls `getUpdates` for taps and edits the original message with 1-3 lines
  of feedback once one arrives.

Neither command calls a model: `telegram/job.py` already decides everything (whether to send,
what to send, how to grade a tap). Each prints its result as JSON on stdout and exits 0; a
learner-CLI or Telegram error prints `{"error": ...}` on stderr and exits 1.

Example crontab (`crontab -e`), with a placeholder path:

```cron
*/30 * * * * cd /path/to/learning-tutor && uv run python -m telegram.job tick > /dev/null
*/5  * * * * cd /path/to/learning-tutor && uv run python -m telegram.job poll > /dev/null
```

Discarding stdout keeps a successful run silent while stderr (an error) still reaches cron's
mail. Use the absolute path to `uv` if it is not on cron's `PATH`. A systemd timer or a
Windows Task Scheduler entry works the same way: run the same two commands from the repo root
on the same two intervals.

**Environment.** The job needs `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and any `LT_TG_*`
overrides. Either set them in the scheduler's own environment, or put them in
`$LT_REPO_DIR/.env` (or `./.env` in the directory the job runs from): `job.py` loads that file
itself to fill gaps, and real environment variables still win over it — see
*Env delivery gap* below for why.

**Check it before scheduling it.** From a shell with the same environment the scheduler will
use:

```bash
cd /path/to/learning-tutor
LT_TG_DRY_RUN=1 uv run python -m telegram.job tick   # prints JSON; no network call
```

`LT_TG_DRY_RUN=1` prints the exact Telegram payload instead of sending it, so this is safe to
run without a live bot token. (`poll` is a no-op under dry run: there is no bot to long-poll
without a token.)

A live run with a real bot token and something due sends a message with an inline keyboard
(the options, "I don't know", "Not now") to `TELEGRAM_CHAT_ID`. Tap one; within 5 minutes
`poll` should edit that message with 1-3 lines of feedback.

### Troubleshooting

| Symptom | Where to look |
|---|---|
| Nothing ever arrives | `LT_TG_DRY_RUN=1 uv run python -m telegram.job tick` from the repo — the JSON `reason` says why (`outside window`, `budget exhausted`, `nothing due`, `paused: ...`). "nothing due" is correct and silent by design (IDEA.md: silence is a feature) until sessions have produced review items. |
| Taps don't register | Check `data/telegram/state.json` for the push's `token`; run `uv run python -m telegram.job poll` by hand and watch for a `TelegramError` on stderr (bad token, or `getUpdates` conflicting with another poller using the same bot). |
| Pushes stopped, marked paused | `telegram/state.json`'s `paused_reason` — 7 days with no tap of any kind (including "Not now") auto-pauses and the next `tick` sends one admin message with a "Resume reviews" button. Tap it, or clear `paused`/`paused_reason` in the state file by hand. |
| Suspiciously few pushes on a study day | `should_send` halves the daily budget on any date listed in `telegram/state.json`'s `live_session_dates`. Nothing currently writes to this list automatically (see *Known limitations*); add the date by hand, or wire `teach`'s `session start` to append it. |
| Considering killing the feature | `uv run python -c "from telegram import state, policy; print(policy.kill_switch(state.load('data')))"` — `True` means the response rate has been under 30% over the trailing 14 days (IDEA.md's stated kill criterion: cut the cadence, then cut the feature). |

### Env vars (all optional; every policy default is IDEA.md's)

| Variable | Default | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | Required for a real send; omit under `LT_TG_DRY_RUN=1` |
| `TELEGRAM_CHAT_ID` | — | Required for a real send |
| `LT_TG_DRY_RUN` | `0` | `1` prints the Telegram payload instead of sending it |
| `LT_TG_BUDGET` | `3` | Pushes per day |
| `LT_TG_WINDOW` | `09:00-21:00` | `HH:MM-HH:MM`, host-local time (or `LT_TG_TZ`) |
| `LT_TG_MIN_GAP_MIN` | `90` | Minimum minutes between pushes |
| `LT_TG_BACKOFF_AFTER_UNANSWERED` | `3` | Consecutive ignored pushes before back-off |
| `LT_TG_BACKOFF_BUDGET` | `1` | Budget once backed off |
| `LT_TG_PAUSE_AFTER_DAYS` | `7` | Days with no tap of any kind before auto-pause |
| `LT_TG_KILL_RATE` / `LT_TG_KILL_WINDOW_DAYS` | `0.30` / `14` | The kill-switch threshold and window |
| `LT_TG_ADAPT_AFTER_DAYS` | `14` | Days of history before the best-3-hours window kicks in (HYPOTHESIS — see the policy table) |
| `LT_TG_REPEAT_COOLDOWN_MIN` | `90` | Minutes before a resolved item may be pushed again (a judgement call, not an IDEA.md default) |
| `LT_TG_PENDING_EXPIRY_HOURS` | `6` | Hours before an unanswered push expires (a judgement call, not an IDEA.md default) |
| `LT_TG_GOAL` | unset | Restrict candidates to one goal id; unset uses the CLI's single-goal default |
| `LT_LEARNER_URL` | unset | `http://host:5034` to use `learner-svc` HTTP instead of shelling out to the CLI |
| `LT_TG_TZ` | host local | IANA zone (e.g. `America/Mexico_City`) if the window shouldn't use the host's local time |
| `LT_DATA_DIR` | `./data` | Where `telegram/state.json` lives — the same variable the `learner` CLI reads |
| `LT_REPO_DIR` | unset | Repo root; `$LT_REPO_DIR/.env` is the file the job loads to fill gaps (else `./.env`) |

### Hard rules this job must never break

1. Never send content unsolicited — only a question, and only feedback *after* an attempt.
2. Nothing due -> nothing sent. Never invent a question to fill the budget.
3. "Not now" is data, not a wrong answer, and never touches `learner record answer`.
4. A tap is graded by exact comparison against the stored answer key and recorded
   `--evaluation-method human` — never `host_llm`, because nothing here is an LLM judging
   itself. (`cli_bridge._supports_evaluation_method` feature-detects the flag; see
   *Known limitations*.)

## Env delivery gap (why `load_dotenv_if_present` exists)

Schedulers usually run a job with a **minimal environment**: cron does not read your shell
profile, and neither does a systemd timer or a Task Scheduler entry unless told to. A token
exported only in an interactive shell never reaches the ticker. `telegram/job.py::main` loads
`$LT_REPO_DIR/.env` (or `./.env`) as a gap-filler — `os.environ.setdefault`, so a real
environment variable always wins — before running `tick`/`poll`. This is a hand-rolled
6-line parser (`config.py::load_dotenv_if_present`: one `KEY=VALUE` per line, `#` comments
and blank lines skipped, surrounding quotes stripped, no escaping or multiline values), not
`python-dotenv`, to avoid
adding a dependency for one small file read.

## State file (`telegram/state.json`, under `LT_DATA_DIR`)

```jsonc
{
  "pushes": [
    {
      "token": "1460f6cc7f61", "item_id": "i_...", "item_version_id": "iv_...",
      "node_id": "n_...", "node_title": "Covectors", "session_id": null,
      "sent_at": "2026-09-05T09:37:59Z", "stem": "...",
      "answer_key": "B",              // positional letter, resolved once at send time
      "options": ["A. ...", "B. ..."],
      "context": "delayed",           // or "transfer" when the item version has a surface_form
      "status": "pending",            // pending | answered | not_now | expired
      "message_id": 123, "resolved_at": null, "correct": null, "idk": false
    }
  ],
  "paused": false, "paused_reason": null,
  "unanswered_streak": 0,
  "live_session_dates": [],           // see the halving row above
  "update_offset": null               // getUpdates cursor
}
```

Safe to delete: the worst case is one re-asked question and a reset streak, never lost
learner evidence (that lives in `events.db`, written only through the CLI/`learner-svc`).
Capped at 200 pushes (`state.MAX_PUSHES_KEPT`), generous for the 14-day kill-switch window
at a 3/day budget.

## Muting risk — what mitigates it, concretely

IDEA.md's own devil's-advocate: "notification fatigue is the whole risk, and a muted bot is a
dead channel." Mitigations actually implemented, each pointing at its test:

- **Silence by default.** `pick_next` returns `None` whenever nothing is due; `tick()` never
  fabricates a question to fill the budget (`tests/test_telegram_policy.py::test_pick_next_returns_none_when_nothing_due`).
- **A hard, small daily ceiling**, tightened further by both live-session halving and the
  unanswered back-off, so a bad week degrades toward silence rather than piling on
  (`test_should_send_live_session_halves_budget`, `test_should_send_backoff_after_unanswered_streak`).
- **"Not now" costs nothing** — it is not graded, does not touch mastery, and immediately
  resets the streak so a busy afternoon does not compound into a lockout
  (`test_not_now_never_calls_record_answer`, `test_not_now_still_resets_the_unanswered_streak`).
- **Self-pausing.** 7 days of silence stops the pushes on its own and asks before resuming,
  rather than the feature quietly wearing out its welcome
  (`test_should_send_pauses_after_days_ignored`).
- **The kill switch is a real, checkable number**, not a vibe: `policy.kill_switch` (unit
  tested at both sides of the 30% line) is one line to run (see *Troubleshooting*).
  IDEA.md's own stated remedy if these aren't enough is not "add more mitigations" — it is
  "the feature dies rather than the channel," i.e. cut this job before it trains the learner
  to mute the bot.

## Known limitations

- **No `learner note add`.** "Not now" lives only in `telegram/state.json` — see above.
- **`live_session_dates` is read but nothing writes it yet.** The halving rule is
  implemented and tested against a manually-populated list; wiring `teach`'s
  `session start` to append today's date is Stage 1 follow-up, not done here.
- **The read-only item-version lookup assumes co-located storage.** Fine for the Stage 1
  single-box target; would need a real read endpoint for a networked `learner-svc`.
- **The adaptive best-hours window, the halving rounding, and every cooldown/expiry
  constant not named in IDEA.md are judgement calls**, listed as HYPOTHESIS in the policy
  table above — exactly like docs/modules/learner.md's evidence thresholds, they are meant
  to be tuned against real data, not treated as settled.
- **`--evaluation-method` on `record answer` was added to this repo's CLI while this module
  was being built.** `cli_bridge._supports_evaluation_method()` feature-detects it via
  `--help` rather than assuming a version, so this job works whether or not the flag is
  present — but if it is ever *removed* from a pinned older CLI, Telegram-graded taps
  silently fall back to `host_llm` (self-graded, never counted toward mastery) instead of
  failing loudly. Worth a warning log if that regresses; none is logged today.
