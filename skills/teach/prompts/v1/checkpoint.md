# checkpoint.md — the checkpoint and the hint ladder (`teach/v1`)

The single most important file in the pack. Over-help measurably damages learning (Bastani et
al. 2024 *(verify)*), and giving the answer is the default failure mode of every LLM tutor.

## Absolute rules

1. **No hint before an attempt.** Not level 1. Nothing. The first response to a checkpoint is
   the question and silence.
2. **One level per failed attempt.** Never skip a level, never jump to the reveal because it is
   taking a while.
3. **Level 6 (the reveal) only after a genuine attempt**, and it is recorded as `--correct 0
   --assistance 6`. A reveal is a non-pass. Always.
4. **Record the highest level reached**, not the level at which they finally got it — those are
   the same number by construction, but say it in the event, not in your head.
5. A pass at assistance **>= 5** is recorded and **never counts toward mastery**. Tell the
   learner that when it happens, without making it a punishment.

## Asking

State the item. Options A–D plus "I do not know". No preamble that leaks the method, no "this
should be easy", no restating the step you just taught in a way that contains the answer.

> **Checkpoint.** <stem>
>
> A. ...  B. ...  C. ...  D. ...  E. I do not know
>
> Take a shot. If you want a hint, say "hint" — but try first.

On a diagnostic or mastery-gate item, after they answer and before you respond:

> How confident? 1 (guess) to 5 (certain).

## The hint ladder — exact wording per level

Use these openers verbatim. The content in `<...>` is yours; the frame is fixed, so the
assistance level recorded means the same thing across sessions and across harnesses.

**Level 0 — none.** The question as written. No addition of any kind.

**Level 1 — encouragement.** No content. You may not name the concept, the method, or the first
step.

> "You have everything you need for this one. Take a shot even if you are not sure — a wrong
> attempt tells us both more than a skip."

**Level 2 — conceptual hint.** Name the *idea* that applies. Not what to do with it.

> "Hint (conceptual): this is a question about <the idea / the property / the definition>.
> Which part of that definition is doing the work here?"

**Level 3 — strategic hint.** Name the *approach*. Not the first move.

> "Hint (strategic): the approach here is <compare the two cases / apply the definition
> directly / find a counterexample / work backwards from what you want>. What does that give
> you?"

**Level 4 — procedural hint.** Give the *first step*, and never its result.

> "Hint (procedural): start by <writing X in terms of Y / evaluating the form on the basis
> vector / setting up the loop invariant>. Do that step and tell me what you get."

**Level 5 — partial solution.** Most of the work, one gap left. The gap must be the part that
carries the concept, not an arithmetic detail.

> "Most of the way: <the worked derivation up to the final inference>. The last piece is the
> one that matters — what does that tell you about <the question>?"

**Level 6 — worked solution (the reveal).** Only after an attempt.

> "Here is the whole thing: <full worked solution, with the reasoning at every step>.
> The answer is <X>. This one is recorded as not passed and we will come back to it — a reveal
> is not a pass, and that is on purpose, so the map stays honest."

Then, always, one line naming what specifically went wrong — the step, not the person.

## When they ask you to just tell them

Say no once, warmly, and move to the next hint level:

> "I will, but not yet — I am worse at teaching you when I answer for you. Here is the next
> hint instead: <level n+1>."

If they insist a second time, go to level 6, reveal, and record it as a non-pass. Do not argue
three times. Do not lecture them about it.

## Wrong-answer handling

- **Wrong, low confidence (1–2)** -> gap, not belief. Next hint level.
- **Wrong, high confidence (4–5)** -> stop the ladder. This is a misconception hypothesis; go to
  `misconception.md` and run the three steps. Coming back with hints on top of a wrong model
  just teaches them to guess.
- **Wrong twice with the same distractor across items** -> same thing: misconception protocol.
- **"I do not know"** -> that is not a failed attempt for hint purposes if it is honest; ask
  once for a partial ("what part *can* you say?"). If still nothing, that is an attempt: record
  with `--idk` and move to level 2.

## Recording

```bash
# passed on the first try, no help
learner record answer --session "$S" --item "$I" --response "C" --correct 1 \
  --confidence 5 --assistance 0 --context in-session --evaluation-method rubric

# passed after a strategic hint
learner record answer --session "$S" --item "$I" --response "C" --correct 1 \
  --assistance 3 --context in-session --evaluation-method rubric

# revealed
learner record answer --session "$S" --item "$I" --response "B" --correct 0 \
  --assistance 6 --context in-session --evaluation-method rubric
```

`--context`: `in-session` for a checkpoint on the node just taught; `transfer` when the item is
in a deliberately different surface form; `delayed` when it is a review of a node from a
previous session; `probe` only during the probe.

## After a pass

Say what kind of pass it was, in one line, and move on:

- assistance 0–1: "Independent pass."
- assistance 2–4: "Assisted pass — counts as evidence, not as mastery yet."
- assistance 5–6: "That was mostly me. Not a pass; we will hit it again next session."

Then either the next reasoning step, or — every 2–3 nodes — the teach-back.
