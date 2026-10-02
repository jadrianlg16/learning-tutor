/**
 * Fixtures for the in-browser fake gateway.
 *
 * The graph is the 13-node differential-forms example from
 * skills/teach/examples/graph.example.json, given stable `n_<slug>_<4hex>` ids. Items come
 * from skills/teach/examples/item.example.json plus two written in the same shape. States
 * cover all four colours, with one active misconception and one open dispute, so every
 * surface in the UI has something real to draw.
 *
 * None of this ships in a production build - it is only imported when NEXT_PUBLIC_MOCK=1.
 */

import type {
  Dispute,
  Goal,
  GraphEdge,
  GraphNode,
  LearnerEvent,
  Misconception,
  NodeState,
  Question,
  QuestionOption,
} from '@/lib/types';

export const N = {
  vectorSpaces: 'n_vector_spaces_1a2b',
  basis: 'n_basis_coords_3c4d',
  linearMaps: 'n_linear_maps_5e6f',
  dualSpace: 'n_dual_space_7a8b',
  covectors: 'n_covectors_9c0d',
  rowVectors: 'n_row_vectors_1e2f',
  lineIntegrals: 'n_line_integrals_3a4b',
  wedge: 'n_wedge_product_5c6d',
  kForms: 'n_k_forms_7e8f',
  extDerivative: 'n_ext_derivative_9a0b',
  pullback: 'n_pullback_1c2d',
  genStokes: 'n_gen_stokes_3e4f',
  classicalStokes: 'n_classical_stokes_5a6b',
} as const;

export const NODES: GraphNode[] = [
  { id: N.vectorSpaces, title: 'Vector spaces', aliases: ['linear spaces'] },
  { id: N.basis, title: 'Basis and coordinates', aliases: ['coordinate representation'] },
  { id: N.linearMaps, title: 'Linear maps', aliases: ['linear transformations'] },
  { id: N.dualSpace, title: 'Dual space', aliases: ['V*', 'space of functionals'] },
  {
    id: N.covectors,
    title: 'Covectors',
    aliases: ['dual vectors', '1-forms', 'linear functionals'],
  },
  { id: N.rowVectors, title: 'Row vectors are covectors', aliases: ['row-vector picture'] },
  { id: N.lineIntegrals, title: 'Line integrals', aliases: ['path integrals of vector fields'] },
  { id: N.wedge, title: 'Wedge product', aliases: ['exterior product'] },
  { id: N.kForms, title: 'k-forms', aliases: ['differential k-forms', 'alternating tensors'] },
  { id: N.extDerivative, title: 'Exterior derivative', aliases: ['d', 'the d operator'] },
  { id: N.pullback, title: 'Pullback', aliases: ['f^*'] },
  { id: N.genStokes, title: 'Generalized Stokes theorem', aliases: ['Stokes for forms'] },
  {
    id: N.classicalStokes,
    title: 'Classical Stokes and divergence',
    aliases: ['vector calculus theorems'],
  },
];

export const EDGES: GraphEdge[] = [
  { from: N.vectorSpaces, to: N.basis, type: 'strict_prerequisite', provenance: 'model' },
  { from: N.vectorSpaces, to: N.linearMaps, type: 'strict_prerequisite', provenance: 'model' },
  { from: N.linearMaps, to: N.dualSpace, type: 'strict_prerequisite', provenance: 'model' },
  { from: N.dualSpace, to: N.covectors, type: 'strict_prerequisite', provenance: 'model' },
  { from: N.basis, to: N.covectors, type: 'recommended_background', provenance: 'model' },
  { from: N.rowVectors, to: N.covectors, type: 'misconception_for', provenance: 'model' },
  { from: N.covectors, to: N.wedge, type: 'strict_prerequisite', provenance: 'course' },
  { from: N.wedge, to: N.kForms, type: 'strict_prerequisite', provenance: 'course' },
  { from: N.kForms, to: N.extDerivative, type: 'strict_prerequisite', provenance: 'reference' },
  { from: N.extDerivative, to: N.pullback, type: 'co_requisite', provenance: 'model' },
  { from: N.extDerivative, to: N.genStokes, type: 'strict_prerequisite', provenance: 'reference' },
  { from: N.lineIntegrals, to: N.covectors, type: 'supports', provenance: 'model' },
  { from: N.classicalStokes, to: N.genStokes, type: 'transfer_related', provenance: 'model' },
  { from: N.lineIntegrals, to: N.wedge, type: 'course_sequence', provenance: 'course' },
];

const st = (
  state: NodeState['state'],
  over: Partial<NodeState> = {},
): NodeState => ({
  state,
  independent_passes: 0,
  assisted_passes: 0,
  self_graded_passes: 0,
  fails: 0,
  last_delayed: null,
  transfer_passes: 0,
  uncertainty: 'high',
  review_priority: 0,
  ...over,
});

