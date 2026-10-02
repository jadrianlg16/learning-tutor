# web-ui

The browser surface for Stage 2. Next.js 15 (App Router) + TypeScript, no UI kit, one global
stylesheet. It talks to the **gateway only** — never to `learner-svc` or `render-svc` directly —
against the shapes pinned in [CONTRACTS.md](../../CONTRACTS.md) *Gateway response shapes*.

Source: `web-ui/`.

## What it is

Three ideas from [IDEA.md](../../IDEA.md) drive the whole design and are worth stating before the
route list, because they are why several screens look the way they do:

1. **Show the learner their own map** (*What's missing*, item 9). The map page is two graphs side
   by side — the curriculum map and the learner path — coloured by the four states, and every node
   is clickable.
2. **Evidence, never decimals** (*Adopted — build later*). Nothing in this UI shows a mastery
   probability, and a concept's **state** (the map colour: unknown / fragile / known /
   misconception) never gets a number — it is a word and a colour, backed by receipts (counts,
   dates, assistance levels, evaluation methods); `learner.md` is rendered as prose.
   **First-try accuracy is the one allowed percentage**, because it is a count ratio, not an
   estimate: it is always called *first-try accuracy* (never mastery), always printed with its
   counts ("7 of 10 first try") and its 95% range, the range is drawn more prominently than the
   point when attempts are few, and with 0 attempts the screen says *no data yet*, never 0%.
   Mock scores ("41 of 60") and shares of the exam blueprint follow the same rule. A percentage
   *on a concept's state*, or one without its counts, is a bug.
3. **Nothing becomes mastery by assertion.** The hint ladder shows its level *name* and says out
   loud when a pass will not count; the reveal is disabled until an attempt (and the gateway
   returns 409 if the UI is bypassed); the six typed disputes open a check, not a shortcut.

## Pages

Routes use a query parameter for the goal id (`?g=`) rather than a `/goals/[g]` path segment.
That is the one deliberate deviation from the planned route list, and the reason is
`output: 'export'`: a static export cannot emit a dynamic path segment whose values are only
known at runtime without a server-side rewrite, and keeping the export dumb is what lets the
gateway serve the build straight from `LT_WEB_DIR` with no config. The screens are the same.

| Route | What it does |
|---|---|
| `/` | Goals list with per-goal state bar and phase; **new goal** form = the full goal contract (concept, depth, purpose, deadline, minutes/session, sessions/week, assessment, source priority, domain, target capabilities, transfer required). |
| `/goal/?g=<id>` | The session screen. Renders by `phase`: grounding → plan → probe → teach → done. Also holds the `learner.md` viewer (`summary_md`) and links to the map and metrics. |
| `/goal/map/?g=<id>` | Curriculum map + learner path, coloured by state; click a node → receipts panel. `&node=<node_id>` deep-links a concept's receipts. With an exam blueprint: grouped by area, weighted boxes, colour-by toggle (see *Exam blueprint on the graphs*). |
| `/goal/metrics/?g=<id>` | The `GET /metrics` passthrough. Renders whatever keys arrive; `x_n` becomes the observation count column and `x_note` the caveat. `null` renders as *not measurable yet*, never as `0`. |
| `/goal/study/?g=<id>` | Study tools, no model: **Progress** / Practice / **Mock exam** / Cards / Tables / Import tabs. Progress is the default for a goal with an exam blueprint, Practice otherwise. See *Study tools* and *Exam prep* below. |
| `/passport/` | `GET /api/passport` download button, and what is in the archive. |

### The session screen, phase by phase

- **grounding** — upload sources with a role (alignment / authority / learner, each with its
  epistemic meaning spelled out); run a research pass and approve the proposed list item by item;
  *Build the plan* moves on. Optional, and the UI says so.
- **plan** — the dependency graph, the feasibility line, the cite-or-abstain verification table
  with **abstentions shown**, and the one-time *review & approve* with an edit-ops box
  (JSON array, passed through as `ops`).
- **probe** — the question card with `asked/budget`, "I do not know", confidence 1–5 when
  `ask_confidence`, and the node's new state after each answer.
- **teach** — the step (markdown + KaTeX + a diagram), a *wait, why?* interrupt box, the
  checkpoint card with the hint ladder, answer feedback, the decision banner, the teach-back
  textarea and score, and the misconception dialog for the three-step sequence.
- **done** — session summary and the path of the session log.

### Graphs

The graphs are drawn **from the `nodes` and `edges` lists**, not from the gateway's mermaid
string: the map has to be coloured by state, clickable and navigable, and a rendered mermaid SVG
is an opaque blob that gives us none of that. The gateway's mermaid is still shown (under
*mermaid source*) and can be sent through `POST /api/render`.

