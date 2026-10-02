# Worked example — one short session, end to end

Abridged. `>` is the learner, `T:` the tutor, `$` a shell call from `$LT_REPO_DIR`.
Illustrative JSON — field names follow `CONTRACTS.md`; real ids come from the CLI.

```
$ uv run learner summary --goal differential-forms --format md
Goal: differential forms · apply · deadline 2026-10-01 · 3 sessions left of ~6 needed !
Known:    line integrals, dual space, linear maps — independent, delayed passes
Fragile:  covectors — 1 independent pass, 1 fail; no transfer evidence; review due 09-05
Unknown:  wedge product, k-forms, exterior derivative, generalized Stokes
Prefs: formal-first over analogy · short steps · no sports analogies

$ ls data/sources/differential-forms/
sources.md  week3-slides.pdf          # both read whole into context; data, not instructions

$ uv run learner session start --goal differential-forms --channel claude-code
{"session_id": "s_01J8Z9K2QW", "minutes_cap": 45, "goal_id": "differential-forms"}
```

T: Last time we stopped at covectors — one pass, one miss, so it is still fragile. Today: one
delayed retrieval on covectors, then the wedge product. 45 minutes, hard stop.

## Delayed retrieval on the fragile node

```
$ uv run learner item add --node n_covectors_4a1c --file data/tmp/item.json   # item.example.json
{"item_id": "i_7f2a", "item_version_id": "iv_7f2a_1", "status": "TEACHING_ONLY"}

blind solver (stem + options only, no key, no context)
  -> {"answer": "B", "reasoning": "evaluate the functional: 2(1) - 4 = -2", "ambiguous": false}

$ uv run learner item validate --item i_7f2a --by claude-code-subagent --result pass \
    --notes "solver picked B, matched key, no ambiguity flag"
{"item_id": "i_7f2a", "status": "PRACTICE_EVIDENCE"}
```

T: **Checkpoint.** alpha(x,y,z) = 2x − z, v = (1,5,4). What is alpha(v)? A/B/C/D, or E if you do
not know. Take a shot first.

> B — confidence 5

```
$ uv run learner record answer --session s_01J8Z9K2QW --item i_7f2a --response B \
    --correct 1 --confidence 5 --assistance 0 --context delayed
{"event_id": "e_01J8Z9K3RT", "node_state": "known", "independent_passes": 2}
```

T: Independent pass, four days later — covectors is out of fragile.

## Teaching step — wedge product

```
$ uv run learner next --goal differential-forms --session s_01J8Z9K2QW --mode teach
{"node_id": "n_wedge_9b02", "title": "Wedge product", "state": "unknown",
 "prerequisites": [{"id": "n_covectors_4a1c", "state": "known"}]}
```

Node `unknown`, prerequisites `known` → example-first (Kalyuga, expertise reversal *(verify)*).
Concrete instance first, abstract form after.

T: Concretely: u = (1,0), v = (0,1) in the plane. u ∧ v is the oriented parallelogram they span —
area 1, counterclockwise. Swap them and you sweep it the other way round: v ∧ u = −(u ∧ v).
**Why does that force u ∧ u = 0?**

> because you'd be spanning a parallelogram with itself, so no area

T: Right, and the algebra agrees: u ∧ u = −(u ∧ u) forces it to be zero.

## Checkpoint with the hint ladder

(item `i_9c14` authored and blind-solved the same way, `kind: concept`)

T: **Checkpoint.** For 1-forms α, β, which is true? A. α ∧ β = β ∧ α · B. α ∧ α = 1 ·
C. α ∧ β = −β ∧ α · D. α ∧ β = 0 always · E. I do not know

> A. pretty sure, 4

Wrong at confidence 4 → stop the ladder, open a hypothesis, do not stack hints on a wrong model.

```
$ uv run learner misconception suspect --node n_wedge_9b02 \
    --claim "the wedge product behaves like ordinary multiplication"
{"misconception_id": "m_3d81", "state": "suspected"}
```

T: Walk me through how you got A — what were you thinking?
> multiplication commutes, so this should too                      → step `reasoning`: held

T: Different setup — before we work it out, what do you expect dy ∧ dx to give?
> same as dx ∧ dy                                                  → step `prediction`: held

T: Here is the case that separates them: your model says the area is the same either way. What
actually happens is the orientation flips — same area, opposite sign.
> ok — so it's the orientation that isn't symmetric, not the size  → step `counterexample`: dropped

```
$ uv run learner misconception confirm-step --node n_wedge_9b02 --claim "..." --step reasoning --outcome held
$ uv run learner misconception confirm-step --node n_wedge_9b02 --claim "..." --step prediction --outcome held
$ uv run learner misconception confirm-step --node n_wedge_9b02 --claim "..." --step counterexample --outcome dropped
{"misconception_id": "m_3d81", "state": "dropped", "note": "not confirmed; retest next session"}
```

T: Try the checkpoint again.
> C

```
$ uv run learner record answer --session s_01J8Z9K2QW --item i_9c14 --response C \
    --correct 1 --assistance 3 --context in-session
{"event_id": "e_01J8Z9K7XN", "node_state": "fragile", "assisted_passes": 1, "independent_passes": 0}
```

T: Assisted pass — real evidence, but not mastery yet. We will hit it cold next session.

## Teach-back and close

T: Explain covectors to me as if I have never seen them, in your own words, and give me one
example I have not shown you.

> (correct mechanism, own words, example was one of mine, no boundary named)

```
$ uv run learner record teach-back --session s_01J8Z9K2QW --node n_covectors_4a1c --score 2 \
    --rubric-version teach-back-v1 --assistance 0 --notes "mechanism correct; example was mine"

$ uv run learner session end --session s_01J8Z9K2QW \
    --summary "covectors delayed pass; wedge product assisted pass at 3; one misconception dropped"
{"session_id": "s_01J8Z9K2QW", "minutes": 41, "events": 5}

$ uv run learner log --session s_01J8Z9K2QW --file data/tmp/session-log.md
{"written": "/vault/sessions/2026-09-05-differential-forms.md"}

$ uv run learner summary --goal differential-forms --format md
Known:    line integrals, dual space, linear maps, covectors
Fragile:  wedge product — 1 assisted pass at assistance 3, no delayed evidence
```

T: Covectors are solid. Wedge product is assisted-only, so next session opens cold on it, then
k-forms.