/** All four colours are present on purpose - this is the map the learner is shown. */
export const SEED_STATES: Record<string, NodeState> = {
  [N.vectorSpaces]: st('known', {
    independent_passes: 3,
    transfer_passes: 1,
    last_delayed: '2026-08-27',
    uncertainty: 'low',
  }),
  [N.basis]: st('known', {
    independent_passes: 2,
    last_delayed: '2026-08-29',
    uncertainty: 'low',
  }),
  [N.linearMaps]: st('known', {
    independent_passes: 3,
    transfer_passes: 1,
    last_delayed: '2026-08-30',
    uncertainty: 'low',
  }),
  [N.lineIntegrals]: st('known', {
    independent_passes: 2,
    transfer_passes: 1,
    last_delayed: '2026-09-01',
    uncertainty: 'low',
  }),
  [N.classicalStokes]: st('known', {
    independent_passes: 2,
    last_delayed: '2026-08-31',
    uncertainty: 'medium',
  }),
  [N.dualSpace]: st('fragile', {
    independent_passes: 1,
    assisted_passes: 1,
    fails: 1,
    uncertainty: 'medium',
    review_priority: 3,
  }),
  [N.covectors]: st('fragile', {
    independent_passes: 1,
    assisted_passes: 2,
    fails: 1,
    last_delayed: '2026-09-02',
    uncertainty: 'high',
    review_priority: 5,
  }),
  [N.rowVectors]: st('misconception', {
    fails: 2,
    assisted_passes: 1,
    uncertainty: 'medium',
    review_priority: 6,
  }),
  [N.wedge]: st('unknown'),
  [N.kForms]: st('unknown'),
  [N.extDerivative]: st('unknown'),
  [N.pullback]: st('unknown'),
  [N.genStokes]: st('unknown'),
};

export const SEED_MISCONCEPTIONS: Misconception[] = [
  {
    misconception_id: 'm_rowvec_01',
    node_id: N.rowVectors,
    claim: 'a covector is just a vector written sideways, so applying it returns a vector',
    state: 'active',
    steps_held: ['reasoning', 'prediction'],
    opened_at: '2026-09-01',
  },
];

export const SEED_DISPUTES: Dispute[] = [
  {
    dispute_id: 'd_pullback_01',
    node_id: N.pullback,
    type: 'not on my exam',
    note: 'The midterm covers up to the exterior derivative; pullbacks are in the second half.',
    outcome: null,
    opened_at: '2026-09-03',
  },
];

/* ------------------------------------------------------------------ items */

export interface MockItem extends Question {
  answer: string;
  distractor_misconceptions: Record<string, string>;
  feedback: Record<string, string>;
  hints: Record<number, string>;
}

