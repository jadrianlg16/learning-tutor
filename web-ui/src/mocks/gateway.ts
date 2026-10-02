/**
 * In-browser fake gateway (`NEXT_PUBLIC_MOCK=1`, i.e. `npm run dev:mock`).
 *
 * Patches `window.fetch` and answers every route in CONTRACTS.md "Gateway response shapes"
 * from the fixtures in ./fixtures.ts. It is a *state machine*, not a stub: creating a goal
 * really does move it through grounding -> plan -> probe -> teach -> done, answers really do
 * move node state, and the map and receipts read back what you just did. That is what makes
 * the UI screenshot-verifiable with no backend.
 *
 * It is loaded by a dynamic import from src/lib/api.ts, so it is a separate chunk and never
 * reaches a production build.
 */

import { GATEWAY_BASE } from '@/lib/api';
import type {
  CardRating,
  Dispute,
  Goal,
  GoalContract,
  GoalListRow,
  ImportKind,
  ImportReport,
  LearnerEvent,
  Misconception,
  NodeState,
  Phase,
  Question,
  QuestionOption,
  Session,
  Source,
  TableSummary,
  VerificationRow,
} from '@/lib/types';
import { CARD_RATINGS } from '@/lib/types';
import {
  EDGES,
  ITEMS,
  N,
  NODES,
  RESEARCH_PROPOSAL,
  SEED_DISPUTES,
  SEED_EVENTS,
  SEED_GOAL,
  SEED_MISCONCEPTIONS,
  SEED_STATES,
  SEED_SUMMARY_MD,
  STEPS,
  type MockItem,
  type MockPractice,
  type MockTable,
} from './fixtures';
import {
  CARDS_NEW_PER_DAY,
  FakeError,
  HOUR,
  bankCounts,
  cardCounts,
  getMock,
  listMocks,
  logCardReview,
  mockFlags,
  persistStudy,
  practiceAnswer,
  practiceNext,
  progress,
  servable,
  startMock,
  stateFromAttempts,
  studyFor,
  submitMock,
  type StudyRecord,
} from './exam';
import { ISOFT_GOAL } from './isoft';

/* ------------------------------------------------------------------ store */

interface GoalRecord {
  goal: Goal;
  phase: Phase;
  sources: Source[];
  proposalId: string | null;
  approvedSources: number;
  graphVersion: string | null;
  sessions: Session[];
}

interface SessionRecord {
  session: Session;
  probeAsked: number;
  probeQueue: string[];
  stepIndex: number;
  attempted: Set<string>;
  hintLevel: Map<string, number>;
  answersThisNode: number;
  teachBackDone: boolean;
}

const store = {
  goals: new Map<string, GoalRecord>(),
  sessions: new Map<string, SessionRecord>(),
  states: { ...structuredCloneSafe(SEED_STATES) } as Record<string, NodeState>,
  events: [...SEED_EVENTS] as LearnerEvent[],
  misconceptions: [...SEED_MISCONCEPTIONS] as Misconception[],
  disputes: [...SEED_DISPUTES] as Dispute[],
  seq: 0,
  idempotency: new Map<string, unknown>(),
};

function structuredCloneSafe<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function nextId(prefix: string): string {
  store.seq += 1;
  return `${prefix}_${store.seq.toString().padStart(3, '0')}`;
}

function now(): string {
  return new Date().toISOString();
}

// The seed goal is already mid-flight, so the goals list is never empty on first load.
store.goals.set(SEED_GOAL.goal_id, {
  goal: SEED_GOAL,
  phase: 'teach',
  sources: [
    {
      source_id: 'src_slides',
      filename: 'course-slides-week-4.pdf',
      role: 'alignment',
      pages: 31,
      ingested_at: '2026-08-26',
    },
    {
      source_id: 'src_spivak',
      filename: 'spivak-calculus-on-manifolds-ch4.pdf',
      role: 'authority',
      pages: 42,
      ingested_at: '2026-08-26',
    },
  ],
  proposalId: null,
  approvedSources: 3,
  graphVersion: 'gv_7',
  sessions: [],
});

// ...with a session already open, so `/goal/?g=g_forms` lands straight on a teach step.
// This is what makes the flow deep-linkable (and the screenshot script reproducible).
const openSession: Session = {
  session_id: 's_seed_open',
  goal_id: SEED_GOAL.goal_id,
  started_at: '2026-09-05T09:00:00Z',
  ended_at: null,
  channel: 'web',
};
store.goals.get(SEED_GOAL.goal_id)!.sessions.push(openSession);
store.sessions.set(openSession.session_id, {
  session: openSession,
  probeAsked: 2,
  probeQueue: [],
  stepIndex: 0,
  attempted: new Set<string>(),
  hintLevel: new Map<string, number>(),
  answersThisNode: 0,
  teachBackDone: false,
});

// A second goal parked at the plan review, so the "one manual review" screen is reachable
// without walking the whole flow.
const PLAN_GOAL: Goal = {
  ...SEED_GOAL,
  goal_id: 'g_stokes',
  title: 'Stokes for the final',
  concept: 'The generalized Stokes theorem and what it subsumes',
  purpose: 'The final has one proof question and it is always this one.',
  deadline: '2026-11-20',
  created_at: '2026-09-05',
};
store.goals.set(PLAN_GOAL.goal_id, {
  goal: PLAN_GOAL,
  phase: 'plan',
  sources: [],
  proposalId: null,
  approvedSources: 0,
  graphVersion: null,
  sessions: [],
});

// A third goal parked at the probe, so the question card is reachable in one URL.
const PROBE_GOAL: Goal = {
  ...SEED_GOAL,
  goal_id: 'g_probe',
  title: 'Vector calculus refresher',
  concept: 'Line integrals, curl, divergence and the classical theorems',
  purpose: 'Everything downstream leans on these and I have not touched them in two years.',
  deadline: '2026-12-01',
  assessment: null,
  created_at: '2026-09-05',
};
store.goals.set(PROBE_GOAL.goal_id, {
  goal: PROBE_GOAL,
  phase: 'probe',
  sources: [],
  proposalId: null,
  approvedSources: 0,
  graphVersion: 'gv_1',
  sessions: [],
});

// A fourth goal with an exam blueprint (EGEL Plus ISOFT-shaped), so the Progress and Mock
// exam tabs of /goal/study/?g=egel-isoft have real structure to draw.
store.goals.set(ISOFT_GOAL.goal_id, {
  goal: ISOFT_GOAL,
  phase: 'teach',
  sources: [],
  proposalId: null,
  approvedSources: 0,
  graphVersion: 'gv_isoft_1',
  sessions: [],
});

/* ------------------------------------------------------------------ helpers */

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function fail(error: string, code: string, status: number): Response {
  return json({ error, code }, status);
}

function asQuestion(item: MockItem, assistance = 0): Question {
  return {
    item_id: item.item_id,
    item_version_id: item.item_version_id,
    node_id: item.node_id,
    node_title: item.node_title,
    stem: item.stem,
    options: item.options,
    allow_idk: item.allow_idk,
    ask_confidence: item.ask_confidence,
    kind: item.kind,
    assistance_level: assistance,
  };
}

function findItem(id: string): MockItem | undefined {
  return ITEMS.find((i) => i.item_id === id);
}

function recordEvent(e: Partial<LearnerEvent> & Pick<LearnerEvent, 'node_id' | 'goal_id' | 'session_id'>): LearnerEvent {
  const full: LearnerEvent = {
    event_id: nextId('e_live'),
    ts: now(),
    kind: 'answer',
    response: null,
    correct: null,
    confidence: null,
    idk: 0,
    assistance_level: 0,
    channel: 'web',
    context: 'in-session',
    prompt_version: 'teach/v1',
    grader_version: 'mc-key-v1',
    evaluation_method: 'blind_solver',
    ...e,
  };
  store.events.push(full);
  return full;
}

