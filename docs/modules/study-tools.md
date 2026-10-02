# Study tools — question bank, flashcards, tables, goal update

Added 2026-09-24. Four tools that need **no model at all**: fix a goal's date and cadence,
import a question bank from markdown, flashcards, and comparison/definition tables. Anything
that needs judgement — writing new cards or tables, blind-solving imported questions — is done
by an agent host (e.g. Claude Code) through the CLI or MCP verbs, never by the gateway
calling an LLM. The browser surface is `/goal/study/?g=<goal>`.

Exam prep on top of these (blueprint, mixed and shuffled practice, sealed mocks, progress):
[exam-prep.md](exam-prep.md).

Binding shapes: [CONTRACTS.md](../../CONTRACTS.md) *Study tools*. The Claude Code procedure:
[`skills/teach/SKILL.md`](../../skills/teach/SKILL.md) *Study tools*.

## Files

| File | What it is |
|---|---|
| `learning_tutor/learner/study_md.py` | The four markdown readers: questions, answer keys, bold-term cards, pipe tables (+ tagged headings → concept titles) |
| `learning_tutor/learner/study.py` | The rules: import, practice, blind check, cards, tables, counts |
| `learning_tutor/learner/schema_v3.sql` | Migration 3: `goals.sessions_per_week`, `items.source_key`, `item_versions.explanation/source`, `study_tables` |
| `learning_tutor/learner/api.py` | `goal_update` and the study-tool functions (idempotency + view refresh) |
| `learning_tutor/learner_svc/routes/study.py` | `/v1` routes |
| `learning_tutor/gateway/routes/study.py` | `/api/goals/{g}/…` routes; the only gateway-side logic is listing importable markdown |
| `learning_tutor/gateway/routes/goals.py` | `PATCH /api/goals/{g}` |

## The rules, and where each is enforced

| Rule | Where |
|---|---|
| No key reaches a client before an attempt: `practice/next` and `cards/next` carry none; practice is graded in learner-svc; a card's back comes only from `cards/reveal` | `study.practice_next`, `study.cards_next`; pinned by `test_practice_serves_no_key_and_grades_server_side` |
| A flashcard flip is the learner rating themselves: event kind `card_review`, `evaluation_method = self_report`, schedules FSRS, never evidence (`card_review` is not an evidence kind and `self_report` is not trusted) | `study.card_review`; `test_cards_flip_in_three_calls_and_never_count` |
| An imported question starts `TEACHING_ONLY` and counts only after a blind check by a solver whose name differs from the author | `study.blind_check` → `items.validate(evaluation_method="blind_solver")` |
| The blind check compares the solver's pick with the key **in code** and never returns the key, so the orchestrating model never needs to read it | `study.blind_check` |
| A question whose latest blind check failed is held back from practice and listed, key included, for a person to judge | `study.bank_review`, `study.practice_next` |
| Holdouts are never served | `_items_of_kind` filters `holdout = 0`; `practice_answer` refuses one |
| Importing the same file twice adds nothing | `items.source_key` (unique) and `study_tables.content_sha256` |
| A card cannot be validated (nothing to solve) | `items.validate` |
| Table fill-in is practice only | the client; nothing is posted |

## Decisions worth knowing

* **Practice schedules unchecked questions too.** `record answer` schedules only
  evidence-writing items; practice calls FSRS itself for unchecked ones, because otherwise a
  question nobody has blind-checked yet would never come back. A schedule is not evidence:
  `counts_toward_mastery` is still decided by the item's status.
* **Delayed retrieval** is decided per item: an answer on a question last answered at least
  `LT_DELAYED_MIN_HOURS` (20) ago is recorded with context `delayed`, which is what the
  `known` rule's "delayed pass" reads. 20 h is a judgement ("yesterday or earlier"), not a
  measurement — **HYPOTHESIS**.
* **Daily caps.** `LT_PRACTICE_NEW_PER_DAY` and `LT_CARDS_NEW_PER_DAY` (20 each) cap how many
  never-seen items are introduced per local day; due items are never capped. "Local" is the
  process's time zone (compose passes `TZ`).
* **Concepts come from the source's own numbering.** A question tagged `[1.2]` is filed under
  the concept whose heading is `## 1.2 …`; a missing one is created with provenance `course`
  and chained by `course_sequence` inside its parent tag. Untagged blocks go to `--node`, or
  to an existing concept whose title is their section heading; otherwise they are skipped and
  reported — a heading like "Glossary" is not a concept and must not become one.
* **Two folder spellings.** `importable` lists markdown under `sources/<goal_id>/` and under
  the underscore spelling (`my_goal` for `my-goal`), because Stage 0 sessions filled the
  latter by hand. Paths outside those folders are refused (400).
* **The goal row is configuration.** `goal update` changes it in place and appends a `note`
  event with the old and new values, so the log still says when the plan's assumptions moved.
  `sessions_per_week` now has a column, so `learner.md` and the gateway divide by the same
  cadence.

## Known limitations

* The markdown readers are literal. A question needs its options on the lines after the stem
  and a key row with the same number (and the same tag, when both have one); anything else is
  reported under `problems`, not guessed. Wrapped option lines are joined.
* Cards need a concept, so a table not filed under one cannot be turned into cards.
* Fill-in results are not recorded — deliberately, but it means table drills leave no trace.
* The browser cannot blind-check (that needs a model). Imported questions stay "unchecked"
  until an agent-host session runs the SKILL.md procedure.

## Tests

`tests/test_study.py` (readers, core rules, learner-svc routes) and
`tests/test_study_gateway.py` (the gateway routes over an in-process learner-svc).
`tests/test_study_surfaces.py` covers the CLI and both MCP backends.