export const ITEMS: MockItem[] = [
  {
    item_id: 'i_covector_eval',
    item_version_id: 'iv_covector_eval_2',
    node_id: N.covectors,
    node_title: 'Covectors',
    kind: 'apply',
    assistance_level: 0,
    allow_idk: true,
    ask_confidence: true,
    stem:
      'Let $\\alpha$ be a covector on $\\mathbb{R}^3$ given by $\\alpha(x,y,z) = 2x - z$, and let $v = (1,5,4)$. What is $\\alpha(v)$, and why?',
    options: [
      {
        key: 'A',
        text: '$(2, 0, -1)$ - a covector applied to a vector returns a vector in the same space',
      },
      { key: 'B', text: '$-2$ - evaluate the linear functional at $v$: $2(1) - 4$' },
      { key: 'C', text: '$6$ - add the components of $v$ that $\\alpha$ mentions: $1 + 5$' },
      { key: 'D', text: '$2$ - a covector reads off the coefficient of its first term' },
    ],
    answer: 'B',
    distractor_misconceptions: {
      A: 'a covector is just a vector written sideways, so applying it returns a vector rather than a scalar',
      C: 'a covector sums the vector components rather than evaluating a linear map',
      D: 'a covector reads off a single coordinate and ignores its own coefficients',
    },
    feedback: {
      A: 'Not quite. A covector is a **map** $V \\to \\mathbb{R}$: feeding it a vector has to give you a number, not another arrow. The type signature is the whole point of the object.',
      B: 'Correct. $\\alpha(v) = 2(1) - (4) = -2$, and the output is a scalar because $\\alpha$ is a linear functional $\\mathbb{R}^3 \\to \\mathbb{R}$.',
      C: 'Not quite - that adds coordinates instead of applying the map. The coefficients $2$ and $-1$ in $\\alpha$ are doing work, and $y$ is not mentioned at all.',
      D: 'Not quite. That reads off one coordinate and drops the coefficients. Apply the whole linear expression.',
      IDK: 'Fine - that is worth more than a guess. Apply the definition directly: $\\alpha(x,y,z) = 2x - z$ is a recipe you evaluate at the components of $v$.',
    },
    hints: {
      1: 'You have everything you need for this one. Take a shot even if you are not sure - a wrong attempt tells us both more than a skip.',
      2: 'Hint (conceptual): this is a question about what kind of **object** a covector is. Which part of the definition of a linear functional is doing the work here?',
      3: 'Hint (strategic): the approach here is to apply the definition directly rather than to reason about shapes. What does $\\alpha$ do to an arbitrary $(x,y,z)$?',
      4: 'Hint (procedural): start by substituting the components of $v$ into $\\alpha(x,y,z) = 2x - z$. Do that step and tell me what you get.',
      5: 'Most of the way: $\\alpha(v) = 2(1) - (4)$. The last piece is the one that matters - is what you just wrote a number or a triple, and what does that tell you about the output type?',
      6: 'Here is the whole thing: $\\alpha$ is the linear map $(x,y,z) \\mapsto 2x - z$. Evaluating at $v=(1,5,4)$ gives $2(1) - 4 = -2$, a scalar. The answer is **B**. This one is recorded as not passed and we will come back to it - a reveal is not a pass, and that is on purpose, so the map stays honest.',
    },
  },
  {
    item_id: 'i_dual_dim',
    item_version_id: 'iv_dual_dim_1',
    node_id: N.dualSpace,
    node_title: 'Dual space',
    kind: 'explain',
    assistance_level: 0,
    allow_idk: true,
    ask_confidence: true,
    stem:
      'For a finite-dimensional real vector space $V$, what is $\\dim V^{*}$, and what makes the isomorphism $V \\cong V^{*}$ awkward?',
    options: [
      { key: 'A', text: '$\\dim V^{*} = \\dim V$, and the isomorphism depends on a choice of basis' },
      { key: 'B', text: '$\\dim V^{*} = \\dim V - 1$, because functionals lose one degree of freedom' },
      { key: 'C', text: '$\\dim V^{*}$ is infinite for every $V$' },
      { key: 'D', text: '$\\dim V^{*} = \\dim V$, and the isomorphism is canonical' },
    ],
    answer: 'A',
    distractor_misconceptions: {
      B: 'thinks a linear functional consumes a dimension',
      D: 'believes the identification of a space with its dual is basis-free',
    },
    feedback: {
      A: 'Correct. Same dimension, but you have to pick a basis to write the isomorphism down - which is exactly why the row-vector picture misleads.',
      B: 'Not quite. Every basis of $V$ gives a dual basis of the same size.',
      C: 'Not for finite-dimensional $V$.',
      D: 'Half right: the dimensions match, but the isomorphism is not canonical. $V \\cong V^{**}$ is the canonical one.',
      IDK: 'Reasonable. Count the dual basis: one functional per basis vector.',
    },
    hints: {
      1: 'You have everything you need for this one. Take a shot even if you are not sure.',
      2: 'Hint (conceptual): this is a question about counting a basis for the space of functionals.',
      3: 'Hint (strategic): construct the dual basis and count it.',
      4: 'Hint (procedural): start by writing the functional $e^i$ that sends $e_j$ to $\\delta^i_j$. How many of those are there?',
      5: 'Most of the way: there is exactly one dual basis covector per basis vector, so the dimensions agree. The last piece is whether writing that map down needed a basis.',
      6: 'Here is the whole thing: the dual basis has one element per basis vector, so $\\dim V^{*} = \\dim V$; but the map $e_i \\mapsto e^i$ depends on the basis you chose, so it is not canonical. The answer is **A**. Recorded as not passed.',
    },
  },
  {
    item_id: 'i_wedge_antisym',
    item_version_id: 'iv_wedge_antisym_1',
    node_id: N.wedge,
    node_title: 'Wedge product',
    kind: 'apply',
    assistance_level: 0,
    allow_idk: true,
    ask_confidence: false,
    stem:
      'For 1-forms $\\alpha$ and $\\beta$, what is $\\alpha \\wedge \\alpha$, and what property forces it?',
    options: [
      { key: 'A', text: '$\\alpha \\wedge \\alpha = \\alpha^2$, by the usual product rule' },
      { key: 'B', text: '$\\alpha \\wedge \\alpha = 0$, forced by antisymmetry on 1-forms' },
      { key: 'C', text: '$\\alpha \\wedge \\alpha = 2\\alpha$, by linearity' },
      { key: 'D', text: 'It is undefined - the wedge needs two different forms' },
    ],
    answer: 'B',
    distractor_misconceptions: {
      A: 'treats the wedge as a commutative product',
      C: 'confuses the wedge with addition',
    },
    feedback: {
      A: 'Not quite - the wedge is not commutative on 1-forms; it is anticommutative.',
      B: 'Correct. $\\alpha \\wedge \\beta = -\\beta \\wedge \\alpha$, so setting $\\beta = \\alpha$ forces $\\alpha \\wedge \\alpha = 0$.',
      C: 'Not quite - that is addition, not the wedge.',
      D: 'It is perfectly well defined; it just happens to vanish.',
      IDK: 'Try the anticommutativity rule with $\\beta = \\alpha$ and see what it forces.',
    },
    hints: {
      1: 'You have everything you need. Take a shot.',
      2: 'Hint (conceptual): this is a question about the sign rule for swapping two 1-forms.',
      3: 'Hint (strategic): write the anticommutativity law, then specialise it.',
      4: 'Hint (procedural): start from $\\alpha \\wedge \\beta = -\\beta \\wedge \\alpha$ and set $\\beta = \\alpha$.',
      5: 'Most of the way: you get $x = -x$ over the reals. The last piece is what that forces $x$ to be.',
      6: 'Here is the whole thing: anticommutativity gives $\\alpha\\wedge\\alpha = -\\alpha\\wedge\\alpha$, so $2(\\alpha\\wedge\\alpha)=0$ and the product vanishes. The answer is **B**. Recorded as not passed.',
    },
  },
];