/** Rule-based, transparent, and never a probability - the same shape the real model uses. */
function recomputeState(nodeId: string): NodeState {
  const base: NodeState = store.states[nodeId] ?? {
    state: 'unknown',
    independent_passes: 0,
    assisted_passes: 0,
    self_graded_passes: 0,
    fails: 0,
    last_delayed: null,
    transfer_passes: 0,
    uncertainty: 'high',
    review_priority: 0,
  };

  const evs = store.events.filter((e) => e.node_id === nodeId && e.kind === 'answer');
  const independent = evs.filter((e) => e.correct === 1 && e.assistance_level === 0).length;
  const assisted = evs.filter(
    (e) => e.correct === 1 && e.assistance_level > 0 && e.assistance_level < 5,
  ).length;
  const selfGraded = evs.filter(
    (e) => e.correct === 1 && e.evaluation_method === 'host_llm',
  ).length;
  const fails = evs.filter((e) => e.correct === 0).length;
  const transfer = evs.filter((e) => e.correct === 1 && e.context === 'transfer').length;
  const delayed = evs.filter((e) => e.context === 'delayed').slice(-1)[0];

  const active = store.misconceptions.some(
    (m) => m.node_id === nodeId && (m.state === 'active' || m.state === 'recurred'),
  );

  let state: NodeState['state'];
  if (active) state = 'misconception';
  else if (independent >= 2 && fails === 0) state = 'known';
  else if (independent >= 1 || assisted >= 1) state = 'fragile';
  else state = 'unknown';
  // A node the seed already called known stays known unless new failures arrive.
  if (base.state === 'known' && fails <= base.fails) state = 'known';

  const next: NodeState = {
    ...base,
    state,
    independent_passes: Math.max(base.independent_passes, independent),
    assisted_passes: Math.max(base.assisted_passes, assisted),
    self_graded_passes: Math.max(base.self_graded_passes, selfGraded),
    fails: Math.max(base.fails, fails),
    transfer_passes: Math.max(base.transfer_passes, transfer),
    last_delayed: delayed?.ts.slice(0, 10) ?? base.last_delayed,
    uncertainty: independent >= 2 ? 'low' : independent + assisted >= 1 ? 'medium' : 'high',
  };
  store.states[nodeId] = next;
  return next;
}

function mermaidFor(order?: string[]): string {
  const lines = ['flowchart TD'];
  const label = (id: string) => NODES.find((n) => n.id === id)?.title ?? id;
  const short = (id: string) => id.replace(/^n_/, '').replace(/_/g, '');
  const inScope = order ? new Set(order) : null;
  for (const e of EDGES) {
    if (inScope && (!inScope.has(e.from) || !inScope.has(e.to))) continue;
    const arrow = e.type === 'strict_prerequisite' ? '-->' : '-.->';
    lines.push(`  ${short(e.from)}["${label(e.from)}"] ${arrow} ${short(e.to)}["${label(e.to)}"]`);
  }
  for (const colour of ['known', 'fragile', 'unknown', 'misconception']) {
    const ids = NODES.filter(
      (n) => (store.states[n.id]?.state ?? 'unknown') === colour && (!inScope || inScope.has(n.id)),
    ).map((n) => short(n.id));
    if (ids.length) lines.push(`  class ${ids.join(',')} ${colour}`);
  }
  return lines.join('\n');
}

/** Topological-ish order over the not-yet-known nodes: the learner path. */
function learnerPath(): string[] {
  const order = [
    N.dualSpace,
    N.covectors,
    N.rowVectors,
    N.wedge,
    N.kForms,
    N.extDerivative,
    N.genStokes,
  ];
  return order.filter((id) => (store.states[id]?.state ?? 'unknown') !== 'known');
}

function verification(): VerificationRow[] {
  return NODES.map((n, i) => {
    // Two nodes abstain on purpose: cite-or-abstain has to be visible, not hidden.
    const abstain = n.id === N.pullback || n.id === N.rowVectors;
    return {
      node_id: n.id,
      status: abstain ? 'abstain' : 'cited',
      citations: abstain
        ? []
        : [
            i % 2 === 0
              ? 'course-slides-week-4.pdf p. ' + (8 + i)
              : 'Spivak, Calculus on Manifolds, p. ' + (70 + i),
          ],
    };
  });
}

function goalRow(rec: GoalRecord): GoalListRow {
  const counts = { known: 0, fragile: 0, unknown: 0, misconception: 0 };
  for (const n of NODES) counts[store.states[n.id]?.state ?? 'unknown'] += 1;
  return {
    ...rec.goal,
    phase: rec.phase,
    node_count: rec.graphVersion ? NODES.length : 0,
    known: rec.graphVersion ? counts.known : 0,
    fragile: rec.graphVersion ? counts.fragile : 0,
    unknown: rec.graphVersion ? counts.unknown : 0,
    misconception: rec.graphVersion ? counts.misconception : 0,
  };
}

