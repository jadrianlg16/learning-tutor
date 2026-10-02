# `sim-learner` — the scripted-learner harness for the automatable gates

`scripts/sim_learner.py` drives the learner core with a **truth-table learner** and prints
the three Stage 0/1 numbers as JSON. No model, no gateway, no LLM: every answer is decided
by a rule over `(node, is_holdout)`, so what the run measures is the learner model — the
probe, the evidence rules, promotion, holdouts and `metrics.py` — not a tutor.

It exists for two of the gates in [`IDEA.md`](../../IDEA.md) *Staging* that a machine can
check: "probe measurably shorter on session 2" and "false-mastery rate on holdouts
acceptable", plus the item-rejection rate. It does **not** replace the Stage 0 gate ("actually
learn something real"): a scripted learner cannot learn, it can only show the machinery
counts what it claims to count.

```
scripts/sim_learner.py       the harness (Config, LearnerRules, Harness, run, main)
tests/test_sim_learner.py    the three assertions below, plus the CLI smoke
```

## Run

```
uv run python scripts/sim_learner.py --data-dir /tmp/sim --forgets N2 --never-knows N4
uv run python scripts/sim_learner.py --data-dir /tmp/sim-perfect
uv run python scripts/sim_learner.py --data-dir /tmp/sim-empty --setup-only
uv run pytest -q tests/test_sim_learner.py
```

`--data-dir` (or `LT_SIM_DATA_DIR`) must point at a fresh directory: a run creates one goal
(`g_sim`) and refuses to reuse it. Everything else reads the normal `LT_*` environment,
except `LT_HOLDOUT_DELAY_DAYS`, which the harness pins to `0` so the holdouts are due in
the same run.

| Flag | Default | Meaning |
|---|---|---|
| `--nodes` | 4 | chain `N1 -> N2 -> ... -> Nk` of `strict_prerequisite` edges |
| `--items-per-node` | 2 | validated practice items per node, plus one designated holdout item |
| `--rejected-items` | 1 | items failed at validation, so the rejection rate has a denominator |
| `--practice-rounds` | 2 | in-session answer rounds in session 1 (this is what gets items to `LT_PROMOTE_MIN_USES`) |
| `--gap-days` | 30 | days between session 1 and session 2, so the FSRS cards are due for review |
| `--forgets N` | — | correct on everything `next` serves, wrong on N's hidden holdout: the crammer |
| `--never-knows N` | — | wrong on everything about N |
| `--setup-only` | off | goal + graph only: the empty-denominator case |

## What one run does

All calls go through `learning_tutor.learner.api` — the same functions behind the `learner`
CLI, `learner-svc` and the MCP server — so the harness cannot exercise a path a real caller
does not have.

1. `goal add`, `graph import` of the chain.
2. Per node: `items-per-node + 1` items authored by `author-model`, validated `pass` by
   `solver-model` (`evaluation_method=blind_solver`). One of them is the *designated*
   holdout. `--rejected-items` more are validated `fail`.
3. **Session 1** (`gap_days` before now): `next --mode probe` until a `probe.md` stop rule
   fires — no pick, budget spent, or three consecutive wrong answers — then
   `practice-rounds` rounds of `record answer --context in-session` on every validated item.
4. **Session 2** (an hour before now): probe again, then `next --mode review` until it
   returns nothing new. Review answers carry the pick's own context (`delayed`), which is the
   evidence a node needs to reach `known`.
5. After **every** answer, every `PRACTICE_EVIDENCE` item with `LT_PROMOTE_MIN_USES` uses is
   promoted. Holdout membership is a hash of a random item id, so the harness promotes the
   designated item with `holdout_fraction=1.0` and every other with `0.0` (a
   `dataclasses.replace` on the store's settings around the one call): exactly one holdout
   per node, without touching the database directly.
6. `holdouts.due(store, goal, now=clock)` with the harness clock; every due holdout is
   answered with the context it was served as (`delayed`, since no item has a
   `surface_form`).
7. `api.metrics` plus the node states become the report.

All answers are `assistance_level=0`, `evaluation_method=blind_solver` — independent passes.
Every event is written with an explicit `ts` from the harness clock, and FSRS is reviewed at
that same `ts` (`record_answer` passes it through), so session 1 really is 30 days old when
session 2 asks what is due.

## The report

```json
{
  "learner": "forgets N2; never knows N4",
  "probe_questions": {"session_1": 6, "session_2": 3, "shorter_on_session_2": true, "log": {...}},
  "false_mastery": {"nodes_with_holdout_checks": 3, "nodes_known_then_failed": 1, "nodes": ["n_n2_0480"], "rate_percent": 33},
  "item_rejection": {"validations": 13, "rejected": 1, "rate_percent": 8},
  "holdout_success_7d": {...},
  "holdout_checks": 3,
  "node_states": {"N1": "known", "N2": "fragile", "N3": "known", "N4": "unknown"}
}
```

`false_mastery`, `item_rejection` and `holdout_success_7d` are `metrics.py`'s own dicts,
untouched. A rate over an empty denominator is `null`, never `0` — the harness does not
convert. `probe_questions.session_1/2` are `null` under `--setup-only` for the same reason.

## What the tests pin

| Test | Asserts |
|---|---|
| forgetting learner (`--forgets N2 --never-knows N4`) | `false_mastery.rate_percent > 0` with `nodes_with_holdout_checks > 0`; N2 ends `fragile` (the failed holdout downgraded it); session-2 probe shorter than session-1 |
| perfect learner | `nodes_known_then_failed == 0`, `rate_percent == 0`, every node `known`, session-2 probe is `0` |
| `--setup-only` | all three rates are `None`, not `0` |
| CLI | prints JSON, `holdout_delay_days` is pinned to `0` |

## Known limitations

- **It is a truth table, not a learner.** It cannot show the pedagogy works; it shows the
  numbers move the way the definitions say they should when a learner behaves in a known
  way. The Stage 0 gate still needs a person.
- **A chain graph only.** Split scores on a chain make the probe order deterministic
  (`N2, N3, N1, N4` on four nodes); the KST split rule is not stressed by a DAG with
  branches.
- **`holdout_success_7d.window_days` reads `0`** in the report because `metrics.py` takes the
  window from `LT_HOLDOUT_DELAY_DAYS`. With the delay pinned to `0` the window is "since
  now", and only the holdout answers the harness wrote after its `now` fall inside it. The
  count is right for this run; the field name is not what it says. This is `metrics.py`'s
  choice, not the harness's, and it is not changed here.
- **Two probes on the same node in one session are real behaviour**, not a harness artefact:
  a wrong probe answer leaves the node `unknown`, so `next --mode probe` picks it again with
  its least-recently-served item. `probe.md` stop rule 3 is what ends it.
- **The gateway is out of scope** by design; nothing here proves the phase machine drives
  these same calls.