/* ------------------------------------------------------------------ teach steps */

export interface MockStep {
  node_id: string;
  strategy: string;
  markdown: string;
  mermaid?: string;
  citations: string[];
  item_id: string;
}

export const STEPS: MockStep[] = [
  {
    node_id: N.covectors,
    item_id: 'i_covector_eval',
    strategy: 'formal-first, then one concrete evaluation',
    citations: ['Spivak, Calculus on Manifolds, p. 75', 'course-slides-week-4.pdf p. 12'],
    markdown: [
      '### A covector is a machine that eats a vector and returns a number',
      '',
      'Formally: a **covector** on a real vector space $V$ is a linear map',
      '',
      '$$\\alpha : V \\to \\mathbb{R}, \\qquad \\alpha(av + bw) = a\\,\\alpha(v) + b\\,\\alpha(w).$$',
      '',
      'The set of all of them is the dual space $V^{*}$, and it is itself a vector space.',
      '',
      'Two things to hold on to, because the next node depends on both:',
      '',
      '1. The **output type is a scalar**. Not a vector, not a triple. If your answer to',
      '   "what is $\\alpha(v)$" has components in it, something has gone wrong at the level of',
      '   types, before any arithmetic.',
      '2. $\\dim V^{*} = \\dim V$ for finite-dimensional $V$, via the dual basis',
      '   $e^{i}(e_j) = \\delta^{i}_{j}$ - but writing that isomorphism down **required a choice',
      '   of basis**. That is why "a covector is a row vector" is a picture, not a definition.',
      '',
      'Concretely, on $\\mathbb{R}^3$ with $\\alpha(x,y,z) = 3x + y$:',
      '',
      '$$\\alpha(1, 2, 99) = 3(1) + 2 = 5.$$',
      '',
      'The $99$ never mattered. $\\alpha$ simply does not look at $z$.',
    ].join('\n'),
    mermaid: [
      'flowchart LR',
      '  V["v in V"] --> A["alpha in V*"]',
      '  A --> R["alpha(v) in R"]',
      '  B["basis choice"] -.-> A',
    ].join('\n'),
  },
  {
    node_id: N.wedge,
    item_id: 'i_wedge_antisym',
    strategy: 'guided discovery from the sign rule',
    citations: ['Spivak, Calculus on Manifolds, p. 79'],
    markdown: [
      '### The wedge is the product that remembers orientation',
      '',
      'The one law that generates everything else on 1-forms:',
      '',
      '$$\\alpha \\wedge \\beta = -\\,\\beta \\wedge \\alpha.$$',
      '',
      'Everything surprising about $\\wedge$ is a consequence of that sign. Before reading on,',
      'work out what it forces when $\\beta = \\alpha$ - that is the checkpoint below.',
      '',
      'Geometrically, $\\alpha \\wedge \\beta$ measures **signed area** of the parallelogram the',
      'two forms pick out. Two copies of the same form span no area, which is the same fact',
      'told a second way.',
    ].join('\n'),
  },
];

/* ------------------------------------------------------------------ seed events */