/** A tiny left-to-right renderer, standing in for render-svc's mermaid -> svg. */
function renderMermaid(src: string): string {
  const nodes = new Map<string, string>();
  const links: Array<[string, string]> = [];
  for (const raw of src.split('\n')) {
    const line = raw.trim();
    // Either end may be a bare id when its label was declared on an earlier line.
    const m = line.match(
      /^(\w+)(?:\s*\["?([^\]"]*)"?\])?\s*(?:-{2,3}>|-\.->|==>)\s*(\w+)(?:\s*\["?([^\]"]*)"?\])?/,
    );
    if (m) {
      if (m[2] || !nodes.has(m[1])) nodes.set(m[1], m[2] || nodes.get(m[1]) || m[1]);
      if (m[4] || !nodes.has(m[3])) nodes.set(m[3], m[4] || nodes.get(m[3]) || m[3]);
      links.push([m[1], m[3]]);
      continue;
    }
    const single = line.match(/^(\w+)\s*\[\"?([^\]"]*)\"?\]$/);
    if (single) nodes.set(single[1], single[2]);
  }
  const ids = Array.from(nodes.keys());
  const w = 190;
  const h = 44;
  const gap = 34;
  const width = ids.length * w + (ids.length - 1) * gap + 32;
  const boxes = ids
    .map((id, i) => {
      const x = 16 + i * (w + gap);
      return `<g><rect x="${x}" y="20" rx="8" width="${w}" height="${h}" fill="var(--accent-tint)" stroke="var(--accent)"/><text x="${x + w / 2}" y="42" text-anchor="middle" font-size="12" fill="var(--ink)" font-family="var(--sans)">${escapeXml(
        nodes.get(id) ?? id,
      )}</text></g>`;
    })
    .join('');
  const arrows = links
    .map(([a, b]) => {
      const ia = ids.indexOf(a);
      const ib = ids.indexOf(b);
      if (ia < 0 || ib < 0) return '';
      const x1 = 16 + ia * (w + gap) + w;
      const x2 = 16 + ib * (w + gap);
      return `<line x1="${x1}" y1="42" x2="${x2 - 6}" y2="42" stroke="var(--muted)" stroke-width="1.4" marker-end="url(#mk)"/>`;
    })
    .join('');
  return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${width} 84" width="${width}" height="84"><defs><marker id="mk" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L8,4 L0,8 z" fill="var(--muted)"/></marker></defs>${arrows}${boxes}</svg>`;
}

function escapeXml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

/* ------------------------------------------------------------------ router */

interface Ctx {
  path: string;
  method: string;
  body: Record<string, unknown>;
  form: FormData | null;
  query: URLSearchParams;
}

async function route(ctx: Ctx): Promise<Response> {
  const { path, method, body } = ctx;
  const seg = path.split('/').filter(Boolean); // e.g. ['goals','g_1','teach','next']

  if (path === '/health') {
    return json({
      ok: true,
      services: { learner: 'ok', render: 'ok', llm: 'ok', corpus: 'unconfigured' },
    });
  }

  if (path === '/render' && method === 'POST') {
    const src = String(body.mermaid ?? '');
    if (!src.trim()) return fail('empty diagram', 'bad_request', 400);
    return json({ svg: renderMermaid(src), warnings: ['rendered by the mock, not render-svc'] });
  }

  if (path === '/passport') {
    const payload = {
      generated_at: now(),
      note: 'Mock passport. The real gateway returns application/zip.',
      goals: Array.from(store.goals.values()).map((g) => g.goal),
      events: store.events,
      states: store.states,
      misconceptions: store.misconceptions,
      disputes: store.disputes,
      graph: { nodes: NODES, edges: EDGES },
      'learner.md': SEED_SUMMARY_MD,
    };
    return new Response(JSON.stringify(payload, null, 2), {
      status: 200,
      headers: {
        'Content-Type': 'application/json',
        'Content-Disposition': 'attachment; filename="learner-passport.json"',
      },
    });
  }

  if (seg[0] !== 'goals') return fail(`no route for ${path}`, 'not_found', 404);

  /* ---- /goals ---- */
  if (seg.length === 1) {
    if (method === 'GET') {
      return json({ goals: Array.from(store.goals.values()).map(goalRow) });
    }
    if (method === 'POST') {
      const c = body as unknown as GoalContract;
      if (!c.title || !c.concept) return fail('title and concept are required', 'bad_request', 400);
      const id = c.goal_id || nextId('g');
      const goal: Goal = { ...c, goal_id: id, created_at: now().slice(0, 10) };
      store.goals.set(id, {
        goal,
        phase: 'grounding',
        sources: [],
        proposalId: null,
        approvedSources: 0,
        graphVersion: null,
        sessions: [],
      });
      return json({ goal, sources_dir: `data/sources/${id}/`, phase: 'grounding' });
    }
  }

  const rec = store.goals.get(seg[1]);
  if (!rec) return fail(`goal ${seg[1]} not found`, 'not_found', 404);
  const goalId = rec.goal.goal_id;
  const rest = seg.slice(2).join('/');

  /* ---- /goals/{g} ---- */
  if (!rest && method === 'GET') {
    const open = rec.sessions.find((s) => !s.ended_at) ?? null;
    return json({
      goal: rec.goal,
      phase: rec.phase,
      session: open,
      summary_md: goalId === SEED_GOAL.goal_id ? SEED_SUMMARY_MD : freshSummary(rec),
    });
  }
  if (!rest && method === 'PATCH') return patchGoal(rec, body);

  /* ---- study tools ---- */
  const study = await studyRoute(ctx, rec.goal, seg.slice(2));
  if (study) return study;

  /* ---- grounding ---- */
  if (rest === 'sources' && method === 'GET') {
    return json({
      sources: rec.sources,
      sources_md: rec.sources.length
        ? rec.sources.map((s) => `- ${s.filename} (${s.role})`).join('\n')
        : null,
    });
  }
  if (rest === 'sources' && method === 'POST') {
    const role = (ctx.form?.get('role') as string) || 'alignment';
    const files = (ctx.form?.getAll('file') ?? []) as File[];
    for (const f of files) {
      rec.sources.push({
        source_id: nextId('src'),
        filename: f.name,
        role: role as Source['role'],
        bytes: f.size,
        ingested_at: now().slice(0, 10),
      });
    }
    if (!files.length) return fail('no file in the upload', 'bad_request', 400);
    return json({ sources: rec.sources });
  }
  if (rest === 'research' && method === 'POST') {
    rec.proposalId = nextId('prop');
    return json({ proposal_id: rec.proposalId, sources: RESEARCH_PROPOSAL });
  }
  if (rest === 'research/approve' && method === 'POST') {
    if (body.proposal_id !== rec.proposalId) {
      return fail('unknown or superseded proposal', 'conflict', 409);
    }
    const accept = (body.accept as string[]) ?? [];
    rec.approvedSources += accept.length;
    for (const url of accept) {
      const p = RESEARCH_PROPOSAL.find((s) => s.url === url);
      if (p) {
        rec.sources.push({
          source_id: nextId('src'),
          filename: p.title,
          role: p.role,
          url: p.url,
          ingested_at: now().slice(0, 10),
        });
      }
    }
    return json({ approved: accept.length });
  }

  /* ---- plan ---- */
  if (rest === 'plan' && method === 'POST') {
    rec.phase = 'plan';
    rec.graphVersion = `gv_${store.seq + 1}`;
    return json({
      graph_version: rec.graphVersion,
      mermaid: mermaidFor(),
      nodes: NODES,
      edges: EDGES,
      course_prior_used: rec.sources.some((s) => s.role === 'alignment'),
      verification: verification(),
    });
  }
  if (rest === 'plan/approve' && method === 'POST') {
    const ops = (body.ops as unknown[]) ?? [];
    rec.graphVersion = `gv_${store.seq + 2 + ops.length}`;
    rec.phase = 'probe';
    return json({ graph_version: rec.graphVersion });
  }

  /* ---- probe ---- */
  if (rest === 'probe/start' && method === 'POST') {
    const session: Session = {
      session_id: nextId('s'),
      goal_id: goalId,
      started_at: now(),
      ended_at: null,
      channel: 'web',
    };
    rec.sessions.push(session);
    rec.phase = 'probe';
    store.sessions.set(session.session_id, {
      session,
      probeAsked: 0,
      probeQueue: ['i_dual_dim', 'i_covector_eval', 'i_wedge_antisym'],
      stepIndex: 0,
      attempted: new Set(),
      hintLevel: new Map(),
      answersThisNode: 0,
      teachBackDone: false,
    });
    const first = findItem('i_dual_dim')!;
    return json({
      session_id: session.session_id,
      budget: 6,
      question: asQuestion(first),
      done: false,
      asked: 0,
    });
  }

  if (rest === 'probe/answer' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    const item = findItem(String(body.item_id));
    if (!item) return fail('unknown item', 'not_found', 404);

    const idk = Boolean(body.idk);
    const correct = !idk && body.response === item.answer;
    const event = recordEvent({
      goal_id: goalId,
      session_id: sr.session.session_id,
      node_id: item.node_id,
      item_version_id: item.item_version_id,
      kind: 'probe_answer',
      response: idk ? 'IDK' : String(body.response ?? ''),
      correct: idk ? null : correct ? 1 : 0,
      confidence: (body.confidence as number) ?? null,
      idk: idk ? 1 : 0,
      context: 'probe',
    });
    const nodeState = recomputeState(item.node_id);

    sr.probeAsked += 1;
    sr.probeQueue = sr.probeQueue.filter((i) => i !== item.item_id);

    // The probe stops when it has located the edge, not when the budget runs out.
    const done = sr.probeAsked >= 2 || sr.probeQueue.length === 0;
    if (done) rec.phase = 'teach';

    return json({
      recorded: event,
      node_state: nodeState,
      next: done ? null : asQuestion(findItem(sr.probeQueue[0])!),
      done,
      asked: sr.probeAsked,
      budget: 6,
    });
  }

  /* ---- teach ---- */
  if (rest === 'teach/next' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    rec.phase = 'teach';
    const step = STEPS[Math.min(sr.stepIndex, STEPS.length - 1)];
    const item = findItem(step.item_id)!;
    sr.answersThisNode = 0;
    return json({
      node: NODES.find((n) => n.id === step.node_id),
      step: {
        strategy: step.strategy,
        markdown: step.markdown,
        mermaid: step.mermaid ?? null,
        svg: null,
        latex_ok: true,
        citations: step.citations,
      },
      checkpoint: asQuestion(item, sr.hintLevel.get(item.item_id) ?? 0),
      assistance_level: sr.hintLevel.get(item.item_id) ?? 0,
    });
  }

  if (rest === 'teach/hint' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    const item = findItem(String(body.item_id));
    if (!item) return fail('unknown item', 'not_found', 404);
    const level = Number(body.level);
    if (level === 6 && !sr.attempted.has(item.item_id)) {
      return fail(
        'The reveal is only available after a genuine attempt.',
        'reveal_before_attempt',
        409,
      );
    }
    if (level < 1 || level > 6) return fail('level must be 1-6', 'bad_request', 400);
    const current = sr.hintLevel.get(item.item_id) ?? 0;
    if (level > current + 1) {
      return fail('hints escalate one level at a time', 'hint_skip', 409);
    }
    sr.hintLevel.set(item.item_id, level);
    return json({ level, hint_markdown: item.hints[level] });
  }

  if (rest === 'teach/answer' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    const item = findItem(String(body.item_id));
    if (!item) return fail('unknown item', 'not_found', 404);

    const idk = Boolean(body.idk);
    const assistance = Number(body.assistance_level ?? sr.hintLevel.get(item.item_id) ?? 0);
    const response = idk ? 'IDK' : String(body.response ?? '');
    const correct = !idk && response === item.answer;
    sr.attempted.add(item.item_id);
    sr.answersThisNode += 1;

    const event = recordEvent({
      goal_id: goalId,
      session_id: sr.session.session_id,
      node_id: item.node_id,
      item_version_id: item.item_version_id,
      response,
      correct: correct ? 1 : 0,
      confidence: (body.confidence as number) ?? null,
      idk: idk ? 1 : 0,
      assistance_level: assistance,
      context: 'in-session',
    });

    // A confident wrong answer on a named distractor is what opens a suspicion - one tap,
    // never a recorded belief.
    const claim = !correct && !idk ? item.distractor_misconceptions[response] : undefined;
    const confident = Number(body.confidence ?? 0) >= 4;
    let suspected: { claim: string } | null = null;
    if (claim && confident) {
      suspected = { claim };
      if (!store.misconceptions.some((m) => m.claim === claim)) {
        store.misconceptions.push({
          misconception_id: nextId('m'),
          node_id: item.node_id,
          claim,
          state: 'suspected',
          steps_held: [],
          opened_at: now().slice(0, 10),
        });
      }
    }

    const nodeState = recomputeState(item.node_id);

    let decision: string = 'continue';
    if (!correct && sr.answersThisNode >= 3) decision = 'switch_strategy';
    else if (!correct) decision = 'repeat';
    else if (!sr.teachBackDone) decision = 'teach_back_due';

    let feedback = item.feedback[idk ? 'IDK' : response] ?? 'Recorded.';
    if (correct && assistance >= 5) {
      feedback +=
        '\n\nRecorded at assistance ' +
        assistance +
        ', which does not count toward mastery - not a punishment, just the map staying honest. We will come back to this one unaided.';
    }

    return json({
      correct,
      recorded: event,
      node_state: nodeState,
      feedback_markdown: feedback,
      reveal_allowed: true,
      misconception_suspected: suspected,
      decision,
    });
  }

  if (rest === 'teach/teach-back' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    const text = String(body.explanation ?? '');
    const nodeId = String(body.node_id);
    // Crude but deterministic: longer, more specific explanations score higher.
    const score = (text.length > 320 ? 3 : text.length > 160 ? 2 : text.length > 40 ? 1 : 0) as
      | 0
      | 1
      | 2
      | 3;
    sr.teachBackDone = true;
    const event = recordEvent({
      goal_id: goalId,
      session_id: sr.session.session_id,
      node_id: nodeId,
      kind: 'teach_back',
      correct: score >= 2 ? 1 : 0,
      assistance_level: 0,
      grader_version: 'teach-back-v1',
      evaluation_method: 'rubric',
      payload: { score },
    });
    recomputeState(nodeId);
    return json({
      score,
      rubric_version: 'teach-back-v1',
      feedback_markdown:
        score >= 2
          ? '**Score ' +
            score +
            '/3.** You named the object and its type before doing arithmetic, which is the part that transfers. Missing: why the identification with row vectors needs a basis.'
          : '**Score ' +
            score +
            '/3.** That is closer to a restatement than an explanation. Say what the object *is*, then what it *does*, then one example.',
      recorded: event,
    });
  }

  if (rest === 'misconception/step' && method === 'POST') {
    const claim = String(body.claim ?? '');
    const step = String(body.step) as Misconception['steps_held'][number];
    let m = store.misconceptions.find((x) => x.claim === claim);
    if (!m) {
      m = {
        misconception_id: nextId('m'),
        node_id: String(body.node_id),
        claim,
        state: 'suspected',
        steps_held: [],
        opened_at: now().slice(0, 10),
      };
      store.misconceptions.push(m);
    }
    const answer = String(body.learner_response ?? '').toLowerCase();
    // "held" unless the learner's own words say they meant something else.
    const dropped = /misread|misclick|guess|meant|typo|different/.test(answer);
    if (dropped) {
      m.state = 'resolved';
      recomputeState(m.node_id);
      return json({
        state: m.state,
        next_step: null,
        prompt_markdown:
          'Dropped - that reads like a slip, not a rule you would reapply. Nothing is recorded as a belief.',
      });
    }
    if (!m.steps_held.includes(step)) m.steps_held.push(step);
    const order: Array<Misconception['steps_held'][number]> = [
      'reasoning',
      'prediction',
      'counterexample',
    ];
    const nextStep = order[order.indexOf(step) + 1] ?? null;
    m.state = nextStep ? 'suspected' : 'active';
    recomputeState(m.node_id);
    return json({ state: m.state, next_step: nextStep });
  }

  if (rest === 'teach/interrupt' && method === 'POST') {
    const q = String(body.question ?? '');
    return json({
      answer_markdown:
        '**Short answer.** ' +
        (q.trim() || 'Good question.') +
        '\n\nBecause a covector is defined as a map into $\\mathbb{R}$, its output is a number by construction - the row-vector picture is a *coordinate representation* that happens to compute that number, not the definition. Ask again if that still feels like a dodge; we are not moving on until it does not.',
      citations: ['Spivak, Calculus on Manifolds, p. 75'],
    });
  }

  if (rest === 'dispute' && method === 'POST') {
    const type = String(body.type);
    const d: Dispute = {
      dispute_id: nextId('d'),
      node_id: String(body.node_id),
      item_id: (body.item_id as string) ?? null,
      type: type as Dispute['type'],
      note: String(body.note ?? ''),
      outcome: null,
      opened_at: now().slice(0, 10),
    };
    store.disputes.push(d);
    recordEvent({
      goal_id: goalId,
      session_id: rec.sessions.slice(-1)[0]?.session_id ?? 's_none',
      node_id: d.node_id,
      kind: 'dispute',
      payload: { type, note: d.note },
      evaluation_method: 'human',
    });
    // "I already know this" and "test me instead" are claims: they come back with a check.
    const needsCheck = type === 'I already know this' || type === 'test me instead';
    return json({
      dispute_id: d.dispute_id,
      check: needsCheck
        ? { items: [asQuestion(ITEMS.find((i) => i.node_id === d.node_id) ?? ITEMS[0])] }
        : null,
    });
  }

  if (rest === 'session/end' && method === 'POST') {
    const sr = store.sessions.get(String(body.session_id));
    if (!sr) return fail('unknown session', 'not_found', 404);
    sr.session.ended_at = now();
    sr.session.summary = String(body.summary ?? '') || 'Covectors: one unaided pass, one reveal.';
    rec.phase = 'done';
    recordEvent({
      goal_id: goalId,
      session_id: sr.session.session_id,
      node_id: STEPS[0].node_id,
      kind: 'session_end',
    });
    return json({
      session: sr.session,
      log_path: `vault/sessions/${now().slice(0, 10)}-${goalId}.md`,
    });
  }

  /* ---- views ---- */
  if (rest === 'map' && method === 'GET') {
    const order = learnerPath();
    return json({
      curriculum: { mermaid: mermaidFor(), nodes: NODES, edges: EDGES },
      path: { mermaid: mermaidFor(order), order },
      states: store.states,
    });
  }

  if (seg[2] === 'receipts' && method === 'GET') {
    const nodeId = decodeURIComponent(seg[3] ?? '');
    const node = NODES.find((n) => n.id === nodeId);
    if (!node) return fail(`node ${nodeId} not found`, 'not_found', 404);
    const state = store.states[nodeId] ?? recomputeState(nodeId);
    const evidence = store.events
      .filter((e) => e.node_id === nodeId)
      .sort((a, b) => (a.ts < b.ts ? 1 : -1));
    const why: string[] = [];
    if (state.state === 'known') {
      why.push(
        `${state.independent_passes} unaided passes and no recorded failure since the last revision.`,
      );
      if (state.last_delayed) why.push(`Held up on a delayed retrieval on ${state.last_delayed}.`);
    } else if (state.state === 'fragile') {
      why.push(
        `${state.independent_passes} unaided pass(es) but ${state.assisted_passes} needed hints and ${state.fails} failed.`,
      );
      if (state.last_delayed) why.push(`Most recent delayed retrieval: ${state.last_delayed}.`);
      if (!state.transfer_passes) why.push('No transfer evidence yet - only the taught surface form.');
    } else if (state.state === 'misconception') {
      why.push('An active misconception outranks the pass counts on this node.');
      why.push('It clears only after the counterexample step, then a clean unaided pass.');
    } else {
      why.push('No evidence either way. Unknown here means unmeasured, not judged.');
    }
    if (state.self_graded_passes) {
      why.push(
        `${state.self_graded_passes} pass(es) were host-LLM graded and do not move this node past fragile.`,
      );
    }
    return json({
      node,
      state,
      evidence,
      why,
      misconceptions: store.misconceptions.filter((m) => m.node_id === nodeId),
      disputes: store.disputes.filter((d) => d.node_id === nodeId),
    });
  }

  if (rest === 'metrics' && method === 'GET') {
    const answers = store.events.filter((e) => e.kind === 'answer' || e.kind === 'probe_answer');
    const delayed = answers.filter((e) => e.context === 'delayed');
    const delayedPass = delayed.filter((e) => e.correct === 1).length;
    return json({
      measured_at: now(),
      // Convention the metrics page reads: <key>_n is the observation count behind <key>,
      // <key>_note is the caveat printed next to it.
      holdout_success_7d: delayed.length ? delayedPass / delayed.length : null,
      holdout_success_7d_n: delayed.length,
      holdout_success_7d_note: 'Two delayed retrievals is not a rate yet; it is a tally.',
      false_mastery_rate: null,
      false_mastery_rate_note: 'Not computable yet: no holdout has been served twice.',
      item_rejection_rate: 0.18,
      item_rejection_rate_n: 17,
      sessions: Array.from(store.goals.values()).reduce((a, g) => a + g.sessions.length, 0) + 4,
      mean_assistance: answers.length
        ? answers.reduce((a, e) => a + e.assistance_level, 0) / answers.length
        : null,
      mean_assistance_n: answers.length,
      sessions_n: null,
      events_total: store.events.length,
    });
  }

  return fail(`no route for ${method} ${path}`, 'not_found', 404);
}

/* ------------------------------------------------------------------ goal edit */

const GOAL_EDITABLE = [
  'deadline',
  'minutes_per_session',
  'sessions_per_week',
  'title',
  'purpose',
  'assessment',
  'depth',
] as const;

/** PATCH /goals/{g}: absent = unchanged, `deadline: ""` = clear. */
function patchGoal(rec: GoalRecord, body: Record<string, unknown>): Response {
  const next: Goal = { ...rec.goal };
  if ('deadline' in body) {
    const d = String(body.deadline ?? '');
    if (d && !/^\d{4}-\d{2}-\d{2}$/.test(d)) {
      return fail('deadline must be YYYY-MM-DD, or "" to clear it', 'bad_request', 400);
    }
    // A rule of this mock, so the inline error is reachable from the form: the real gateway
    // may or may not refuse a past date.
    if (d && d < now().slice(0, 10)) {
      return fail(`deadline ${d} is in the past`, 'bad_request', 400);
    }
    next.deadline = d || null;
  }
  for (const k of ['minutes_per_session', 'sessions_per_week'] as const) {
    if (!(k in body)) continue;
    const v = Number(body[k]);
    if (!Number.isInteger(v) || v < 1) {
      return fail(`${k} must be a positive whole number`, 'bad_request', 400);
    }
    next[k] = v;
  }
  for (const k of ['title', 'purpose', 'assessment', 'depth'] as const) {
    if (k in body) (next as unknown as Record<string, unknown>)[k] = body[k];
  }
  const changed: Record<string, { from: unknown; to: unknown }> = {};
  for (const k of GOAL_EDITABLE) {
    if (rec.goal[k] !== next[k]) changed[k] = { from: rec.goal[k] ?? null, to: next[k] ?? null };
  }
  rec.goal = next;
  // The fake plan route never stores a feasibility, so there is nothing to recompute: the UI
  // falls back to src/lib/feasibility.ts with the new goal fields, as it does for real.
  return json({ goal: next, changed, feasibility: null });
}

/* ------------------------------------------------------------------ study tools
 * The per-goal study record, practice grading, progress and mocks live in ./exam.ts (with
 * sessionStorage persistence, so a reload mid-mock resumes). This file keeps the routes,
 * cards, tables and the importer. Keys and backs never leave the fake before an attempt. */

/** Due first (oldest first), then new up to the daily cap. */
function queueOf<T extends { due: number | null }>(
  list: T[],
  newToday: number,
  newLimit: number,
): Array<{ it: T; reason: 'due' | 'new' }> {
  const t = Date.now();
  const due = list
    .filter((x) => x.due !== null && x.due <= t)
    .sort((a, b) => (a.due as number) - (b.due as number))
    .map((it) => ({ it, reason: 'due' as const }));
  const fresh = list
    .filter((x) => x.due === null)
    .slice(0, Math.max(0, newLimit - newToday))
    .map((it) => ({ it, reason: 'new' as const }));
  return [...due, ...fresh];
}

/**
 * Ids for things the study record persists across a reload. `nextId` restarts at 1 on every
 * page load, so it could hand out an id a persisted import already has.
 */
let studySeq = 0;
function studyId(prefix: string): string {
  studySeq += 1;
  return `${prefix}_${Date.now().toString(36)}${studySeq.toString(36)}`;
}

function optionOf(p: MockPractice, key: string | null | undefined): QuestionOption | null {
  return p.options.find((o) => o.key === key) ?? null;
}

function tableSummary(t: MockTable): TableSummary {
  return {
    table_id: t.table_id,
    title: t.title,
    node_id: t.node_id,
    node_title: t.node_title,
    columns: t.columns,
    row_count: t.rows.length,
    source: t.source,
    author: t.author,
    created_at: t.created_at,
  };
}

/** Anki reads `#` header lines: separator, html, and which column carries the tags. */
function exportText(rows: string[][], format: 'tsv' | 'csv'): string {
  const cell = (s: string) =>
    format === 'tsv'
      ? s.replace(/\t/g, ' ').replace(/\r?\n/g, '<br>')
      : `"${s.replace(/"/g, '""').replace(/\r?\n/g, '<br>')}"`;
  const sep = format === 'tsv' ? '\t' : ',';
  const head = [`#separator:${format === 'tsv' ? 'tab' : 'comma'}`, '#html:true', '#tags column:3'];
  return [...head, ...rows.map((r) => r.map(cell).join(sep))].join('\n') + '\n';
}