Two files, split so the hard part is testable on its own:

- **`src/lib/graph.ts`** — pure layout. No React, no DOM.
- **`src/components/GraphCanvas.tsx`** — interaction only: pan, zoom, hit-testing, search.

**Chapters, not one strip.** A real goal graph is not one tree: one 40-concept CS-review goal of mine has
**ten** weakly connected components over the structural edge types (algorithms, graphs, automata,
networking, SQL, …). Laid out as a single layered DAG that is a ~2,700 px-wide banner that fits
to width at 30-50% — unreadable, with no way to expand or collapse anything. So each component
is laid out on its own and the blocks are packed into rows
that wrap. If a graph really is one giant component (one course, one root), the component split
would degenerate to a single block, so `clustersOf()` falls back to grouping by **root ancestor**
when the largest component is ≥ 60% of a graph of more than 12 nodes.

**The packing chooses the zoom.** Column count and fit-to-width scale are the same decision: a
wider packing fits to a smaller scale. Given the canvas width, the layout takes the *widest*
packing that still fits to width at ≥ 0.8× — the most chapters per screen that stays legible —
and everything else is reached by panning. Nodes are a fixed 186 px wide and wrap to at most 3
lines at 12 px; a layer wider than 4 nodes wraps inside its chapter so one fan-out cannot blow
the block out sideways.