const ev = (over: Partial<LearnerEvent> & Pick<LearnerEvent, 'event_id' | 'node_id' | 'ts'>): LearnerEvent => ({
  session_id: 's_seed_01',
  goal_id: 'g_forms',
  kind: 'answer',
  response: null,
  correct: null,
  confidence: null,
  idk: 0,
  assistance_level: 0,
  channel: 'claude-code',
  context: 'in-session',
  prompt_version: 'teach/v1',
  grader_version: 'mc-key-v1',
  evaluation_method: 'blind_solver',
  ...over,
});

export const SEED_EVENTS: LearnerEvent[] = [
  ev({
    event_id: 'e_seed_001',
    node_id: N.covectors,
    ts: '2026-08-28T18:20:00Z',
    item_version_id: 'iv_covector_eval_1',
    response: 'A',
    correct: 0,
    confidence: 4,
    assistance_level: 0,
  }),
  ev({
    event_id: 'e_seed_002',
    node_id: N.covectors,
    ts: '2026-08-28T18:31:00Z',
    item_version_id: 'iv_covector_eval_1',
    response: 'B',
    correct: 1,
    confidence: 3,
    assistance_level: 3,
  }),
  ev({
    event_id: 'e_seed_003',
    node_id: N.covectors,
    ts: '2026-08-30T09:02:00Z',
    item_version_id: 'iv_covector_eval_2',
    response: 'B',
    correct: 1,
    confidence: 4,
    assistance_level: 0,
    context: 'delayed',
    channel: 'telegram',
  }),
  ev({
    event_id: 'e_seed_004',
    node_id: N.covectors,
    ts: '2026-09-02T08:40:00Z',
    item_version_id: 'iv_covector_eval_2',
    response: 'C',
    correct: 0,
    confidence: 2,
    assistance_level: 0,
    context: 'delayed',
    channel: 'telegram',
  }),
  ev({
    event_id: 'e_seed_005',
    node_id: N.covectors,
    ts: '2026-09-02T09:10:00Z',
    kind: 'teach_back',
    correct: null,
    assistance_level: 1,
    grader_version: 'teach-back-v1',
    evaluation_method: 'rubric',
    payload: { score: 2 },
  }),
  ev({
    event_id: 'e_seed_006',
    node_id: N.rowVectors,
    ts: '2026-09-01T17:05:00Z',
    response: 'A',
    correct: 0,
    confidence: 5,
    assistance_level: 0,
  }),
  ev({
    event_id: 'e_seed_007',
    node_id: N.rowVectors,
    ts: '2026-09-01T17:22:00Z',
    kind: 'misconception_step',
    correct: null,
    evaluation_method: 'human',
    payload: { step: 'reasoning', outcome: 'held' },
  }),
  ev({
    event_id: 'e_seed_008',
    node_id: N.dualSpace,
    ts: '2026-08-29T19:00:00Z',
    item_version_id: 'iv_dual_dim_1',
    response: 'D',
    correct: 0,
    confidence: 3,
    assistance_level: 0,
  }),
  ev({
    event_id: 'e_seed_009',
    node_id: N.dualSpace,
    ts: '2026-08-29T19:12:00Z',
    item_version_id: 'iv_dual_dim_1',
    response: 'A',
    correct: 1,
    confidence: 4,
    assistance_level: 4,
  }),
  ev({
    event_id: 'e_seed_010',
    node_id: N.lineIntegrals,
    ts: '2026-09-01T18:00:00Z',
    response: 'B',
    correct: 1,
    confidence: 5,
    assistance_level: 0,
    context: 'transfer',
  }),
];

/* ------------------------------------------------------------------ seed goal */

export const SEED_GOAL: Goal = {
  goal_id: 'g_forms',
  title: 'Differential forms for Maxwell',
  concept: 'Differential forms and the generalized Stokes theorem',
  depth: 'apply',
  purpose: 'To read the covariant formulation of Maxwell equations without hand-waving.',
  deadline: '2026-10-01',
  minutes_per_session: 45,
  sessions_per_week: 3,
  assessment: 'Written midterm, problems in the style of the course problem sets.',
  source_priority: 'alignment',
  target_capabilities: ['explain', 'apply'],
  transfer_required: true,
  domain: 'math-cs',
  created_at: '2026-08-26',
};

export const SEED_SUMMARY_MD = [
  '# Learner - 2026-09-05',
  '',
  'Goal: differential forms - apply - deadline 2026-10-01 - 3 sessions left of ~6 needed !',
  '',
  '- **Known:** line integrals, classical Stokes, vector spaces, linear maps, basis and',
  '  coordinates - independent, delayed passes',
  '- **Fragile:** covectors - 1 independent pass, 2 assisted, 1 fail; last delayed retrieval',
  '  failed at 4 days; no transfer evidence; uncertainty high; review due 09-05',
  '- **Fragile:** dual space - 1 independent pass, 1 assisted, 1 fail',
  '- **Unknown:** wedge product, k-forms, exterior derivative, pullback, generalized Stokes',
  '- **Misconception (active):** "a covector is just a vector written sideways" - confirmed',
  '  09-01 by reasoning + reworded prediction; counterexample step not yet run',
  '- **Due today:** 2 items',
  '- **Open dispute:** pullback - "not on my exam" (unsettled)',
  '- Prefs: formal-first over analogy - short steps - no sports analogies',
  '- Last session: 09-02, 40 min, stopped at covectors, mean assistance 1.4, no frustration',
  '',
  '+41 known in linear algebra, vector calculus',
].join('\n');

