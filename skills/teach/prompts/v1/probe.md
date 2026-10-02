# probe.md — the probe phase (`teach/v1`)

Goal of the phase: produce a map of what the learner can actually do right now, cheaply, and
find the edge — the lowest unknown node whose prerequisites are known. Not a grade. Not a
diagnosis of the person.

## Frame it to the learner, once

> Before I teach anything I want to find where you actually are, so I do not waste your time on
> things you have. About 10 minutes of multiple choice. "I do not know" is always an option and
> is genuinely the most useful answer when it is true — it is data, not a failure.

## Item shape

Every probe item is a **graded multiple-choice** item:

- **Stem**: one concrete question. No "which of the following is NOT". No double negatives.
- **4 content options plus "I do not know"**, always present, always last.
- **Every wrong option maps to a named misconception** in `distractor_misconceptions`. If you
  cannot name what a distractor's chooser believes, that option is filler — rewrite it. This is
  how physics concept inventories work: a wrong answer says *what* the learner believes, not
  just that they missed.
- **`kind`**: `recall | concept | apply | analyze | transfer`. Match the goal's Bloom depth.
- **`components`**: the knowledge components the item actually needs (evidence aggregates here,
  not at the node).
- **Confidence 1–5** is asked on diagnostic and mastery-gate items only — not on every small
  question. Ask it *after* the answer is locked, never before.

Batch-author 4–6 items, blind-solve them in one pass, validate, then serve. Authoring one at a
time in the loop is slower and produces worse distractors.

## Choosing the next question

`learner next --mode probe` picks the node. Your job is only to write a good item on it. Do not
override the CLI's choice; if you think the choice is wrong, say so to the learner and open a
dispute rather than quietly asking something else.

Coverage order, when the CLI leaves you a choice:

1. Nodes on the shortest prerequisite path to the goal, deepest-uncertainty first.
2. Nodes with a `misconception_for` edge — these are where confident-wrong is likeliest.
3. Nodes the course sequence puts early but the learner claims to have.

## Budget and stop rules

Budget **12 questions** (default; see SKILL.md for the defense). Stop when any fires:

1. `next --mode probe` returns no node or `{"done": true}`.
2. 12 questions asked.
3. Three consecutive "I do not know", **or** three consecutive wrong answers at the frontier.
   Continuing past this measures frustration, not knowledge.
4. The learner asks to stop.

Do not extend the budget to "get a cleaner picture". An unresolved node is a node teaching will
resolve in one checkpoint anyway.

## Reading an answer

| Answer | Confidence | Reading | Action |
|---|---|---|---|
| Correct | 4–5 | probably known | record; move on |
| Correct | 1–2 | fragile — right for the wrong reason, or a guess | record; ask one "why that one?" follow-up, do not grade it |
| Wrong | 4–5 | **misconception hypothesis** | record; run the misconception protocol (`misconception.md`) — later, not mid-probe unless the node is next to teach |
| Wrong | 1–2 | unknown | record; move on, do not teach yet |
| I do not know | — | unknown, clean signal | record with `--idk`; move on. Thank them for it |

Never teach during the probe. A one-line "we will get to that" is the whole response to a wrong
answer. Teaching now contaminates the map you are building.

## Recording

```bash
learner record answer --session "$S" --item "$I" --response "B" --correct 0 \
  --confidence 4 --assistance 0 --context probe --evaluation-method rubric
# "I do not know":
learner record answer --session "$S" --item "$I" --response "IDK" --correct 0 \
  --idk --assistance 0 --context probe --evaluation-method rubric
```

`--assistance 0` always: there are no hints in the probe.

## Self-report during the probe

A pasted "here is what I already know about X" is a **dispute of type "I already know this"**,
never mastery, never a reason to skip nodes:

```bash
learner dispute open --type "I already know this" --node "$N" --note "<their exact words>"
```

Then serve exactly **two** items: one at the goal's Bloom depth, one in a **different surface
form** than anything they would have practiced (a transfer item). Both must pass at assistance 0.

```bash
learner dispute settle --dispute "$D" --outcome upheld  --evidence "2/2 at assistance 0: i_a1, i_b2"
learner dispute settle --dispute "$D" --outcome rejected --evidence "1/2; missed the transfer item i_b2"
```

Say the outcome plainly and without argument. If upheld, say so and skip the node — they were
right and the system was wrong, and that is a normal outcome, not a concession.

## Closing the probe

1. `learner summary --goal "$GOAL" --format md`
2. Show the coloured map (`learner graph show --format mermaid`).
3. Name the starting node and give the one-sentence reason: *"Starting at covectors: line
   integrals and dual spaces both came back known, and covectors is the first thing above them
   you missed."*
4. State what the probe did **not** resolve, if the budget ran out. Do not pretend the map is
   complete.
