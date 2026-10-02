# Exam prep — blueprint, mixed and shuffled practice, sealed mocks, progress

Added 2026-09-26, on top of the [study tools](study-tools.md). Still **no model anywhere**:
everything here is counting, scheduling and grading against frozen keys. Binding shapes:
[CONTRACTS.md](../../CONTRACTS.md) *Exam blueprint, mixed practice and sealed mock exams*.
The Claude Code procedure: [`skills/teach/SKILL.md`](../../skills/teach/SKILL.md) *Study tools*.

Why it exists: a question bank used for exam practice has predictable failure modes. Served
area by area in import order, it never interleaves. With options never shuffled, the answer
keys tend to cluster on some letters, so position becomes a cue. When the bank's share of an
area does not match the exam's weight, practice time goes to the wrong places. And without a
sealed set, there is no honest way to rehearse the exam itself. This module addresses each.

## Files

| File | What it is |
|---|---|
| `learning_tutor/learner/schema_v4.sql` | Migration 4: `blueprints`, `items.pool` |
| `learning_tutor/learner/blueprint.py` | Set/get a goal's blueprint; `index()` maps live concepts to their row |
| `learning_tutor/learner/study.py` | `interleave`, `concept_weights`, `shown_order`/`shown_choice`, `focus`, the `sealed` filter |
| `learning_tutor/learner/exam.py` | `progress`, `mock_start/show/submit/list` |
| `learning_tutor/learner/graph.py` | `merge`/`split` now move `study_tables` with their concept |
| `learning_tutor/learner_svc/routes/study.py`, `gateway/routes/study.py` | the routes |

## The rules, and where each is enforced

| Rule | Where / pinned by |
|---|---|
| New questions interleave by blueprint share from the first pick (largest deficit: the concept furthest below `share × (seen + 1)` goes next); equal shares without a blueprint | `study.interleave`; `test_new_questions_follow_the_blueprint_weights_from_the_first_picks` |
| `focus` narrows practice to an area code or a concept, for a first pass through new material | `study._focus`; `test_focus_narrows_practice_to_an_area_or_a_concept` |
| Options are shown in a per-question, per-local-day shuffle; letters are positions in *that* order; the answer carries `order` back and the event records `payload.shown_order` | `study.shown_order`, `practice_answer`; `test_options_are_shuffled_and_graded_by_the_order_shown` |
| A sealed question (pool `mock`, never answered) is invisible to practice, cards and the Anki export; blind checks and `bank review` still reach it | `study._SEALED_SQL`; `test_sealed_questions_stay_out_of_practice_cards_and_exports` |
| A sealed import is questions only (cards or tables made from it would leak it) | `study.import_markdown`; `test_a_sealed_import_is_questions_only` |
| The browser importer neither lists nor reads a folder holding a `SEALED` file | `gateway/routes/study._sealed`; `test_the_browser_importer_cannot_see_or_read_a_sealed_folder` |
| A mock uses only **checked** sealed questions, weighted by the blueprint; one open mock at a time (409) | `exam.mock_start`; `test_a_mock_round_trip` |
| Nothing that gives a key away while a mock is open; everything is graded once, on submit; a blank is `idk`; a second submit is 409; a bad answer writes nothing | `exam.mock_show`, `exam.mock_submit`; `test_a_mock_rejects_foreign_items_and_bad_answers_without_writing` |
| After a mock its questions join practice rotation (scheduled, never "new" again) | the `sealed` filter keys on "answered" |
| Progress counts first tries, never migrated copies of an answer (a merge copies evidence onto the new node) | `exam._NOT_MIGRATED`; `test_progress_counts_first_tries_by_area` |
| A blueprint row follows its concept through a merge or split (rows name a `ref`, resolved through aliases) | `blueprint.index`; `test_the_blueprint_and_tables_follow_a_merged_concept` |

## Decisions worth knowing

* **Counts, not a mastery estimate.** Progress is first-try accuracy with a 95 % Wilson range
  by area, drawn against generic reference lines (80 % target, 70 % floor by default).
  Node *state* still comes only from the evidence rules. The weighted Disciplinar headline
  stays `null` until every area has 5 first tries; even then it is **not** an ICNE: Ceneval
  publishes neither the cut scores nor the mapping from % correct (guide, scoring section).
* **Why shuffle per day, not per serve.** The server can recompute a day's order, so a
  client that forgets to send `order` back is still graded right on the same day. Across
  days the order changes, which is what breaks the A/B/C position bias.
* **Mock timing.** `LT_MOCK_MINUTES_PER_ITEM` = 2.9: the ISOFT Disciplinar timetable is
  8:30–13:00 and 14:30–17:00, 420 min for 143 items. The timer is advisory — a late submit is
  graded and flagged `overtime` (one minute of grace for an auto-submit at the bell).
* **Mock answers are ordinary evidence.** `answer` / `rubric` / `bank-key-v1`, prompt
  `mock/v1`, `payload.mock = session_id`, context `in-session` (a first sight is not a
  delayed retrieval). A checked question answered right in a mock is a real independent pass.
* **Sealed ≠ holdout.** Holdouts keep their own door (`holdout-check`); the mock pool is a
  separate, additive flag so neither changes meaning.

## Setting up an exam goal

1. Create the goal with its deadline and cadence, then store the exam's blueprint (the
   official item count per area and concept) with `learner goal blueprint` or the matching MCP
   tool. Concepts the blueprint does not list can be retired with `graph revise`; deciding
   which borderline concepts belong is a judgement (**HYPOTHESIS**, reversible).
2. Import the practice bank and its key from markdown (`learner study import`), then
   blind-check it so its questions can count as evidence.
3. Import a separate sealed set with `--pool mock` from the CLI or MCP. A sources folder that
   contains a file named `SEALED` is hidden from the browser importer (reading from it returns
   400), so mock questions cannot leak into practice by accident.

Real question banks and study material stay in the gitignored `data/` folder; the mock UI's
fixtures are original questions (see [web-ui.md](web-ui.md)).