export const RESEARCH_PROPOSAL = [
  {
    title: 'Spivak, Calculus on Manifolds (ch. 4)',
    url: 'https://example.org/spivak-ch4',
    role: 'authority' as const,
    why: 'Canonical treatment of forms and the generalized Stokes theorem; matches your notation for the wedge.',
  },
  {
    title: 'MIT 18.952 lecture notes, weeks 3-5',
    url: 'https://example.org/mit-18952-notes',
    role: 'authority' as const,
    why: 'Worked examples at exactly the "apply" depth your goal contract asks for.',
  },
  {
    title: 'Bachman, A Geometric Approach to Differential Forms',
    url: 'https://example.org/bachman-forms',
    role: 'authority' as const,
    why: 'Intuition-first alternative; useful only if the formal-first pass stalls.',
  },
  {
    title: 'Physics SE: "why is dF = 0 the same as two Maxwell equations?"',
    url: 'https://example.org/physics-se-maxwell-forms',
    role: 'learner' as const,
    why: 'Community answer, not authoritative. Proposed as motivation only - flagged so it is never cited as truth.',
  },
];

/* ------------------------------------------------------------------ study tools
 * A small bank, deck and two tables for /goal/study. Every goal gets its own copy the first
 * time it is asked for (see studyFor() in ./gateway.ts). Keys live only here and in the
 * fake gateway: the UI never sees one before it answers, exactly as with the real thing. */

export interface MockPractice {
  item_id: string;
  item_version_id: string;
  node_id: string;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  answer: string;
  explanation: string | null;
  /** Blind-check outcome. `rejected` = a solver disagreed with the key. */
  check: 'checked' | 'unchecked' | 'rejected';
  solver_answer?: string | null;
  ambiguous?: boolean;
  notes?: string | null;
  /** Hours from page load; negative = already due. Absent = never answered (new). */
  due_in_hours?: number;
  /** Hours since it was last answered; >= LT_DELAYED_MIN_HOURS makes the next one delayed. */
  answered_hours_ago?: number;
  /** Dedupe key for the fake importer (the real one keeps `items.source_key`). */
  source_key?: string;
  /** `mock` = sealed: never served by practice until it has been answered in a mock. */
  pool?: 'practice' | 'mock';
  /** The blueprint row of the item's concept ("3.2"); absent on goals with no blueprint. */
  ref?: string;
  /** A sealed item already answered in a mock: it has joined practice rotation. */
  mocked?: boolean;
}

