/**
 * The one typed client for the Stage 2 gateway.
 *
 * Every route in CONTRACTS.md "Gateway response shapes" has exactly one function here.
 * No component calls `fetch` directly.
 *
 * Base URL: `NEXT_PUBLIC_GATEWAY_URL`, default `/api` (same origin - the nginx image in
 * ./Dockerfile proxies `/api` to `${GATEWAY_URL}`, and the gateway serving `out/` from
 * `LT_WEB_DIR` already answers on the same origin).
 */
import type {
  Blueprint,
  CardRating,
  CardRevealResponse,
  CardReviewResponse,
  CardsNextResponse,
  CreateGoalResponse,
  DisputeResponse,
  DisputeType,
  GoalContract,
  GoalDetailResponse,
  GoalPatch,
  GoalPatchResponse,
  GoalsResponse,
  GraphEdge,
  GraphNode,
  HealthResponse,
  HintResponse,
  ImportCount,
  ImportKind,
  ImportReport,
  MapResponse,
  MetricsResponse,
  MisconceptionStep,
  MisconceptionStepResponse,
  MockAnswer,
  MockOpen,
  MockResult,
  MockSession,
  MocksResponse,
  PlanApproveResponse,
  PlanResponse,
  PracticeAnswerResponse,
  PracticeNextResponse,
  PracticeReviewResponse,
  ProbeAnswerResponse,
  ProbeStartResponse,
  Progress,
  ReceiptsResponse,
  RenderResponse,
  ResearchApproveResponse,
  ResearchResponse,
  SessionEndResponse,
  SourceRole,
  SourcesResponse,
  StudyResponse,
  StudyTable,
  TableSummary,
  TeachAnswerResponse,
  TeachBackResponse,
  TeachNextResponse,
} from './types';

export const GATEWAY_BASE = (process.env.NEXT_PUBLIC_GATEWAY_URL ?? '/api').replace(/\/$/, '');

export const MOCK_ENABLED = process.env.NEXT_PUBLIC_MOCK === '1';

/** Typed error: mirrors the gateway body `{error, code}` and keeps the HTTP status. */
export class ApiError extends Error {
  readonly code: string;
  readonly status: number;

  constructor(error: string, code: string, status: number) {
    super(error);
    this.name = 'ApiError';
    this.code = code;
    this.status = status;
  }
}

export function errorMessage(e: unknown): string {
  if (e instanceof ApiError) return `${e.message} (${e.code})`;
  if (e instanceof Error) return e.message;
  return String(e);
}

/** RFC 4122 v4 where available, with a non-crypto fallback for older browsers. */
function idempotencyKey(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return `k-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 12)}`;
}

let mockReady: Promise<void> | null = null;

/**
 * In mock mode the fake gateway is loaded lazily, so it lands in its own chunk. The
 * condition is the literal env comparison, not `MOCK_ENABLED`: Next inlines
 * `NEXT_PUBLIC_MOCK`, webpack then sees `"0" === "1"` and drops the branch - `import()`
 * included - so a production build does not even emit the fake's chunk into `out/`.
 */
async function ensureMockGateway(): Promise<void> {
  if (typeof window === 'undefined') return;
  if (process.env.NEXT_PUBLIC_MOCK === '1') {
    if (!mockReady) {
      mockReady = import('@/mocks/gateway').then((m) => m.installMockGateway());
    }
    await mockReady;
  }
}

type Method = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';

interface RequestOptions {
  method?: Method;
  body?: unknown;
  /** Pre-built FormData for multipart routes; skips JSON encoding. */
  form?: FormData;
  signal?: AbortSignal;
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  await ensureMockGateway();

  const method = opts.method ?? 'GET';
  const headers: Record<string, string> = { Accept: 'application/json' };

  // Every mutating route accepts an Idempotency-Key (CONTRACTS.md); generate one per call
  // so a retry of the *same* click replays rather than double-writing.
  if (method !== 'GET') headers['Idempotency-Key'] = idempotencyKey();

  let body: BodyInit | undefined;
  if (opts.form) {
    body = opts.form; // browser sets the multipart boundary
  } else if (opts.body !== undefined) {
    headers['Content-Type'] = 'application/json';
    body = JSON.stringify(opts.body);
  }

  const res = await fetch(`${GATEWAY_BASE}${path}`, {
    method,
    headers,
    body,
    signal: opts.signal,
  });

  if (!res.ok) throw await toApiError(res);