function slug(s: string): string {
  return (
    s
      .normalize('NFD')
      .replace(/[̀-ͯ]/g, '')
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '_')
      .replace(/^_|_$/g, '')
      .slice(0, 32) || 'node'
  );
}

/** Study routes. A FakeError becomes the contract's `{error, code}`; every write persists. */
async function studyRoute(ctx: Ctx, goal: Goal, seg: string[]): Promise<Response | null> {
  try {
    const res = await studyRouteInner(ctx, goal, seg);
    if (res && res.ok && ctx.method !== 'GET') persistStudy(goal.goal_id);
    return res;
  } catch (e) {
    if (e instanceof FakeError) return fail(e.message, e.code, e.status);
    throw e;
  }
}

async function studyRouteInner(ctx: Ctx, goal: Goal, seg: string[]): Promise<Response | null> {
  const { method, body, query } = ctx;
  const goalId = goal.goal_id;
  const rest = seg.join('/');
  const s = studyFor(goalId);

  if (rest === 'study' && method === 'GET') {
    return json({
      bank: bankCounts(s),
      cards: cardCounts(s),
      tables: s.tables.map(tableSummary),
      importable: Object.entries(s.importable).map(([path, text]) => ({
        path,
        name: path.split('/').pop() ?? path,
        bytes: new TextEncoder().encode(text).length,
      })),
      blueprint: s.blueprint !== null,
      mock: mockFlags(s),
    });
  }

  if (rest === 'study/import' && method === 'POST') return importStudy(ctx, s);

  /* ---- exam prep: blueprint, progress, mocks ---- */
  if (rest === 'blueprint' && method === 'GET') {
    if (!s.blueprint) return fail(`goal ${goalId} has no exam blueprint`, 'no_blueprint', 404);
    return json(s.blueprint);
  }
  if (rest === 'progress' && method === 'GET') return json(progress(s, goalId, goal.deadline));
  if (rest === 'mocks' && method === 'GET') return json(listMocks(s));
  if (rest === 'mocks' && method === 'POST') return json(startMock(s, body));
  if (seg[0] === 'mocks' && seg.length === 2 && method === 'GET') {
    return json(getMock(s, decodeURIComponent(seg[1])));
  }
  if (seg[0] === 'mocks' && seg[2] === 'submit' && seg.length === 3 && method === 'POST') {
    return json(submitMock(s, decodeURIComponent(seg[1]), body));
  }

  /* ---- practice ---- */
  if (rest === 'practice/next' && method === 'GET') {
    const n = Math.max(1, Number(query.get('n') ?? 1) || 1);
    return json(practiceNext(s, n, query.get('focus') ?? ''));
  }

  if (rest === 'practice/answer' && method === 'POST') {
    const g = practiceAnswer(s, body);
    const p = g.item;
    const counts = p.check === 'checked';
    let nodeState: NodeState;
    if (s.blueprint) {
      // Exam goals keep their evidence in the study record (it survives a reload).
      nodeState = stateFromAttempts(s, p.node_id);
    } else if (counts) {
      recordEvent({
        goal_id: goalId,
        session_id: 's_practice',
        node_id: p.node_id,
        item_version_id: p.item_version_id,
        response: g.idk ? 'IDK' : (g.your_answer?.key ?? null),
        correct: g.idk ? null : g.correct ? 1 : 0,
        confidence: (body.confidence as number) ?? null,
        idk: g.idk ? 1 : 0,
        context: g.context,
        grader_version: 'mc-key-v1',
        evaluation_method: 'blind_solver',
        payload: { shown_order: s.attempts[s.attempts.length - 1]?.order ?? null },
      });
      nodeState = recomputeState(p.node_id);
    } else {
      nodeState = store.states[p.node_id] ?? recomputeState(p.node_id);
    }
    return json({
      item_id: p.item_id,
      correct: g.correct,
      idk: g.idk,
      your_answer: g.your_answer,
      correct_answer: g.correct_answer,
      explanation: p.explanation,
      checked: counts,
      counts_toward_mastery: counts,
      context: g.context,
      node_state: nodeState,
      schedule: { due: new Date(g.due).toISOString(), rating: g.correct ? 'good' : 'again' },
      note: counts
        ? null
        : 'Imported and not yet blind-checked: recorded for scheduling, never as evidence.',
    });
  }

  if (rest === 'practice/review' && method === 'GET') {
    return json({
      items: s.practice
        .filter((p) => p.check === 'rejected')
        .map((p) => ({
          item_id: p.item_id,
          node_title: p.node_title,
          stem: p.stem,
          options: p.options,
          answer: optionOf(p, p.answer),
          solver_answer: optionOf(p, p.solver_answer),
          ambiguous: Boolean(p.ambiguous),
          notes: p.notes ?? null,
          explanation: p.explanation,
        })),
    });
  }

  /* ---- cards ---- */
  if (rest === 'cards/next' && method === 'GET') {
    const n = Math.max(1, Number(query.get('n') ?? 1) || 1);
    const queue = queueOf(s.cards, s.newCardsToday, CARDS_NEW_PER_DAY).slice(0, n);
    return json({
      // The front only. The back is a separate reveal call, by contract.
      cards: queue.map(({ it, reason }) => ({
        item_id: it.item_id,
        node_id: it.node_id,
        node_title: it.node_title,
        front: it.front,
        reason,
      })),
      counts: cardCounts(s),
      done: queue.length === 0,
    });
  }

  if ((rest === 'cards/reveal' || rest === 'cards/review') && method === 'POST') {
    const id = String(body.item_id ?? '');
    const c = s.cards.find((x) => x.item_id === id);
    if (!c) {
      if (s.practice.some((p) => p.item_id === id)) {
        return fail('that item is a question, not a card', 'bad_request', 400);
      }
      return fail(`card ${id} not found`, 'not_found', 404);
    }
    if (rest === 'cards/reveal') {
      return json({ item_id: c.item_id, front: c.front, back: c.back, source: c.source });
    }
    const rating = String(body.rating ?? '') as CardRating;
    if (!CARD_RATINGS.includes(rating)) {
      return fail('rating must be again, hard, good or easy', 'bad_request', 400);
    }
    const hours: Record<CardRating, number> = { again: 1 / 6, hard: 24, good: 72, easy: 168 };
    if (c.due === null) s.newCardsToday += 1;
    logCardReview(s);
    c.due = Date.now() + hours[rating] * HOUR;
    c.state = rating === 'again' ? 'relearning' : 'review';
    return json({
      item_id: c.item_id,
      rating,
      schedule: { due: new Date(c.due).toISOString(), state: c.state },
      counts_toward_mastery: false,
      note: 'Self-report: this schedules the next review and is never evidence.',
    });
  }

  if (rest === 'cards/export' && method === 'GET') {
    const format = query.get('format') ?? 'tsv';
    const include = query.get('include') ?? 'cards';
    if (format !== 'tsv' && format !== 'csv') return fail('format must be tsv or csv', 'bad_request', 400);
    if (!['cards', 'questions', 'all'].includes(include)) {
      return fail('include must be cards, questions or all', 'bad_request', 400);
    }
    const rows: string[][] = [];
    if (include !== 'questions') {
      for (const c of s.cards) rows.push([c.front, c.back, `lt ${slug(c.node_title)}`]);
    }
    if (include !== 'cards') {
      // Sealed mock questions stay out of the export until a mock has used them.
      for (const p of s.practice.filter(servable)) {
        const key = optionOf(p, p.answer);
        rows.push([
          `${p.stem}\n\n${p.options.map((o) => `${o.key}) ${o.text}`).join('\n')}`,
          `${key?.key}) ${key?.text}${p.explanation ? `\n\n${p.explanation}` : ''}`,
          `lt ${slug(p.node_title)} question`,
        ]);
      }
    }
    return new Response(exportText(rows, format), {
      status: 200,
      headers: {
        'Content-Type': format === 'tsv' ? 'text/tab-separated-values; charset=utf-8' : 'text/csv; charset=utf-8',
        'Content-Disposition': `attachment; filename="${goalId}-${include}.${format}"`,
      },
    });
  }

  /* ---- tables ---- */
  if (rest === 'tables' && method === 'GET') {
    return json({ tables: s.tables.map(tableSummary) });
  }
  if (seg[0] === 'tables' && seg.length >= 2) {
    const t = s.tables.find((x) => x.table_id === decodeURIComponent(seg[1]));
    if (!t) return fail(`table ${seg[1]} not found`, 'not_found', 404);
    if (seg.length === 2 && method === 'GET') return json({ ...tableSummary(t), rows: t.rows });
    if (seg[2] === 'cards' && method === 'POST') {
      // One card per row: the first cell is the front, the rest labelled on the back.
      let imported = 0;
      let skipped = 0;
      for (const row of t.rows) {
        const front = row[0] ?? '';
        const back =
          t.columns.length === 2
            ? (row[1] ?? '')
            : t.columns
                .slice(1)
                .map((col, i) => `- **${col}:** ${row[i + 1] ?? ''}`)
                .join('\n');
        if (s.cards.some((c) => c.front === front && c.back === back)) {
          skipped += 1;
          continue;
        }
        s.cards.push({
          item_id: studyId('c_tbl'),
          node_id: t.node_id ?? 'n_unassigned_0000',
          node_title: t.node_title ?? t.title,
          front,
          back,
          source: `table ${t.table_id}`,
          due: null,
          state: 'new',
        });
        imported += 1;
      }
      return json({ parsed: t.rows.length, imported, skipped_existing: skipped });
    }
  }

  return null;
}