export const PRACTICE: MockPractice[] = [
  {
    item_id: 'pq_covector_es',
    item_version_id: 'pqv_covector_es_1',
    node_id: N.covectors,
    node_title: 'Covectors',
    stem: 'Sea $\\alpha(x,y) = 3x - y$ un covector en $\\mathbb{R}^2$ y $v = (2, 1)$. ¿Cuánto vale $\\alpha(v)$?',
    options: [
      { key: 'A', text: '$(6, -1)$' },
      { key: 'B', text: '$5$' },
      { key: 'C', text: '$7$' },
      { key: 'D', text: '$3$' },
    ],
    answer: 'B',
    explanation:
      '$\\alpha(v) = 3(2) - 1 = 5$. Un covector devuelve un **escalar**, no un vector: la opción A confunde el covector con sus coeficientes.',
    check: 'checked',
    due_in_hours: -6,
    answered_hours_ago: 70,
  },
  {
    item_id: 'pq_order_ext',
    item_version_id: 'pqv_order_ext_1',
    node_id: N.extDerivative,
    node_title: 'Exterior derivative',
    stem: [
      'Put these steps for computing $d\\omega$, with $\\omega = f\\,dx$ on $\\mathbb{R}^2$, in order:',
      '',
      '1. Simplify with $dx \\wedge dx = 0$.',
      '2. Write $d\\omega = df \\wedge dx$.',
      '3. Expand $df = f_x\\,dx + f_y\\,dy$.',
      '',
      'Which order is right?',
    ].join('\n'),
    options: [
      { key: 'A', text: '1, 2, 3' },
      { key: 'B', text: '2, 3, 1' },
      { key: 'C', text: '3, 1, 2' },
      { key: 'D', text: '2, 1, 3' },
    ],
    answer: 'B',
    explanation:
      'Start from the definition $d(f\\,dx) = df \\wedge dx$ (step 2), expand $df$ (step 3), then $f_x\\,dx \\wedge dx$ vanishes (step 1), leaving $f_y\\,dy \\wedge dx = -f_y\\,dx \\wedge dy$.',
    check: 'checked',
  },
  {
    item_id: 'pq_wedge_degree_es',
    item_version_id: 'pqv_wedge_degree_es_1',
    node_id: N.wedge,
    node_title: 'Wedge product',
    stem: 'En $\\mathbb{R}^3$, ¿cuál es el grado de $dx \\wedge dy \\wedge dz$?',
    options: [
      { key: 'A', text: '1' },
      { key: 'B', text: '2' },
      { key: 'C', text: '3' },
      { key: 'D', text: '0' },
      { key: 'E', text: 'Depende de la base' },
    ],
    answer: 'C',
    explanation: 'Tres 1-formas unidas con $\\wedge$ dan una 3-forma: los grados se suman.',
    check: 'unchecked',
  },
  {
    item_id: 'pq_dual_dim_en',
    item_version_id: 'pqv_dual_dim_en_1',
    node_id: N.dualSpace,
    node_title: 'Dual space',
    stem: 'For $V = \\mathbb{R}^4$, what is $\\dim V^{*}$?',
    options: [
      { key: 'A', text: '3' },
      { key: 'B', text: '4' },
      { key: 'C', text: '16' },
      { key: 'D', text: 'infinite' },
    ],
    answer: 'B',
    explanation:
      'One dual basis covector $e^i$ per basis vector $e_i$, so $\\dim V^{*} = \\dim V = 4$.',
    check: 'checked',
  },
  {
    item_id: 'pq_stokes_case',
    item_version_id: 'pqv_stokes_case_1',
    node_id: N.genStokes,
    node_title: 'Generalized Stokes theorem',
    stem: 'Which classical theorem is the $k = 1$ case of the generalized Stokes theorem?',
    options: [
      { key: 'A', text: "Green's theorem" },
      { key: 'B', text: 'The classical Stokes theorem' },
      { key: 'C', text: 'The divergence theorem' },
      { key: 'D', text: 'The fundamental theorem of line integrals' },
    ],
    answer: 'B',
    explanation:
      'For a 1-form on a surface in $\\mathbb{R}^3$, $\\int_S d\\omega = \\int_{\\partial S} \\omega$ is the classical Stokes theorem.',
    check: 'rejected',
    solver_answer: 'A',
    ambiguous: true,
    notes:
      "The solver picked Green's theorem, which is also a k = 1 case (in the plane). The stem never says the surface is in R^3, so two options are defensible.",
  },
  {
    item_id: 'pq_pullback_sign',
    item_version_id: 'pqv_pullback_sign_1',
    node_id: N.pullback,
    node_title: 'Pullback',
    stem: 'Con $f(t) = (t, t^2)$ y $\\omega = y\\,dx$, ¿qué vale $f^{*}\\omega$?',
    options: [
      { key: 'A', text: '$t\\,dt$' },
      { key: 'B', text: '$2t^2\\,dt$' },
      { key: 'C', text: '$t^2\\,dt$' },
      { key: 'D', text: '$2t\\,dt$' },
    ],
    answer: 'B',
    explanation: '$f^{*}(y\\,dx) = t^2\\,d(t) = t^2\\,dt$.',
    check: 'rejected',
    solver_answer: 'C',
    ambiguous: false,
    notes: 'The solver worked it out as $t^2\\,dt$, which contradicts the key. The key looks wrong.',
  },
];

export interface MockCard {
  item_id: string;
  node_id: string;
  node_title: string;
  front: string;
  back: string;
  source: string | null;
  /** Hours from page load; negative = due. Absent = new. */
  due_in_hours?: number;
}