**Interactions** (all of them on both graphs, and on the plan's dependency graph):

| | |
|---|---|
| zoom | wheel/trackpad around the cursor, pinch, `+` / `−` buttons, `+`/`-` keys. 0.25×-4× |
| pan | drag (cursor turns to `grabbing`), one-finger touch drag, arrow keys |
| fit | **fit** (whole graph) and **reset** (fit to width, also `0`); fit-to-width runs on first paint and after every re-flow |
| expand | full-viewport overlay, same interactions inside, **Esc** closes |
| collapse | click a chapter header (or its collapsed box) to toggle; **collapse all / expand all**; the collapsed set is stored per graph in `localStorage` under `lt-collapsed:<key>:<goal>` |
| search | filters and dims non-matches, **go** (or Enter) centres the first hit and opens its chapter if collapsed |
| detail | hover a node for state, counts and learner-svc's own `reasons` bullets; click for the full receipts |

A collapsed chapter renders as **one box the size of a normal node**, coloured by its dominant
state, labelled with the concept count — and edges into and out of the chapter are re-pointed at
it rather than disappearing. Edge type is visible in the line: strict prerequisite solid,
recommended/course sequence dashed, transfer/supports/misconception dotted, all with arrowheads.

**No graph library.** d3, cytoscape and react-flow are all ~200-500 kB for what is one SVG
transform plus pointer arithmetic, and the static export has to keep working; the map page
stays a few kilobytes of its own code, blueprint overlay included. Two pointer-event details
are worth keeping in mind if this is ever touched: `setPointerCapture` is taken **after** the
drag threshold, never on `pointerdown`,
because capturing early retargets the compatibility mouse events and nothing on the graph is
clickable any more; and the wheel listener is a **native non-passive** one, because React routes
`onWheel` through a passive root listener where `preventDefault()` does nothing and the page
scrolls instead of the graph zooming.

`POST /api/render` **is** used for the arbitrary diagram a teach step embeds (`step.mermaid`),
where there is no node list to lay out. If that call fails the step still renders — the diagram
is an aid, not the content. No `mermaid` npm package is bundled (it is ~3 MB and would not have
been clickable anyway).

### Exam blueprint on the graphs (2026-09-26)

When `GET /goals/{g}/blueprint` returns one (CONTRACTS.md *Exam blueprint, mixed practice and
sealed mock exams*), the map's two graphs and the plan's dependency graph stop clustering by
graph shape. An exam goal of 14 subáreas joined by cross-area edges is one component, and a
root-ancestor split of it cuts areas in half; the exam's own structure is the chapter. A `404`
from `/blueprint` means "no blueprint" and the screen renders exactly as before.

Files: `src/lib/mapBlueprint.ts` (pure: joins `/blueprint` + `/progress` + the graph's nodes into
an overlay, area groups, box sizes, colour bands), `src/components/MapExam.tsx` (data hook, the
colour-by toggle, legends, the drawer's numbers), and additions to `lib/graph.ts`
(`groups` / `box` layout options, `stack` chapters, taller headers), `GraphCanvas.tsx` (`exam`
prop), `goal/map/page.tsx`, `PlanPanel.tsx`, `ReceiptsPanel.tsx` (`exam` prop). No new types or
API functions were needed: `api.blueprint`, `api.progress` and their types already existed.

- **Chapters = areas, in official order.** Header: *Área 3 · 49 reactivos (34%)*, the area title,
  the area's own counts (*5/13 first try · 13/15 seen · 11 due*) and a thin bar for its share of
  the exam. The full *Área 3 · Desarrollo de Sistemas de Software · 49 reactivos (34%)* is the
  header's accessible name and tooltip. Subáreas stack in official order (1.1, 1.2, …).
- **A box's width is its weight.** `30 + 270 × items / heaviest`, floor 180 px: 2.2 (16) is
  300 px, 1.1 (12) 233, a 10-item subárea 199, 2.3 (7) 180. Boxes are left-aligned, so each area
  reads like a bar chart of where the points are. Each box: ref + a *12 reactivos* pill, the
  official title, a thin coverage bar (seen of bank) and counts: *2/3 seen*, *7/10 first try* or
  *no first try yet*. **No percentage appears inside a box.**
- **Colour by** (radio group above the map; arrow keys move and select; remembered per goal in
  `lt-map-colour:<goal>`): **State** (default, the four state colours); **First-try accuracy**
  in bands against two generic reference lines (defaults: 80 % target, 70 % floor; `TARGET` /
  `FLOOR` in `web-ui/src/lib/progress.ts`), paler under 5 first tries, and **hatched + dashed
  for no first attempt** (no data is not 0); **Coverage**, a four-step accent ramp of
  seen/bank, hatched when the bank is empty. The legend under the
  toggle is painted by the same function as the boxes, so it cannot drift. In the two non-state
  modes a small dot keeps each box's state visible.
- **Drawer.** A subárea's numbers sit above the existing receipts: weight and share, practice
  bank (checked, sealed), seen with a bar, first try *k of n right · 95% range low–high%* (or
  "no first try yet · no range until there is one"), all attempts, due now.
- **Not in the blueprint** — graph concepts the blueprint does not name get their own plain
  chapter after the areas. **Unresolved subáreas** (a `ref` with no live concept, or a node id the
  graph does not have) still get a dashed box, a note in the summary line, and a drawer with
  their numbers and no receipts call.
- **Edges** are drawn under the chapters in exam mode, and edges between two areas are faded
  (`.gedge.cross`), so a long cross-area dependency never strikes through an area header or a
  box's text. Subárea boxes have an opaque backing for the same reason.
- **Plan graph** (`PlanPanel`) uses the same drawing, colours fixed to State, with one line
  above it (*Grouped by the exam blueprint: 4 areas, 143 reactivos …*). The geometry is tight
  enough that four areas sit in one row at ≥ 0.8× in a desktop-width plan panel.
- Search also matches a subárea's ref (`3.2`) and official title. *collapse all* now counts
  only chapters that exist, so ids stored under an older clustering cannot flip it to
  *expand all*.

This was checked in headless Chrome at desktop and phone widths, in light and dark: official
order, box widths, no percentage inside a box, fit to width, the colour modes, the drawer,
unresolved subáreas, every interaction, and that a goal without a blueprint renders exactly as
before. **Not verified here:** the real gateway. The mock gateway serves the same 13-node graph
as every goal's `/map`, so on the mock the exam goal's map shows the unresolved case: dashed
subárea boxes plus a *Not in the blueprint* chapter.

### Panels, drawer, phase review

- **`Collapsible`** (`src/components/Panels.tsx`) — every large panel has a chevron and remembers
  its state in `localStorage` (`lt-panel:<key>`). `learner.md` starts collapsed; the plan graph
  starts open; the verification table and the mermaid source start collapsed.
- **`Drawer`** — receipts open in a right-hand drawer on desktop and a bottom sheet under 860 px,
  never as a card below the fold: clicking a concept no longer scrolls the graph off screen.
  **Esc** or the × closes it and drops `?node=` from the URL.
- **`TruncatedList`** — long inline lists (the plan's abstentions) show six and then *show all N*.
- **Phase rail** — a finished phase is clickable and opens a **read-only** review (grounding: the
  source table; plan: the stored graph and, if a build has been run in this view, the
  cite-or-abstain table; probe: what it left on the map). A phase you have not reached is
  disabled, with a title that says so: nothing here lets you skip forward.
- **Sticky goal header** — title, deadline, cadence, assessment and the live phase stay on screen
  while you scroll (desktop; static under 860 px).

### The plan build is no longer automatic

`POST /goals/{g}/plan` runs the model over the corpus and **re-imports the graph**. The old
`PlanPanel` fired it from a `useEffect` on mount, so opening a goal parked in the plan phase cost
an LLM run and a graph re-import every time. It now reads the stored graph from `GET /map` and
draws that; *rebuild with the model* is a button, and the automatic build happens only when the
goal has no graph at all (a new goal, where there is nothing else to show). Approval is
unaffected — the gateway keeps the stored plan, so *approve* still works without a rebuild.

### Feasibility

`POST /plan` returns `feasibility: {sessions_needed, sessions_available, verdict, assumption}`,
and `PATCH /goals/{g}` returns a recomputed one; when either is present the UI prints that.
When there is none (the graph was read from `GET /map` rather than built in this view, or the
gateway sent `null`), `src/lib/feasibility.ts` computes a fallback and prints its assumption
next to it: *"16 sessions left before the deadline, about 4 needed: that fits — 8 concepts not
yet known, ~20 min each (one teach step, one checkpoint, one teach-back), 45 min per session.
Estimate, not a measurement."*

## Study tools (2026-09-24)

Built against CONTRACTS.md *Study tools → Gateway additions*. Files: `src/app/goal/study/page.tsx`,
`src/components/{PracticePanel,CardsPanel,TablesPanel,ImportPanel,GoalEditForm}.tsx`,
`src/lib/study.ts` (shortcut guard, due-date formatting, seeded shuffle).

- **Goal edit** — *edit dates* in the sticky goal header opens deadline / minutes / sessions per
  week. Only changed fields are sent to `PATCH /goals/{g}`; an emptied deadline is sent as `""`
  (clear). The screen then re-reads the goal, so the pills and the plan's feasibility line use
  stored values. Feasibility order: PATCH's recomputed `feasibility` if non-null, else a plan
  built in this view (dropped after any edit, since it is stale), else `src/lib/feasibility.ts`.
- **Practice** — one question from `practice/next`; A–E pick, 1–5 confidence (optional, press
  again to unset), Enter submits and then moves on. Feedback shows your answer vs the key, the
  explanation, and whether it counts: `counts_toward_mastery`, or for `checked: false` the
  sentence *not yet verified by an independent checker — practice only, not proof*. *Questions
  the checker disagreed with* (`practice/review`) keeps each key, the solver's pick and the notes
  out of the DOM until *show answer*.
- **Cards** — front → *Show answer* (Space) → `cards/reveal` → Again/Hard/Good/Easy (1–4). The
  screen says the rating is self-report and never proof. *Export for Anki (TSV)* is a real link to
  `cards/export`, fetched on click so an error shows inline instead of as a raw page.
- **Tables** — rendered with row headers inside `.table-wrap`, which scrolls on its own at phone
  width. Fill-in hides any column but the first; each cell becomes a `<select>` of that column's
  values, shuffled with a seeded shuffle (stable across re-renders, new order on *Reset*). *Check*
  scores locally and sends nothing. *Make flashcards* → `tables/{t}/cards`.
- **Import** — a file from `importable` (optionally a second one as the key) as JSON, or an
  upload as multipart (`file`, `key_file`, repeated `what`, `dry_run` `"1"|"0"`). *Preview* is
  the dry run; the report labels the middle column *would import* when `dry_run` is true.

Shortcuts go through `allowShortcut()`: never in inputs/selects, never with a modifier, and
Enter/Space on a focused button or link is left to the browser (it would fire twice otherwise),
except on the selected tab and inside `data-enter-submits` groups. The active tab is stored as
`lt-study-tab:<goal>`. The mock (`src/mocks/gateway.ts`) implements every route with a per-goal
copy of the fixtures, grades and parses server-side (keys never reach the UI early), and has one
rule of its own: a past deadline is a 400, so the inline error is reachable.

Every tab was driven by keyboard and mouse against the mock gateway at desktop and phone widths,
with no horizontal page scroll on a phone; header pills wrap and `.prose`/`.qstem` have
`min-width: 0` so a long assessment or a display equation cannot widen the page. **Not
verified:** the real gateway, and saving an actual export file.

## Exam prep: Progress, practice focus, Mock exam (2026-09-26)

Built against CONTRACTS.md *Exam blueprint, mixed practice and sealed mock exams* (including the
additive fields `MockSummary.areas`, `MocksResponse.sealed_unchecked`, `BankCounts.sealed`).
Files: `src/components/{ProgressPanel,MockExamPanel,Charts}.tsx`, `src/lib/progress.ts`
(pure: Wilson range, verdict words, today's plan, pace, ticks), `src/lib/mockDraft.ts` (the open
mock's local draft), the new functions in `src/lib/api.ts` (`blueprint`, `progress`,
`listMocks`, `startMock`, `getMock`, `submitMock`; `practiceNext` gains `focus`, `practiceAnswer`
gains `order`), the types at the end of `src/lib/types.ts`, and one marked block at the end of
`globals.css` (*EXAM PREP*).

**Tabs.** Progress · Practice · Mock exam · Cards · Tables · Import. With nothing stored under
`lt-study-tab:<goal>`, the page waits for `GET /study` and opens Progress when `blueprint` is
true, Practice otherwise. While a mock is open (`study.mock.open`), the Mock exam tab carries an
*open* badge and every other tab shows a *Resume the mock* banner. At ≤ 560 px the six tabs are
two rows of three.

**Practice.** A focus selector (a `<select>`: *Mixed — every area, by exam weight* — the
default — then the 4 areas, then the 14 subáreas grouped by area) sits above the card and is
hidden when the goal has no blueprint. It is stored as `lt-practice-focus:<goal>` and sent as
`practice/next?focus=`; the first question waits until the page knows whether there is a
blueprint, so a stored focus is applied before anything is served. The card shows the
question's `ref`, concept and area. The answer carries the question's `order` (the shown option
order), so the key is graded against what was on screen. Counts under a focus are the whole
bank's (that is what learner-svc returns) and the line says so. *practise area N* / *practise*
links on the Progress tab switch to Practice with that focus.

**Progress.** Everything comes from `GET /progress` (plus `GET /study` for today's new-question
room). Sections, and why each has the form it has:

| Section | Form | Reading |
|---|---|---|
| Header | KPI tiles + one sentence | Days to the exam (the one hero number), today's `new · due` questions and cards, exam weight touched (`disciplinar.coverage`), whole-bank first try (counts + range), blueprint-weighted first try (`weighted_accuracy` with its counts, or the gateway's `note` while it is null). The sentence — "Today: 20 new + 18 due questions and 4 cards — about 110 min for the questions at the exam’s pace (2.9 min each)." — is arithmetic on those counts; minutes appear only when a mock supplies a pace (your own from mocks you actually sat: ≥ 10 answers, ≥ half answered, ≥ 30 s per answer — a judgement; else the pace a mock's time limit implies). A second line names where to point practice (untouched area by weight, an area whose whole range is under the floor, else the weakest with ≥ 5 first tries). |
| By area | small multiples: three aligned 0–100% tracks per area | exam weight (gray meter — context), bank seen (accent meter, `seen of bank · N sealed`), first try (dot + 95% whisker, floor dashed, target solid — two generic reference lines, 70 % and 80 % by default, `FLOOR`/`TARGET` in `src/lib/progress.ts`). A verdict word with an icon (✓ meets target / ▲ between / ✕ under the floor / ○ no data) is worded by how sure the range is ("on target so far, range still wide"). Under 5 attempts the row says "read the range, not the dot". |
| 14 subáreas | compact grid of cells | the same strip per subárea, `k of n first try · low–high%`, exam items, the concept's state as a word, a link to its receipts and a *practise* button. A subárea with no first attempt is a dashed, empty cell that says *no data yet* — visibly different from 0%. |
| Last 28 days | two stacked panels sharing the x axis and the y scale | answers per day as one-hue stacked columns (correct solid, the rest a lighter step), card reviews per day in gray below (self-rated, never evidence — kept apart from answers on purpose). |
| Reviews coming due | columns, today → exam day | `forecast`, with *today* and *exam day* marked. |
| Mock exams | small multiples (overall + one per area) | score per mock on a 0–100% axis with the floor and target lines and `k/n` over each column, from `MockSummary.areas`. Empty state points to the Mock exam tab. |

Goals without a blueprint get the header (with *deadline* instead of *exam*), a *By concept*
grid over `unassigned`, activity, forecast and mock history.

**Chart rules** (`Charts.tsx`): one hue. `--viz-solid` is the accent and `--viz-soft` a
lighter step of it, with at least 2.7:1 contrast against the surface in both themes (light
`#9d93e3` on white, dark `#5b50b8` on `#171b23`); the existing gray `--unknown` (3.0:1 light,
4.7:1 dark) is used only for context that is not evidence. A three-series categorical stack
(correct / wrong / cards) was tried first and rejected: the gray has too little colour to
read as a category of its own. Columns ≤ 24 px with a 4 px
rounded top and a 2 px surface gap between stacked segments; dots ≥ 9 px with a 2 px surface
ring; hairline solid gridlines; text never wears a series colour. Every chart has an
`aria-label` summary in words and a *Show as a table* twin. Column charts are one tab stop:
hover or ←/→/Home/End move a readout (tooltip + a polite live region); Esc clears it.
Inline SVG and plain HTML/CSS only — no chart library, no new dependency.

**Mock exam.** Lobby: sealed questions available per area, size 30 / 60 / all (only sizes
below what is available; default 60 when ≥ 60 are available, else all), past mocks with
*Review*; with none available it says why, and shows `sealed_unchecked` when that is what is
blocking. Exam screen: a countdown from `ends_at` (corrected by the browser-vs-server clock
difference measured at start), a navigator grid (answered = filled, flagged = a folded corner,
so not colour alone; current = outline), flag for review, change or clear an answer, optional
confidence 1–5. Keys as in practice: A–E pick, 1–5 confidence, F flag, ←/→ or Enter move —
nothing on the keyboard submits. **No feedback of any kind until submit**: no right/wrong, no
key, no explanation, and the area/ref are not shown either. Answers are kept in
`localStorage` under `lt-mock:<session_id>` as the chosen option's *stored* index (so a reload
can never re-point an answer at a different option), with the flags, confidence and current
question; every access is wrapped, and if storage is blocked the screen says a reload would
lose the answers. Reloading the page reopens the Mock exam tab and resumes the open mock. Submit
asks first ("22 of 30 unanswered — they will be recorded as I do not know. 1 still flagged");
when the clock reaches zero the page submits by itself with what it has. On phones the status
(`8/30 · ⚑1`, timer, Submit) is one bar fixed to the bottom of the screen. Results: score with
its 95% range, answered, time used, then by area and subárea (the same strip, Wilson range
computed client-side from the counts), then a review list — your answer vs the correct one,
the explanation — with an *only wrong or unanswered* filter. Past mocks open the same view.

### Fake gateway additions

`src/mocks/exam.ts` holds the study record and the exam engine; `src/mocks/isoft.ts` is the
fixture. A fourth seeded goal, **`egel-isoft`** (deadline 45 days after the mock loads), has the real ISOFT
blueprint (4 areas, 14 subáreas, 143 items: 1.1:12 … 4.3:12) and a bank of **original**
three-option questions in the exam's case style — 3 practice + 5 sealed per subárea, 42 + 70 —
not a real question bank, which stays in the gitignored `data/` folder and is not copied into
tracked source. Two weeks of seeded history cover areas 1, 3 and 4.3 and leave area 2 and most
of area 4 empty, so *no data yet* and the mixed pull toward them are visible on first load. The
engine: mixed practice picks new questions for the concept furthest below its blueprint share
of the questions seen so far (equal shares without a blueprint); every serve carries a
per-item, per-local-day `order`, grading maps the letter back through the `order` it receives;
sealed items never reach practice, cards or the export until a mock has used them; a mock is
a blueprint-weighted largest-remainder draw with shuffled questions and options, graded on
submit into ordinary attempts (unanswered = idk), after which its items join practice; progress
(tallies, Wilson ranges, the weighted headline, activity, forecast, mocks) is computed only
from attempts recorded in the fake, so the charts move as you practise. Wording of
`disciplinar.note` matches `learner/exam.py`. The study record is persisted to
`sessionStorage` (`lt-fake-study:<goal>`) after every write, so a reload — in particular one
in the middle of a mock — resumes; the teaching flow still resets on reload.

### Verified

`scripts/drive-exam-prep.mjs` drives these flows headlessly against the mock gateway and asserts
as it goes: Progress as the default tab, mixed practice by keyboard and focused practice, with no
key in the page before an attempt, Progress moving with the answers, a mock answered, reloaded
mid-exam, submitted and reviewed, a mock auto-submitted when its clock runs out, and no
horizontal scroll at phone width. It uses puppeteer-core, which is **not** a dependency of
this app: install it anywhere and point `PUPPETEER_DIR` at that folder.

**Not verified:** the real gateway (the fake mirrors its shapes), a real phone (Chrome device
emulation only), and screen-reader output.

## Env

| Var | Where it applies | Default | Meaning |
|---|---|---|---|
| `NEXT_PUBLIC_GATEWAY_URL` | build time (baked into the bundle) | `/api` | Gateway base URL. Same-origin `/api` is right for both deployments below. An absolute URL needs CORS on the gateway. |
| `NEXT_PUBLIC_MOCK` | build/dev time | `0` | `1` swaps in the in-browser fake gateway. Never `1` in a real deployment; `npm run build:mock` sets it for a demo build. |
| `PORT` | `npm run dev` / `dev:mock` | `3000` | Dev server port only. |
| `WEB_PORT` | container runtime | `8080` | Port nginx listens on inside the container. |
| `GATEWAY_URL` | container runtime | `http://gateway:5033` | Origin that `/api/` is proxied to. No trailing slash. |
| `DNS_RESOLVER` | container runtime | auto-detected | Resolver for the lazy upstream lookup. Falls back to `NGINX_LOCAL_RESOLVERS`, then `/etc/resolv.conf`, then `127.0.0.11`. Only set it if that chain picks wrong. |
| `BASE_URL`, `CHROME_PATH` | `npm run screenshots` only | `http://localhost:$PORT`, probed | Where to point the screenshot script and which browser to drive. |
| `SHOT_GOAL`, `SHOT_NODE` | `npm run screenshots` only | unset | `SHOT_GOAL=<goal id>` captures the live shot set against a real gateway instead of the mock set; `SHOT_NODE=<node id>` picks the concept whose receipts the drawer shots open. |
| `PUPPETEER_DIR` | `node scripts/drive-exam-prep.mjs` only | current dir | A folder where `puppeteer-core` is installed (it is not a dependency of this app). The script also reads `BASE_URL`, `CHROME_PATH` and `SHOT_GOAL` (default `egel-isoft`). |

Nothing machine-specific is committed: every port, host and path is an env var with a default,
and the screenshot script probes for a browser rather than hardcoding a path.

## Mock mode

`npm run dev:mock` (a Node wrapper, because `NEXT_PUBLIC_MOCK=1 next dev` is not portable to
Windows' cmd.exe) starts the dev server with an **in-browser fake gateway**:
`src/mocks/gateway.ts` patches `window.fetch` and answers every route in the contract from
`src/mocks/fixtures.ts` (and, for exam prep, `src/mocks/exam.ts` + `src/mocks/isoft.ts`). It is
loaded by a dynamic `import()` from `src/lib/api.ts` behind a literal
`process.env.NEXT_PUBLIC_MOCK === '1'` check, so webpack drops the branch in a production build
(`NEXT_PUBLIC_MOCK=0`) and the fake's chunk is not even emitted into `out/`. The one exception
is `npm run build:mock`: a static export with the fake gateway baked in, for demo builds and
for the screenshots.

It is a state machine, not a stub. Creating a goal really walks grounding → plan → probe → teach
→ done; answers really move node state (by transparent rules, the same shape the real model
uses); the map and receipts read back what you just did; `Idempotency-Key` replay is implemented;
hints refuse to skip a level and the reveal 409s before an attempt.

Fixtures: the 13-node differential-forms graph from `skills/teach/examples/graph.example.json`
with `n_<slug>_<4hex>` ids, three items in the shape of
`skills/teach/examples/item.example.json`, states across **all four colours**, one *active*
misconception, one open dispute, and ten seeded events. Three teaching-flow goals are seeded,
plus the exam-prep goal `egel-isoft`, so every screen is reachable by URL:

| URL | Lands on |
|---|---|
| `/goal/?g=g_stokes` | the plan review |
| `/goal/?g=g_probe` | the probe question card |
| `/goal/?g=g_forms` | a teach step, session already open |
| `/goal/map/?g=g_forms&node=n_covectors_9c0d` | the map with receipts open |
| `/goal/study/?g=egel-isoft` | the exam-prep goal: Progress (default), focused practice, mock exams |

The teaching-flow state lives in memory, so a page reload resets it to the seed. The study
record (bank schedules, attempts, cards, mocks) is kept in `sessionStorage` per tab
(`lt-fake-study:<goal>`), so a reload mid-mock resumes; a new tab starts from the seed.

## Deployment

Two options. The repo's `docker-compose.yml` uses **B**.

**A. The gateway serves the export (preferred).** `npm run build` writes `web-ui/out/` — plain
files, no Node runtime. Point `LT_WEB_DIR` at it. `docker build --target export -o
type=local,dest=./web-out ./web-ui` extracts it from the image without running anything.

**B. The nginx container.** `web-ui/Dockerfile` is multi-stage: `node:20-alpine` builds the
export, `nginx:alpine` serves it and proxies `/api/` to `${GATEWAY_URL}` via the official
image's envsubst-on-templates entrypoint (`nginx.conf.template`). Listens on `${WEB_PORT}`
(default 8080), has a `HEALTHCHECK`, caches `/_next/static` hard, allows 64 MB uploads for
source ingest and 300 s proxy timeouts for the passport export.

`output: 'standalone'` is **not** used — nothing here needs a Node server.

One thing the compose author should know: **`proxy_pass` uses a variable on purpose.** With a
literal upstream host, nginx resolves it once at startup and refuses to boot with
`host not found in upstream` whenever the gateway container is not up yet — which would make
web-ui depend on start order and crash-loop through any gateway restart. A variable defers the
lookup to request time, so a missing gateway costs one 502 instead of a dead container. That
needs a `resolver`, which `docker-resolver.envsh` fills in from `NGINX_LOCAL_RESOLVERS`, then
`/etc/resolv.conf`, then Docker's embedded DNS — override with `DNS_RESOLVER` if needed. No
`depends_on` ordering is required for web-ui.

The image serves every page with 200 and an unknown path with 404 (there is no SPA fallback, so
a typo stays a 404). With the gateway unreachable, `/api/health` is a 502 and the container
stays healthy; with it up, `/api/` requests reach it with their path and query string intact.

## Verified

The map and session screens were verified end to end in a real browser against the running
compose stack, at desktop and phone widths: chapter layout, zoom and pan (mouse, keyboard and
synthetic touch), chapter collapse, search, the receipts drawer, no horizontal page scroll, no
model call on page load and no console errors. Against the mock gateway, the whole teaching flow was driven
from goal creation through research, plan approval, the probe, a teach step with the hint ladder
and the refused reveal, the misconception dialog, an interrupt, a teach-back and session end, to
the map, the receipts, metrics and the passport download.

### Screenshots

Three screenshots live in `web-ui/docs/screenshots/`, all from the fake gateway:

| File | Shows |
|---|---|
| `plan-graph.png` | the plan screen for the mock *Stokes* goal (`g_stokes`): dependency graph coloured by state, feasibility line, cite-or-abstain table |
| `progress-desktop-light.png` | the Progress tab of the mock exam goal (`egel-isoft`) after some practice |
| `mock-exam-desktop.png` | a sealed mock exam in progress: navigator, flag, timer |

They are captured from a production mock build (`npm run build:mock`, served as static files)
in headless Chrome driven by puppeteer-core. To regenerate them, run `npm run screenshots`
against a mock server for the plan shot, and `node scripts/drive-exam-prep.mjs` for the
exam-prep shots.

Setting `SHOT_GOAL` switches `npm run screenshots` to a live set (the map overview, the
receipts drawer on desktop and phone, and the session screen) against a real gateway:

```
BASE_URL=http://localhost:5033 SHOT_GOAL=<goal id> SHOT_NODE=<node id> npm run screenshots
```

Live shots show a real learner's data, so none are committed.

## Interrupts

The *wait, why?* box posts `POST /api/goals/{g}/teach/interrupt {session_id, node_id, question}`
(CONTRACTS.md *Gateway response shapes — additions*). The gateway answers inline against the
step already on screen and moves nothing: no step advance, no change to the assistance level, no
evidence written. A question that is really asking for the checkpoint's answer gets the
hint-ladder rule back instead (see [gateway.md](gateway.md) *Interrupts*). The UI shows
`answer_markdown` under the step, and an abstention note is part of that text. Against a gateway
without the route (404/405) it says the question was not sent anywhere rather than swallowing it.

One convention the UI relies on that the contract does not spell out, harmless if the gateway
ignores it: `metrics` may carry `x_n` / `x_note` alongside `x`.

## Not implemented

- **The `/goals/[g]` path shape.** Query parameter instead — see *Pages* for why.
- **Graph editing beyond the plan review.** The edit-ops box is a raw JSON textarea validated
  only as "is this an array". There is no visual node/edge editor, and evidence migration on a
  split or merge is the learner service's job.
- **`GET /api/goals/{g}/sources` `sources_md`** is fetched but not rendered; the source table is.
- **A shareable collapsed view.** The collapsed set is per browser (`localStorage`), not in the
  URL, so "here is the map with everything but chapter 3 folded away" cannot be sent to anyone —
  and cannot be captured by the screenshot script either.
- **Edge routing.** Edges are béziers between box centres/sides with no obstacle avoidance, so a
  cross-chapter transfer edge can run under a chapter block. They are drawn faintest of the three
  styles for that reason.
- **Real-time anything.** No polling, no SSE. A session screen reflects what the last call
  returned; another writer (Claude Code running the `teach` skill, the Telegram reminder job)
  will not show up until you navigate.
- **Auth.** There is none, in either deployment. Single user on a private host is the assumption.
- **Offline.** No service worker, no cache. The webfonts are pulled from Google Fonts by an
  `@import` with real local fallbacks; `next/font` is deliberately not used because it fetches at
  *build* time, which would make the Docker build need network access.
- **Markdown sanitising is an allow-list of my own** (`src/lib/markdown.ts`), not DOMPurify —
  tags and attributes are filtered against a list, `on*` handlers and `javascript:` URLs are
  stripped, and KaTeX output is allowed through. It is a second line of defence behind "the
  gateway does not emit hostile HTML", not a hardened sanitiser. If the corpus ever renders
  untrusted third-party HTML, swap in DOMPurify.
- **Tests.** No unit or e2e suite. Verification was the driven mock flow above.