/* ---- the importer, small but honest about the format in CONTRACTS.md ---- */

interface ParsedDoc {
  questions: Array<{ n: string; stem: string; tag: string | null; options: QuestionOption[] }>;
  keys: Map<string, { letter: string; explanation: string }>;
  cards: Array<{ front: string; back: string; tag: string | null }>;
  tables: Array<{ title: string; columns: string[]; rows: string[][]; tag: string | null }>;
  /** `## <tag> <title>` headings: node titles for the tags questions carry. */
  headings: Map<string, string>;
}

function splitRow(line: string): string[] {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((c) => c.trim());
}

function parseStudyMarkdown(src: string): ParsedDoc {
  const lines = src.split(/\r?\n/);
  const doc: ParsedDoc = { questions: [], keys: new Map(), cards: [], tables: [], headings: new Map() };
  let heading = '';
  let tag: string | null = null;
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];

    const h = line.match(/^#{1,6}\s+(.+?)\s*$/);
    if (h) {
      heading = h[1];
      const t = heading.match(/^([A-Za-z]{0,4}\d+[\w.]*)\s+(.+)$/);
      tag = t ? t[1] : null;
      if (t) doc.headings.set(t[1], t[2]);
      i += 1;
      continue;
    }

    // GFM pipe table. An answer-key table (| N | tag | LETTER | explanation |) is keys, not data.
    if (/^\s*\|/.test(line) && /^\s*\|?\s*:?-{3,}/.test(lines[i + 1] ?? '')) {
      const columns = splitRow(line);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && /^\s*\|/.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i += 1;
      }
      const isKey =
        /^(n|#|no\.?)$/i.test(columns[0] ?? '') &&
        rows.every((r) => /^\d+$/.test(r[0] ?? '') && /^[A-E]$/i.test(r[2] ?? ''));
      if (isKey) {
        for (const r of rows) {
          doc.keys.set(r[0], { letter: r[2].toUpperCase(), explanation: r[3] ?? '' });
        }
      } else {
        doc.tables.push({ title: heading || 'Untitled table', columns, rows, tag });
      }
      continue;
    }

    const q = line.match(/^(\d+)\.\s+(.+)$/);
    if (q) {
      const stemLines = [q[2]];
      const options: QuestionOption[] = [];
      i += 1;
      while (i < lines.length && lines[i].trim() && !/^\d+\.\s+/.test(lines[i]) && !/^#/.test(lines[i])) {
        const o = lines[i].match(/^([A-E])\)\s+(.+)$/);
        if (o) options.push({ key: o[1], text: o[2] });
        else if (!options.length) stemLines.push(lines[i]);
        i += 1;
      }
      let stem = stemLines.join('\n');
      const tm = stem.match(/\s*\[([^\]]+)\]\s*$/);
      if (tm) stem = stem.slice(0, tm.index).trimEnd();
      doc.questions.push({ n: q[1], stem, tag: tm ? tm[1] : tag, options });
      continue;
    }

    const c = line.match(/^\*\*(.+?)\*\*\s*(.*)$/);
    if (c) {
      const back = [c[2]];
      i += 1;
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        back.push(lines[i]);
        i += 1;
      }
      doc.cards.push({ front: c[1].replace(/[.:]\s*$/, ''), back: back.join('\n').trim(), tag });
      continue;
    }

    i += 1;
  }
  return doc;
}

