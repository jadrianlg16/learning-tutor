# misconception.md — the misconception protocol (`teach/v1`)

A misconception is not "got it wrong". It is *believes X, wrongly, and will keep applying X*.
It is a **hypothesis until confirmed**, and one confident wrong tap is not enough — that is how
a learner model fills up with noise it can never clear.

States: `suspected -> active -> weakened -> resolved -> recurred`. Only `active` shows in the
learner view.

## Triggers

Open a suspicion when any of these happens:

- A wrong answer at **confidence 4–5**.
- The **same named distractor** chosen on two different items.
- The learner states a rule in a teach-back or self-explanation that is wrong in a *systematic*
  way (not a slip — a rule they would reapply).

Not a trigger: a wrong answer at low confidence, an arithmetic slip, a misread stem, a misclick.
Those are gaps or noise. If the learner says it was a misclick, that is a dispute, not a
misconception.

## Step 0 — suspect

Name the belief in the learner's terms, as a claim they would agree they hold. Not "does not
understand covectors" — that is a gap. "Thinks a covector is just a vector written sideways" —
that is a belief.

```bash
learner misconception suspect --node "$N" --claim "a covector is a vector written as a row"
```

Say nothing to the learner yet. Do not announce a diagnosis you have not confirmed.

## Step 1 — reasoning

Ask for the reasoning behind the answer. Neutrally. Do not signal that it was wrong.

> "Walk me through how you got to B — what were you thinking?"

- Their reasoning **matches the claim** -> `held`.
- Their reasoning is something else (they misread, they guessed, they meant a different option)
  -> `dropped`. The hypothesis dies here and that is the common case.

```bash
learner misconception confirm-step --node "$N" --claim "..." --step reasoning --outcome held
```

## Step 2 — reworded prediction

Ask them to **predict** the outcome of a *new* situation, worded differently from the original
item, where the wrong belief and the right one give **different** answers. Prediction before
they see the result — that is what makes conceptual change stick.

> "Different setup: <situation>. Before we work it out — what do you expect to happen, and why?"

- They predict what the wrong belief implies -> `held`.
- They predict correctly -> `dropped`. It was a one-off. Say "good — that was my mistake, not
  yours" and move on.

```bash
learner misconception confirm-step --node "$N" --claim "..." --step prediction --outcome held
```

## Step 3 — discriminating counterexample

Now show the case where the belief visibly fails. It must **discriminate**: the wrong model and
the right model must predict different, observable things, and the learner must see their model
produce the wrong one. Then — and only then — supply the correct model and re-derive the case.

> "Here is the case that separates them: <counterexample>. Your model says <A>. What actually
> happens is <B>. Here is why: <the correct model, applied to this case>."

Record the outcome of the *confrontation*:

- The learner accepts the counterexample and can restate the correct model -> `dropped`
  (the belief was dislodged in this attempt).
- The learner rejects the counterexample or reasserts the belief -> `held` (the belief survived;
  it is confirmed and needs a clinic).

```bash
learner misconception confirm-step --node "$N" --claim "..." --step counterexample --outcome held
```

## Confirmation rule

- **All three `held`** -> confirmed, state `active`. It appears in the learner view, and it gets
  attacked first: the next session opens on this node with the counterexample, and future items
  on this node use the belief as a distractor to test whether it has been dislodged.
- **Any `dropped`** -> the hypothesis dies. Do not record it as a belief. Say so plainly if the
  learner is aware of it: "false alarm on my end."

Never call `resolve` in the same session in which you confirmed. Resolution means the belief has
failed to reappear on a later, delayed item.

```bash
learner misconception resolve --node "$N" --claim "..."
```

## Domains that need this most

Elicit-then-confront is the standard move in domains with strong everyday intuitions that are
technically wrong:

- **Physics** — frames, forces, momentum, "heavier falls faster".
- **Probability and statistics** — independence, base rates, p-values, regression to the mean.
- **Programming** — reference vs value, concurrency, "the compiler runs my code top to bottom".
- **Anything with an everyday word used technically** — work, power, significant, random, bias,
  theory, energy.

In these domains, do not wait for a wrong answer: elicit the wrong model *before* teaching, with
a prediction question. That is conceptual change by design rather than by accident.

## What you never do

- Never tell the learner they "have a misconception" during steps 0–2. You are testing a
  hypothesis; naming it early makes them defend it.
- Never record a confirmation you did not run all three steps for.
- Never let a Telegram-style single tap (Stage 1) confirm anything. One out-of-context tap is
  the weakest evidence in the system.
- Never stack hints on top of a confirmed wrong model — fix the model, then return to the item.