  if (res.status === 204) return undefined as T;
  const text = await res.text();
  if (!text) return undefined as T;
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new ApiError('Gateway returned a non-JSON body', 'bad_response', res.status);
  }
}

async function toApiError(res: Response): Promise<ApiError> {
  let error = `Request failed with ${res.status}`;
  let code = `http_${res.status}`;
  try {
    const body = (await res.json()) as { error?: string; code?: string };
    if (body && typeof body.error === 'string') error = body.error;
    if (body && typeof body.code === 'string') code = body.code;
  } catch {
    /* non-JSON error body: keep the generic message */
  }
  return new ApiError(error, code, res.status);
}

const enc = encodeURIComponent;

/* ------------------------------------------------------------------ goals */

/* ---------------------------------------------------------------- graph shape
 * learner-svc (and so the gateway) emits nodes as {node_id,...} and edges as
 * {from_node,to_node,...}; the pinned contract and the mock use {id} / {from,to}.
 * Normalise once here so every screen draws either shape. */
type AnyNode = GraphNode & { node_id?: string };
type AnyEdge = GraphEdge & { from_node?: string; to_node?: string };
function normNodes(nodes: AnyNode[] | undefined): GraphNode[] {
  return (nodes ?? []).map((n) => ({ ...n, id: n.id ?? n.node_id ?? '' }));
}
function normEdges(edges: AnyEdge[] | undefined): GraphEdge[] {
  return (edges ?? []).map((e) => ({ ...e, from: e.from ?? e.from_node ?? '', to: e.to ?? e.to_node ?? '' }));
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  listGoals: () => request<GoalsResponse>('/goals'),

  createGoal: (contract: GoalContract) =>
    request<CreateGoalResponse>('/goals', { method: 'POST', body: contract }),

  getGoal: (goalId: string) => request<GoalDetailResponse>(`/goals/${enc(goalId)}`),

  /** Absent fields are unchanged; `deadline: ""` clears the deadline. */
  updateGoal: (goalId: string, patch: GoalPatch) =>
    request<GoalPatchResponse>(`/goals/${enc(goalId)}`, { method: 'PATCH', body: patch }),

  /* ---------------------------------------------------------------- grounding */

  listSources: (goalId: string) => request<SourcesResponse>(`/goals/${enc(goalId)}/sources`),

  uploadSources: (goalId: string, files: File[], role: SourceRole) => {
    const form = new FormData();
    form.append('role', role);
    for (const f of files) form.append('file', f, f.name);
    return request<SourcesResponse>(`/goals/${enc(goalId)}/sources`, { method: 'POST', form });
  },

  research: (goalId: string, body: { topic?: string; guidelines?: string }) =>
    request<ResearchResponse>(`/goals/${enc(goalId)}/research`, { method: 'POST', body }),

  approveResearch: (goalId: string, proposalId: string, accept: string[]) =>
    request<ResearchApproveResponse>(`/goals/${enc(goalId)}/research/approve`, {
      method: 'POST',
      body: { proposal_id: proposalId, accept },
    }),

  /* ---------------------------------------------------------------- plan */

  plan: (goalId: string) =>
    request<PlanResponse>(`/goals/${enc(goalId)}/plan`, { method: 'POST', body: {} }).then(
      (r) => ({ ...r, nodes: normNodes(r.nodes), edges: normEdges(r.edges) }),
    ),

  approvePlan: (goalId: string, ops: unknown[] = []) =>
    request<PlanApproveResponse>(`/goals/${enc(goalId)}/plan/approve`, {
      method: 'POST',
      body: { ops },
    }),

  /* ---------------------------------------------------------------- probe */

  probeStart: (goalId: string) =>
    request<ProbeStartResponse>(`/goals/${enc(goalId)}/probe/start`, { method: 'POST', body: {} }),

  probeAnswer: (
    goalId: string,
    body: {
      session_id: string;
      item_id: string;
      response: string;
      confidence?: number;
      idk?: boolean;
    },
  ) => request<ProbeAnswerResponse>(`/goals/${enc(goalId)}/probe/answer`, { method: 'POST', body }),

  /* ---------------------------------------------------------------- teach */

  teachNext: (goalId: string, sessionId: string) =>
    request<TeachNextResponse>(`/goals/${enc(goalId)}/teach/next`, {
      method: 'POST',
      body: { session_id: sessionId },
    }),

  /** level <= 5 always; level 6 is a 409 until an attempt has been recorded. */
  teachHint: (goalId: string, body: { session_id: string; item_id: string; level: number }) =>
    request<HintResponse>(`/goals/${enc(goalId)}/teach/hint`, { method: 'POST', body }),

  teachAnswer: (
    goalId: string,
    body: {
      session_id: string;
      item_id: string;
      response: string;
      confidence?: number;
      idk?: boolean;
      assistance_level: number;
    },
  ) => request<TeachAnswerResponse>(`/goals/${enc(goalId)}/teach/answer`, { method: 'POST', body }),

  teachBack: (goalId: string, body: { session_id: string; node_id: string; explanation: string }) =>
    request<TeachBackResponse>(`/goals/${enc(goalId)}/teach/teach-back`, { method: 'POST', body }),

  /**
   * "Wait, why?" mid-step (IDEA.md, "What's missing" item 10):
   *   POST /api/goals/{g}/teach/interrupt {session_id, node_id, question}
   *     -> {answer_markdown, citations[]}
   * On 404/405 (a gateway without the route) the UI shows a visible "not implemented"
   * message instead of silently dropping the question.
   */
  interrupt: (goalId: string, body: { session_id: string; node_id: string; question: string }) =>
    request<{ answer_markdown: string; citations?: string[] }>(
      `/goals/${enc(goalId)}/teach/interrupt`,
      { method: 'POST', body },
    ),

  misconceptionStep: (
    goalId: string,
    body: {
      session_id: string;
      node_id: string;
      claim: string;
      step: MisconceptionStep;
      learner_response: string;
    },
  ) =>
    request<MisconceptionStepResponse>(`/goals/${enc(goalId)}/misconception/step`, {
      method: 'POST',
      body,
    }),

  dispute: (
    goalId: string,
    body: { type: DisputeType; node_id: string; item_id?: string; note: string },
  ) => request<DisputeResponse>(`/goals/${enc(goalId)}/dispute`, { method: 'POST', body }),

  endSession: (goalId: string, body: { session_id: string; summary?: string }) =>
    request<SessionEndResponse>(`/goals/${enc(goalId)}/session/end`, { method: 'POST', body }),

  /* ---------------------------------------------------------------- views */

  map: (goalId: string) =>
    request<MapResponse>(`/goals/${enc(goalId)}/map`).then((r) => ({
      ...r,
      curriculum: {
        ...r.curriculum,
        nodes: normNodes(r.curriculum?.nodes),
        edges: normEdges(r.curriculum?.edges),
      },
    })),

  receipts: (goalId: string, nodeId: string) =>
    request<ReceiptsResponse>(`/goals/${enc(goalId)}/receipts/${enc(nodeId)}`),

  metrics: (goalId: string) => request<MetricsResponse>(`/goals/${enc(goalId)}/metrics`),

  render: (mermaid: string, theme?: 'default' | 'dark') =>
    request<RenderResponse>('/render', { method: 'POST', body: { mermaid, theme } }),

  /* ---------------------------------------------------------------- study tools
   * No model behind any of these. Keys never come back before an attempt: practice is
   * graded server-side and a card's back is its own `reveal` call. */

  study: (goalId: string) => request<StudyResponse>(`/goals/${enc(goalId)}/study`),

  /** Upload: multipart `file` (+ optional `key_file`), `what` repeated, `dry_run` "1"|"0". */
  importStudyUpload: (
    goalId: string,
    opts: { file: File; keyFile?: File | null; what: ImportKind[]; dryRun: boolean },
  ) => {
    const form = new FormData();
    form.append('file', opts.file, opts.file.name);
    if (opts.keyFile) form.append('key_file', opts.keyFile, opts.keyFile.name);
    for (const w of opts.what) form.append('what', w);
    form.append('dry_run', opts.dryRun ? '1' : '0');
    return request<ImportReport>(`/goals/${enc(goalId)}/study/import`, { method: 'POST', form });
  },

  /** A file already in the goal's sources dir; paths are relative to that dir. */
  importStudyPath: (
    goalId: string,
    body: { path: string; key_path?: string; what: ImportKind[]; dry_run: boolean },
  ) => request<ImportReport>(`/goals/${enc(goalId)}/study/import`, { method: 'POST', body }),

  /** `focus`: "" (mixed, by exam weight), an area code ("3") or a concept ref/id ("3.2"). */
  practiceNext: (goalId: string, n = 1, focus = '') =>
    request<PracticeNextResponse>(
      `/goals/${enc(goalId)}/practice/next?n=${n}${focus ? `&focus=${enc(focus)}` : ''}`,
    ),

  /** `order` is the question's shown option order, echoed back so the key grades what was on screen. */
  practiceAnswer: (
    goalId: string,
    body: {
      item_id: string;
      response: string;
      order?: number[];
      confidence?: number;
      idk?: boolean;
    },
  ) =>
    request<PracticeAnswerResponse>(`/goals/${enc(goalId)}/practice/answer`, {
      method: 'POST',
      body,
    }),

  practiceReview: (goalId: string) =>
    request<PracticeReviewResponse>(`/goals/${enc(goalId)}/practice/review`),

  cardsNext: (goalId: string, n = 1) =>
    request<CardsNextResponse>(`/goals/${enc(goalId)}/cards/next?n=${n}`),

  cardReveal: (goalId: string, itemId: string) =>
    request<CardRevealResponse>(`/goals/${enc(goalId)}/cards/reveal`, {
      method: 'POST',
      body: { item_id: itemId },
    }),

  cardReview: (goalId: string, itemId: string, rating: CardRating) =>
    request<CardReviewResponse>(`/goals/${enc(goalId)}/cards/review`, {
      method: 'POST',
      body: { item_id: itemId, rating },
    }),

  /** Anki-importable text (front, back, tags). A link, so it also works with no JS. */
  cardsExportUrl: (
    goalId: string,
    format: 'tsv' | 'csv' = 'tsv',
    include: 'cards' | 'questions' | 'all' = 'cards',
  ) => `${GATEWAY_BASE}/goals/${enc(goalId)}/cards/export?format=${format}&include=${include}`,

  /** Same URL through fetch, so a failure shows inline instead of as a raw error page. */
  async downloadCardsExport(
    goalId: string,
    format: 'tsv' | 'csv' = 'tsv',
    include: 'cards' | 'questions' | 'all' = 'cards',
  ): Promise<{ blob: Blob; filename: string }> {
    await ensureMockGateway();
    const res = await fetch(api.cardsExportUrl(goalId, format, include), {
      headers: { Accept: '*/*' },
    });
    if (!res.ok) throw await toApiError(res);
    const cd = res.headers.get('Content-Disposition') ?? '';
    const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
    return { blob: await res.blob(), filename: m ? decodeURIComponent(m[1]) : `${goalId}-cards.${format}` };
  },

  listTables: (goalId: string) =>
    request<{ tables: TableSummary[] }>(`/goals/${enc(goalId)}/tables`),

  getTable: (goalId: string, tableId: string) =>
    request<StudyTable>(`/goals/${enc(goalId)}/tables/${enc(tableId)}`),

  tableCards: (goalId: string, tableId: string) =>
    request<ImportCount>(`/goals/${enc(goalId)}/tables/${enc(tableId)}/cards`, {
      method: 'POST',
      body: {},
    }),

  /* ---------------------------------------------------------------- exam prep
   * CONTRACTS.md "Exam blueprint, mixed practice and sealed mock exams". A mock gives no
   * feedback of any kind while it is open: GET on an open session returns questions only. */

  /** 404 (`ApiError.status`) when the goal has no blueprint. */
  blueprint: (goalId: string) => request<Blueprint>(`/goals/${enc(goalId)}/blueprint`),

  progress: (goalId: string) => request<Progress>(`/goals/${enc(goalId)}/progress`),

  listMocks: (goalId: string) => request<MocksResponse>(`/goals/${enc(goalId)}/mocks`),

  /** 409 when a mock is already open. Both fields default server-side. */
  startMock: (goalId: string, body: { n?: number; minutes?: number } = {}) =>
    request<MockOpen>(`/goals/${enc(goalId)}/mocks`, { method: 'POST', body }),

  /** An open session (questions, no keys) or a submitted one (the graded result). */
  getMock: (goalId: string, sessionId: string) =>
    request<MockSession>(`/goals/${enc(goalId)}/mocks/${enc(sessionId)}`),

  /** 409 when already submitted. An unanswered item is sent as `response: null` (recorded as idk). */
  submitMock: (goalId: string, sessionId: string, answers: MockAnswer[]) =>
    request<MockResult>(`/goals/${enc(goalId)}/mocks/${enc(sessionId)}/submit`, {
      method: 'POST',
      body: { answers },
    }),

  /** The passport is a binary download, so it bypasses the JSON envelope. */
  passportUrl: () => `${GATEWAY_BASE}/passport`,

  async downloadPassport(): Promise<Blob> {
    await ensureMockGateway();
    const res = await fetch(`${GATEWAY_BASE}/passport`, { headers: { Accept: '*/*' } });
    if (!res.ok) throw await toApiError(res);
    return res.blob();
  },
};