async function importStudy(ctx: Ctx, s: StudyRecord): Promise<Response> {
  let text: string;
  let keyText: string | null = null;
  let source: string;
  let what: string[];
  let dryRun: boolean;

  if (ctx.form) {
    const file = ctx.form.get('file');
    if (!(file instanceof File)) return fail('file is required', 'bad_request', 400);
    const key = ctx.form.get('key_file');
    text = await file.text();
    if (key instanceof File) keyText = await key.text();
    source = `upload:${file.name}`;
    what = ctx.form.getAll('what').map(String);
    dryRun = ctx.form.get('dry_run') === '1';
  } else {
    const path = String(ctx.body.path ?? '');
    if (!path) return fail('path is required', 'bad_request', 400);
    if (!(path in s.importable)) {
      return fail(`${path} is not in this goal's sources dir`, 'not_found', 404);
    }
    text = s.importable[path];
    const keyPath = ctx.body.key_path ? String(ctx.body.key_path) : null;
    if (keyPath) {
      if (!(keyPath in s.importable)) {
        return fail(`${keyPath} is not in this goal's sources dir`, 'not_found', 404);
      }
      keyText = s.importable[keyPath];
    }
    source = path;
    what = Array.isArray(ctx.body.what) ? (ctx.body.what as unknown[]).map(String) : [];
    dryRun = Boolean(ctx.body.dry_run);
  }

  const kinds: ImportKind[] = ['questions', 'cards', 'tables'];
  if (!what.length) what = kinds;
  const bad = what.find((w) => !kinds.includes(w as ImportKind));
  if (bad) return fail(`what: unknown kind "${bad}"`, 'bad_request', 400);

  const doc = parseStudyMarkdown(text);
  const keys = new Map([...doc.keys, ...(keyText ? parseStudyMarkdown(keyText).keys : [])]);

  const nodesCreated: Array<{ node_id: string; title: string }> = [];
  const pendingNodes = new Map(s.nodes);
  const nodeFor = (tag: string | null) => {
    if (!tag) return { node_id: 'n_unassigned_0000', title: 'Unassigned' };
    const known = pendingNodes.get(tag);
    if (known) return known;
    const title = doc.headings.get(tag) ?? tag;
    const node = {
      node_id: `n_${slug(title)}_${(0x1000 + pendingNodes.size * 2654).toString(16).slice(-4)}`,
      title,
    };
    pendingNodes.set(tag, node);
    nodesCreated.push(node);
    return node;
  };

  const report: ImportReport = {
    source,
    dry_run: dryRun,
    questions: null,
    cards: null,
    tables: null,
    nodes_created: nodesCreated,
  };

  if (what.includes('questions')) {
    const r = { parsed: doc.questions.length, imported: 0, skipped_existing: 0, problems: [] as string[] };
    for (const q of doc.questions) {
      const key = keys.get(q.n);
      if (!q.options.length) {
        r.problems.push(`question ${q.n}: no options (A) ... lines) - skipped`);
        continue;
      }
      if (!key) {
        r.problems.push(`question ${q.n}: no answer key - skipped`);
        continue;
      }
      if (!q.options.some((o) => o.key === key.letter)) {
        r.problems.push(`question ${q.n}: key ${key.letter} is not one of its options - skipped`);
        continue;
      }
      const sourceKey = `${source}#${q.n}`;
      if (s.practice.some((p) => p.source_key === sourceKey || p.stem === q.stem)) {
        r.skipped_existing += 1;
        continue;
      }
      r.imported += 1;
      const node = nodeFor(q.tag);
      if (!dryRun) {
        const id = studyId('pq_imp');
        s.practice.push({
          item_id: id,
          item_version_id: `${id}_v1`,
          node_id: node.node_id,
          node_title: node.title,
          stem: q.stem,
          options: q.options,
          answer: key.letter,
          explanation: key.explanation || null,
          // Every import starts TEACHING_ONLY: it counts only after a blind check.
          check: 'unchecked',
          source_key: sourceKey,
          due: null,
          last: null,
        });
      }
    }
    report.questions = r;
  }

  if (what.includes('cards')) {
    const r = { parsed: doc.cards.length, imported: 0, skipped_existing: 0 };
    for (const c of doc.cards) {
      if (s.cards.some((x) => x.front === c.front)) {
        r.skipped_existing += 1;
        continue;
      }
      r.imported += 1;
      const node = nodeFor(c.tag);
      if (!dryRun) {
        s.cards.push({
          item_id: studyId('c_imp'),
          node_id: node.node_id,
          node_title: node.title,
          front: c.front,
          back: c.back,
          source,
          due: null,
          state: 'new',
        });
      }
    }
    report.cards = r;
  }

  if (what.includes('tables')) {
    const r = { parsed: doc.tables.length, imported: 0, skipped_existing: 0 };
    for (const t of doc.tables) {
      if (s.tables.some((x) => x.title === t.title && x.columns.join('|') === t.columns.join('|'))) {
        r.skipped_existing += 1;
        continue;
      }
      r.imported += 1;
      const node = t.tag ? nodeFor(t.tag) : null;
      if (!dryRun) {
        s.tables.push({
          table_id: studyId('t_imp'),
          title: t.title,
          node_id: node?.node_id ?? null,
          node_title: node?.title ?? null,
          columns: t.columns,
          rows: t.rows,
          source,
          author: 'import',
          created_at: now(),
        });
      }
    }
    report.tables = r;
  }

  if (!dryRun) s.nodes = pendingNodes;
  return json(report);
}

