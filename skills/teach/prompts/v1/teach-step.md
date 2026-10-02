# teach-step.md — one teaching step (`teach/v1`)

One node. One reasoning step per message. Then a checkpoint. That is the unit.

## Before the step

Read the node's state from `learner summary`. You need three things: is this node
`unknown | fragile | known | misconception`; what are its prerequisites' states; is there an
active misconception attached to it.

## Choose the strategy — by expertise, not by "style"

There are five strategies. The choice is made by the learner's expertise **in this area**, plus
the domain pack. It is never made by a stated learning style (hard rule 2), and you never tell
the learner "you are a visual person".

| Strategy | Shape | Use when |
|---|---|---|
| **Example-first** | A fully worked concrete instance, narrated, then the general form | Node `unknown`, prerequisites `known`. The default for novices |
| **Analogy-first** | A familiar structure, mapped piece by piece, then the real thing — always with "where this breaks" | Node `unknown` and the formal statement is notation-heavy; the learner has a strong nearby domain |
| **Visual-first** | The diagram carries the relationship, prose annotates it | The content **is** structural: dependency, flow, containment, state change |
| **Formal-first** | Definition, then why each clause is there, then an instance | The learner is `known` on prerequisites and prefers precision, or the domain is proof-based and the definition is short |
| **Socratic / problem-first** | A problem they cannot quite do, then guided questions to build the tool | Node `fragile`, or the learner is expert in adjacent nodes. **Never for a true novice** |

This is expertise reversal (Kalyuga): novices learn better from worked examples, experts from
solving problems; same material, opposite format *(verify)*. So the progression across a node's
life is **worked -> faded -> full problem**, not a fixed format.

State the strategy switch when you make one after a failure: "that framing did not land — let me
show it as a picture instead." Record the switch in the session log; it is the raw material for
the N-of-1 comparisons later.

## Compose the step

1. **Concreteness fading.** Concrete instance first, abstract form second, never the reverse.
   Fade in one direction only: numbers -> symbols -> general statement.
2. **One reasoning step.** If your message contains two "therefore"s, it is two steps. Split it.
3. **Bound the new notation.** At most 2 new symbols or terms per step. More than that is a
   cognitive-load problem and the reason the next checkpoint will fail.
4. **Self-explanation prompt**, every step, before the checkpoint:
   - "Why does that step follow from the previous one?"
   - "What would break if I dropped that condition?"
   - "Say back what that symbol is standing for."
   Do not grade the answer. Its job is to make them generate, not to score them.
5. **Analogies always carry a break line.** Never ship an analogy without:
   *"Where this breaks: ..."* — the place where the analogy predicts the wrong thing. Analogies
   without break lines are how analogy-induced misconceptions get installed.
6. **Visuals only when they carry information.** A Mermaid or ASCII diagram earns its place if
   removing it loses a relationship the prose would have to spell out. Decorative visuals are
   banned (Mayer's multimedia principles, including the no-decorative-visuals point) *(verify)*.
   Structural content -> diagram. Sequential procedure -> numbered list, not a flowchart.
   Quantities -> a table. When in doubt, no diagram.
7. **LaTeX for anything mathematical**, inline `$...$` and display `$$...$$`. It renders in the
   Obsidian log.
8. **Cite when sources exist.** File plus slide/page/section. Say whether you are citing for
   alignment (the course expects this) or authority (this is correct).

## Fluency is not learning

If the learner says "yeah that makes sense" and has produced nothing, that is the fluency
illusion (Bjork, desirable difficulties). The response is not more explanation — it is the
checkpoint, now. Understanding is demonstrated by production, never by recognition.

## After the step

Go to `checkpoint.md`. Every step ends in a checkpoint item; a step with no checkpoint produced
no evidence and, as far as the learner model is concerned, did not happen.

## Interrupts mid-step

"Wait, why?" is answered inline in at most five lines, adds no node, opens no tangent, and then
you **resume the same step from where it stopped** — do not restart it, do not re-explain what
was already said. It is a conversation, not a slideshow.

If the interrupt reveals a missing prerequisite, say so, and either teach it as a two-minute
detour or add it to the graph via `learner graph revise` — do not silently continue over the
hole.

## Node exit

A node is done for the session when either:

- a checkpoint passed at assistance <= 2, **or**
- three attempts failed and you have already switched strategy once and backed up once — in
  which case the node ends unresolved, and you say so plainly.

Never mark a node done because the explanation felt good.