export const CARDS: MockCard[] = [
  {
    item_id: 'c_covector',
    node_id: N.covectors,
    node_title: 'Covectors',
    front: 'What is a **covector** on $V$?',
    back: 'A linear map $V \\to \\mathbb{R}$. Feeding it a vector gives a **number**, never another vector.',
    source: 'notas/formas.md',
    due_in_hours: -20,
  },
  {
    item_id: 'c_dual_basis',
    node_id: N.dualSpace,
    node_title: 'Dual space',
    front: 'The dual basis $e^i$ of a basis $e_j$',
    back: '$e^i(e_j) = \\delta^i_j$ - one covector per basis vector, so $\\dim V^{*} = \\dim V$.',
    source: 'notas/formas.md',
    due_in_hours: -2,
  },
  {
    item_id: 'c_wedge_es',
    node_id: N.wedge,
    node_title: 'Wedge product',
    front: 'Producto cuña: ¿qué vale $\\alpha \\wedge \\alpha$ para una 1-forma $\\alpha$?',
    back: '$0$, por antisimetría: $\\alpha \\wedge \\beta = -\\beta \\wedge \\alpha$.',
    source: 'materials/bank.md',
  },
  {
    item_id: 'c_ext_derivative',
    node_id: N.extDerivative,
    node_title: 'Exterior derivative',
    front: 'Exterior derivative: what is $d \\circ d$?',
    back: [
      '$d(d\\omega) = 0$ for every form $\\omega$ - mixed partials commute.',
      '',
      '- on 0-forms in $\\mathbb{R}^3$, $d$ is the gradient',
      '- on 1-forms it is the curl, on 2-forms the divergence',
    ].join('\n'),
    source: 'notas/formas.md',
  },
  {
    item_id: 'c_pullback',
    node_id: N.pullback,
    node_title: 'Pullback',
    front: '**Pullback** $f^{*}\\omega$',
    back: 'Precompose with $f$: $(f^{*}\\omega)_p(v) = \\omega_{f(p)}(df_p\\,v)$.',
    source: null,
  },
];

export interface MockTable {
  table_id: string;
  title: string;
  node_id: string | null;
  node_title: string | null;
  columns: string[];
  rows: string[][];
  source: string | null;
  author: string;
  created_at: string;
}

export const TABLES: MockTable[] = [
  {
    table_id: 't_stokes_family',
    title: 'The Stokes family',
    node_id: N.genStokes,
    node_title: 'Generalized Stokes theorem',
    columns: ['Classical theorem', 'Form degree k', 'Integrates over'],
    rows: [
      ['Fundamental theorem of line integrals', '0', 'a curve (its two endpoints)'],
      ["Green's theorem", '1', 'a region of the plane'],
      ['Classical Stokes', '1', 'a surface in space'],
      ['Divergence theorem', '2', 'a solid region'],
    ],
    source: 'notas/formas.md',
    author: 'import',
    created_at: '2026-09-20T10:00:00Z',
  },
  {
    table_id: 't_glosario',
    title: 'Glosario de formas',
    node_id: null,
    node_title: null,
    columns: ['Término', 'Definición'],
    rows: [
      ['Covector', 'Función lineal de V en los reales'],
      ['Espacio dual', 'El espacio V* de todos los covectores'],
      ['Producto cuña', 'Producto antisimétrico de formas'],
      ['k-forma', 'Campo de funciones multilineales alternantes de grado k'],
      ['Pullback', 'Composición de una forma con la diferencial de un mapa'],
    ],
    source: 'materials/bank.md',
    author: 'claude-code',
    created_at: '2026-09-22T18:30:00Z',
  },
];

/** Markdown already sitting in the goal's sources dir, in the importer's format. */
export const IMPORTABLE: Record<string, string> = {
  'materials/bank.md': [
    '# Formas diferenciales - banco de práctica',
    '',
    '## F1 Covectores',
    '',
    '**Covector.** Una función lineal $V \\to \\mathbb{R}$; devuelve un escalar.',
    '',
    '1. Sea $\\alpha(x,y) = x + 2y$. ¿Cuánto vale $\\alpha(1, 1)$? [F1]',
    'A) $(1, 2)$',
    'B) $3$',
    'C) $2$',
    'D) $1$',
    '',
    '## F2 Producto cuña',
    '',
    '**Producto cuña.** Producto antisimétrico de formas:',
    '- $\\alpha \\wedge \\beta = -\\beta \\wedge \\alpha$',
    '- $\\alpha \\wedge \\alpha = 0$ para 1-formas',
    '',
    '2. ¿Qué vale $dx \\wedge dx$? [F2]',
    'A) $2\\,dx$',
    'B) $dx^2$',
    'C) $0$',
    'D) No está definido',
    '',
    '3. Pregunta sin opciones: el importador la reporta y no la importa. [F2]',
    '',
    '## F3 Grados',
    '',
    '| Forma | Grado |',
    '|---|---|',
    '| $f$ | 0 |',
    '| $dx$ | 1 |',
    '| $dx \\wedge dy$ | 2 |',
    '',
  ].join('\n'),
  'materials/key.md': [
    '# Clave',
    '',
    '| N | tag | LETTER | explanation |',
    '|---|---|---|---|',
    '| 1 | F1 | B | $1 + 2(1) = 3$, un escalar. |',
    '| 2 | F2 | C | Antisimetría: $dx \\wedge dx = -dx \\wedge dx$, así que vale 0. |',
    '',
  ].join('\n'),
};