function freshSummary(rec: GoalRecord): string {
  const lines = [
    `# Learner - ${now().slice(0, 10)}`,
    '',
    `Goal: ${rec.goal.concept} - ${rec.goal.depth}${
      rec.goal.deadline ? ` - deadline ${rec.goal.deadline}` : ''
    }`,
    '',
  ];
  if (!rec.graphVersion) {
    lines.push('No graph yet. Ground the goal and build the plan, then the map fills in.');
    return lines.join('\n');
  }
  for (const colour of ['known', 'fragile', 'unknown', 'misconception'] as const) {
    const titles = NODES.filter((n) => (store.states[n.id]?.state ?? 'unknown') === colour).map(
      (n) => n.title,
    );
    if (titles.length) lines.push(`- **${colour}:** ${titles.join(', ')}`);
  }
  const active = store.misconceptions.filter((m) => m.state === 'active');
  for (const m of active) lines.push(`- **Misconception (active):** "${m.claim}"`);
  lines.push('', 'Evidence, never decimals: open the map and click a node for the receipts.');
  return lines.join('\n');
}

/* ------------------------------------------------------------------ install */

let installed = false;

export function installMockGateway(): void {
  if (installed || typeof window === 'undefined') return;
  installed = true;

  const real = window.fetch.bind(window);

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url =
      typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const method = (init?.method ?? (input instanceof Request ? input.method : 'GET')).toUpperCase();

    const idx = url.indexOf(GATEWAY_BASE);
    const isGateway = GATEWAY_BASE.startsWith('/')
      ? new URL(url, window.location.origin).pathname.startsWith(GATEWAY_BASE)
      : idx >= 0;
    if (!isGateway) return real(input as RequestInfo, init);

    const parsed = new URL(url, window.location.origin);
    const pathname = parsed.pathname;
    const path = pathname.slice(pathname.indexOf(GATEWAY_BASE) + GATEWAY_BASE.length) || '/';

    let body: Record<string, unknown> = {};
    let form: FormData | null = null;
    if (init?.body instanceof FormData) form = init.body;
    else if (typeof init?.body === 'string') {
      try {
        body = JSON.parse(init.body) as Record<string, unknown>;
      } catch {
        body = {};
      }
    }

    // Same key + same body replays the first response, exactly as CONTRACTS.md requires.
    const key = (init?.headers as Record<string, string> | undefined)?.['Idempotency-Key'];
    const cacheKey = key ? `${key}:${method}:${path}` : null;
    if (cacheKey && store.idempotency.has(cacheKey)) {
      return json(store.idempotency.get(cacheKey));
    }

    await new Promise((r) => setTimeout(r, 120)); // enough latency to see loading states

    const res = await route({ path, method, body, form, query: parsed.searchParams });

    if (cacheKey && res.ok && res.headers.get('Content-Type')?.includes('json')) {
      const clone = res.clone();
      store.idempotency.set(cacheKey, await clone.json());
    }
    return res;
  };

  console.info('[learning-tutor] mock gateway installed on', GATEWAY_BASE);
}
