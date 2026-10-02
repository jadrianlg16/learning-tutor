# teach-back-rubric.md — rubric version `teach-back-v1` (`teach/v1`)

Teach-back is the strongest retrieval form available at Stage 0 and it costs one message.
Run it after every 2–3 nodes, and always before `session end`.

**Never change the anchors without cutting a new rubric version** (`teach-back-v2`). Every
event records `--rubric-version`, so scores stay comparable only if the anchors are frozen.

## The prompt

> Explain **<node>** to me as if I have never seen it, in your own words — no notation you have
> not defined — and give me one example I have **not** shown you.

Two constraints, both load-bearing: *own words* blocks recitation, *an example I have not shown
you* is a miniature transfer test.

If they stall completely, offer one scaffold and record `--assistance 2`:

> "Start with: what problem does it solve, and what would go wrong without it?"

Do not scaffold twice. A second stall is a score of 0 or 1 and a reteach.

## Scoring — 0 to 3

Score the **explanation**, not the personality, not the fluency, not the length.

### 3 — Correct and generative

- The mechanism is right: *why* it works, not only *what* it is.
- Uses their own words; any notation they introduce, they define.
- The new example is genuinely new (not one you used, not a trivial relabel of one you used)
  and it is correct.
- Names at least one boundary: a condition, an assumption, or a case where it does not apply.

### 2 — Correct but bounded

- The mechanism is right, or right with one small slip they catch or that does not change the
  conclusion.
- The example is correct but is one of yours, or a relabel of one of yours.
- No boundary named, or a boundary named vaguely ("it does not always work").

### 1 — Partial / recited

- Recognizable but leaning on remembered phrasing; the *why* is missing or circular
  ("it works because that is the definition").
- The example is missing, wrong, or does not actually exercise the concept.
- Or: correct on a special case only, presented as the general case.

### 0 — Absent or wrong

- Cannot produce an explanation, or the mechanism stated is wrong.
- Or it describes a different concept.
- "I do not know" scores 0 and is recorded honestly — it is a clean signal, not a punishment.

## Consequences

| Score | Action |
|---|---|
| 3 | Node stands. Mark it as a teach-back pass, move to the next node |
| 2 | Node stands. Note the missing boundary; one transfer item on this node next session |
| 1 | **Reteach now** with a *different* strategy (see `teach-step.md`), then a fresh checkpoint |
| 0 | **Back up.** Teach the prerequisite node, then this one again. Do not push forward |

A score of 3 is a strong signal but it is **not** a mastery pass on its own — mastery is a
passed check on a `PRACTICE_EVIDENCE`+ item. Teach-back is evidence about the *explanation*
component; keep them separate.

## Recording

```bash
learner record teach-back --session "$S" --node "$N" --score 2 \
  --rubric-version teach-back-v1 --assistance 0 \
  --notes "mechanism correct; example was the one I used; no boundary named"
```

`--assistance`: `0` unscaffolded, `2` after the one permitted scaffold. Higher levels do not
apply to teach-back — if you had to supply the content, the score is 0 or 1, not a higher
assistance level.

## Feedback to the learner

Two sentences, maximum. What was right, and the one thing missing.

> "The mechanism is right — you have why the wedge product has to be antisymmetric. The example
> was mine though; next time bring one of your own, that is the part that tells us it transfers."

Never read the rubric out loud, never give a percentage, never compare to a previous session
unless the learner asks.
