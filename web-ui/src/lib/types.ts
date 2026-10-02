/**
 * Types for the Stage 2 gateway contract.
 *
 * Source of truth: ../../CONTRACTS.md, "Gateway HTTP (Stage 2)", "Gateway response
 * shapes (pinned 2026-09-05)" and "Study tools (added 2026-09-24)". Nothing here may drift
 * from that file; if a shape needs to change, change CONTRACTS.md first.
 */

/* ------------------------------------------------------------------ vocabulary */

export type Depth = 'recognize' | 'explain' | 'apply' | 'analyze';
export type Domain = 'math-cs' | 'empirical' | 'procedural';
export type SourcePriority = 'alignment' | 'authority';
export type SourceRole = 'alignment' | 'authority' | 'learner';
export type Phase = 'grounding' | 'plan' | 'probe' | 'teach' | 'done';

/** The four colours of the learner map. */
export type NodeStateName = 'unknown' | 'fragile' | 'known' | 'misconception';

export type EdgeType =
  | 'strict_prerequisite'
  | 'recommended_background'
  | 'course_sequence'
  | 'co_requisite'
  | 'supports'
  | 'misconception_for'
  | 'transfer_related';

export type Provenance = 'course' | 'reference' | 'model' | 'learner_evidence' | 'human';

export type EventKind =
  | 'answer'
  | 'teach_back'
  | 'probe_answer'
  | 'dispute'
  | 'graph_revision'
  | 'session_start'
  | 'session_end'
  | 'misconception_step'
  | 'note';

export type EvaluationMethod = 'host_llm' | 'blind_solver' | 'rubric' | 'human';
export type EventContext = 'in-session' | 'delayed' | 'transfer' | 'probe';
export type Channel = 'claude-code' | 'agent' | 'telegram' | 'web';

/** The six typed disputes (IDEA.md, "Adopted into the design - build later"). */
export type DisputeType =
  | 'I already know this'
  | 'ambiguous question'
  | 'misclick'
  | 'not on my exam'
  | 'this edge is wrong'
  | 'test me instead';

export const DISPUTE_TYPES: DisputeType[] = [
  'I already know this',
  'ambiguous question',
  'misclick',
  'not on my exam',
  'this edge is wrong',
  'test me instead',
];

/** Assistance ladder from skills/teach/prompts/v1/checkpoint.md. Level >= 5 never counts. */
export const HINT_LEVEL_NAMES: Record<number, string> = {
  0: 'none',
  1: 'encouragement',
  2: 'conceptual hint',
  3: 'strategic hint',
  4: 'procedural hint',
  5: 'partial solution',
  6: 'worked solution (the reveal)',
};

export type MisconceptionStep = 'reasoning' | 'prediction' | 'counterexample';
export type MisconceptionState =
  | 'suspected'
  | 'active'
  | 'weakened'
  | 'resolved'
  | 'recurred';

/* ------------------------------------------------------------------ core objects */

export interface GoalContract {
  goal_id?: string;
  title: string;
  concept: string;
  depth: Depth;
  purpose: string;
  deadline?: string | null;
  minutes_per_session: number;
  sessions_per_week?: number | null;
  assessment?: string | null;
  source_priority?: SourcePriority | null;
  target_capabilities?: string[];
  transfer_required?: boolean;
  domain: Domain;
}

export interface Goal extends GoalContract {
  goal_id: string;
  created_at?: string;
}

/** GET /api/goals rows: the goal plus its map counters. */
export interface GoalListRow extends Goal {
  phase: Phase;
  node_count: number;
  known: number;
  fragile: number;
  unknown: number;
  misconception: number;
}

export interface Session {
  session_id: string;
  goal_id: string;
  started_at: string;
  ended_at?: string | null;
  channel: Channel;
  summary?: string | null;
}

export interface GraphNode {
  id: string;
  title: string;
  aliases?: string[];
}

export interface GraphEdge {
  from: string;
  to: string;
  type: EdgeType;
  provenance: Provenance;
}

/**
 * Multidimensional state under the four colours (IDEA.md). The UI shows the counters as
 * evidence rows, never as a probability.
 */
export interface NodeState {
  state: NodeStateName;
  independent_passes: number;
  assisted_passes: number;
  self_graded_passes: number;
  fails: number;
  last_delayed?: string | null;
  transfer_passes: number;
  uncertainty: 'low' | 'medium' | 'high';
  review_priority?: number;
  /* learner-svc sends these alongside the pinned fields; the map tooltip uses them and
     nothing breaks when a gateway build leaves them out. */
  reasons?: string[];
  due_items?: number;
  active_misconception?: string | null;
  unearned_passes?: number;
}

export interface QuestionOption {
  key: string;
  text: string;
}

export interface Question {
  item_id: string;
  item_version_id: string;
  node_id: string;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  allow_idk: boolean;
  ask_confidence: boolean;
  kind: string;
  assistance_level: number;
}

export interface LearnerEvent {
  event_id: string;
  ts: string;
  session_id: string;
  goal_id: string;
  node_id: string;
  item_version_id?: string | null;
  kind: EventKind;
  response?: string | null;
  correct?: 0 | 1 | null;
  confidence?: number | null;
  idk: 0 | 1;
  assistance_level: number;
  channel: Channel;
  context: EventContext;
  prompt_version?: string;
  grader_version?: string;
  evaluation_method?: EvaluationMethod;
  payload?: Record<string, unknown> | null;
}

export interface Source {
  source_id: string;
  filename: string;
  role: SourceRole;
  pages?: number | null;
  bytes?: number | null;
  url?: string | null;
  ingested_at?: string | null;
}

export interface ProposedSource {
  title: string;
  url: string;
  role: SourceRole;
  why: string;
}

export interface Misconception {
  misconception_id: string;
  node_id: string;
  claim: string;
  state: MisconceptionState;
  steps_held: MisconceptionStep[];
  opened_at?: string;
}

export interface Dispute {
  dispute_id: string;
  node_id: string;
  item_id?: string | null;
  type: DisputeType;
  note: string;
  outcome?: 'upheld' | 'rejected' | null;
  opened_at?: string;
  evidence?: string | null;
}

/* ------------------------------------------------------------------ responses */

export interface CreateGoalResponse {
  goal: Goal;
  sources_dir: string;
  phase: Phase;
}

export interface GoalsResponse {
  goals: GoalListRow[];
}

export interface GoalDetailResponse {
  goal: Goal;
  phase: Phase;
  session: Session | null;
  /** The generated learner.md projection. Evidence, never decimals. */
  summary_md: string;
}

export interface SourcesResponse {
  sources: Source[];
  sources_md?: string | null;
}

export interface ResearchResponse {
  proposal_id: string;
  sources: ProposedSource[];
}

export interface ResearchApproveResponse {
  approved: number;
}

/** A citation is a plain string (mock, legacy) or the gateway's cite_or_abstain object. */
export type Citation =
  | string
  | {
      title?: string | null;
      locator?: Record<string, unknown> | null;
      quote?: string | null;
      role?: string | null;
      proves?: string | null;
    };

export function formatCitation(c: Citation): string {
  if (typeof c === 'string') return c;
  const loc = c.locator
    ? Object.entries(c.locator)
        .map(([k, v]) => `${k} ${String(v)}`)
        .join(', ')
    : '';
  const proves = c.proves ? ` (proves ${c.proves})` : '';
  return `${c.title ?? 'source'}${loc ? ` — ${loc}` : ''}${proves}`;
}

export interface VerificationRow {
  node_id: string;
  status: 'cited' | 'abstain';
  citations: Citation[];
}

export interface Feasibility {
  sessions_needed: number;
  sessions_available: number | null;
  verdict: 'comfortable' | 'tight' | 'not-feasible' | 'unknown';
  assumption: string;
}

export interface PlanResponse {
  graph_version: string;
  mermaid: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  course_prior_used: boolean;
  verification: VerificationRow[];
  /** Optional: the gateway may compute the feasibility line itself. */
  feasibility?: Feasibility | null;
}

export interface PlanApproveResponse {
  graph_version: string;
}

export interface ProbeStartResponse {
  session_id: string;
  budget: number;
  question: Question | null;
  done: boolean;
  asked?: number;
}

export interface ProbeAnswerResponse {
  recorded: LearnerEvent;
  node_state: NodeState;
  next: Question | null;
  done: boolean;
  asked: number;
  budget: number;
}

export interface TeachStep {
  strategy: string;
  markdown: string;
  mermaid?: string | null;
  svg?: string | null;
  latex_ok: boolean;
  citations: Citation[];
}

export interface TeachNextResponse {
  node: GraphNode;
  step: TeachStep;
  checkpoint: Question;
  assistance_level: number;
  /** Not in the pinned shape; tolerated when a gateway sends it. */
  done?: boolean;
}

export interface HintResponse {
  level: number;
  hint_markdown: string;
}

export type TeachDecision =
  | 'continue'
  | 'repeat'
  | 'back_up'
  | 'switch_strategy'
  | 'teach_back_due'
  | 'end_session';

export interface TeachAnswerResponse {
  correct: boolean;
  recorded: LearnerEvent;
  node_state: NodeState;
  feedback_markdown: string;
  reveal_allowed: boolean;
  misconception_suspected: { claim: string } | null;
  decision: TeachDecision;
}

export interface TeachBackResponse {
  score: 0 | 1 | 2 | 3;
  rubric_version: string;
  feedback_markdown: string;
  recorded: LearnerEvent;
}

export interface MisconceptionStepResponse {
  state: MisconceptionState;
  next_step: MisconceptionStep | null;
  /** Prompt for the next step, when the gateway supplies one. */
  prompt_markdown?: string | null;
}

export interface DisputeResponse {
  dispute_id: string;
  check: { items: Question[] } | null;
}

export interface SessionEndResponse {
  session: Session;
  log_path: string;
}

export interface MapResponse {
  curriculum: { mermaid: string; nodes: GraphNode[]; edges: GraphEdge[] };
  path: { mermaid: string; order: string[] };
  states: Record<string, NodeState>;
}

export interface ReceiptsResponse {
  node: GraphNode;
  state: NodeState;
  evidence: LearnerEvent[];
  /** Plain-language reasons for the current colour. */
  why: string[];
  misconceptions: Misconception[];
  disputes: Dispute[];
}

export interface MetricsResponse {
  [key: string]: unknown;
  holdout_success_7d?: number | null;
  false_mastery_rate?: number | null;
  item_rejection_rate?: number | null;
  sessions?: number;
  mean_assistance?: number | null;
}

export interface RenderResponse {
  svg: string;
  warnings?: string[];
}

export interface HealthResponse {
  ok: boolean;
  services: Record<string, 'ok' | 'down' | 'unconfigured' | 'degraded'>;
}

/** Error body for every non-2xx response. */
export interface ApiErrorBody {
  error: string;
  code: string;
}

/* ------------------------------------------------------------------ study tools
 * CONTRACTS.md "Study tools (added 2026-09-24)", "Gateway additions". Nothing here needs a
 * model: practice and cards are graded or scheduled by learner-svc, tables are data. */

/** PATCH /api/goals/{g}. Absent = unchanged; `deadline: ""` clears it. */
export interface GoalPatch {
  deadline?: string;
  minutes_per_session?: number;
  sessions_per_week?: number;
  title?: string;
  purpose?: string;
  assessment?: string;
  depth?: Depth;
}

export interface GoalPatchResponse {
  goal: Goal;
  changed: Record<string, { from: unknown; to: unknown }>;
  /** The stored plan's feasibility, recomputed; null when no plan carries one. */
  feasibility: Feasibility | null;
}

export interface BankCounts {
  total: number;
  checked: number;
  unchecked: number;
  rejected: number;
  due_now: number;
  new_available: number;
  new_today: number;
  new_limit: number;
  /** Sealed mock questions not yet used in a mock; never included in `total` (2026-09-26). */
  sealed?: number;
}

export interface CardCounts {
  total: number;
  due_now: number;
  new_available: number;
  new_today: number;
  new_limit: number;
}

export interface TableSummary {
  table_id: string;
  title: string;
  node_id: string | null;
  node_title: string | null;
  columns: string[];
  row_count: number;
  source: string | null;
  author: string;
  created_at: string;
}

export interface StudyTable extends TableSummary {
  rows: string[][];
}

/** A markdown file already in the goal's sources dir; `path` is relative to it. */
export interface ImportableFile {
  path: string;
  name: string;
  bytes: number;
}

export interface StudyResponse {
  bank: BankCounts;
  cards: CardCounts;
  tables: TableSummary[];
  importable: ImportableFile[];
  /** Exam-prep additions (2026-09-26). Optional only so an older gateway build still renders. */
  blueprint?: boolean;
  mock?: { sealed_available: number; open: string | null };
}

export type ImportKind = 'questions' | 'cards' | 'tables';

export interface ImportCount {
  parsed: number;
  imported: number;
  skipped_existing: number;
}

export interface ImportReport {
  source: string;
  dry_run: boolean;
  questions: (ImportCount & { problems: string[] }) | null;
  cards: ImportCount | null;
  tables: ImportCount | null;
  nodes_created: { node_id: string; title: string }[];
}

/** A bank question as served: no key, no explanation - those come back only after an attempt. */
export interface PracticeQuestion {
  item_id: string;
  item_version_id: string;
  node_id: string;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  allow_idk: boolean;
  /** False until a blind solver that is not the author has agreed with the key. */
  checked: boolean;
  reason: 'due' | 'new';
  context: 'delayed' | 'in-session';
  /**
   * The stored option indexes in the order shown (A = order[0]). Sent back with the answer so
   * the key is graded against what was on screen. Optional only for older gateway builds.
   */
  order?: number[];
  /** The blueprint row this question's concept belongs to (e.g. "3.2"); null without one. */
  ref?: string | null;
  area?: { code: string; title: string } | null;
}

export type FocusKind = 'mixed' | 'area' | 'node';

export interface PracticeNextResponse {
  questions: PracticeQuestion[];
  counts: BankCounts & { focus?: { kind: FocusKind; label: string } };
  done: boolean;
  note: string | null;
}

export interface PracticeAnswerResponse {
  item_id: string;
  correct: boolean;
  idk: boolean;
  your_answer: QuestionOption | null;
  correct_answer: QuestionOption;
  explanation: string | null;
  checked: boolean;
  counts_toward_mastery: boolean;
  context: string;
  node_state: NodeState;
  schedule: { due: string; rating: string } | null;
  note: string | null;
}

/** A question a blind solver disagreed with. Keys included: the learner judges it. */
export interface ReviewItem {
  item_id: string;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  answer: QuestionOption;
  solver_answer: QuestionOption | null;
  ambiguous: boolean;
  notes: string | null;
  explanation: string | null;
}

export interface PracticeReviewResponse {
  items: ReviewItem[];
}

/** A flashcard as served: the front only. The back is a separate `reveal` call. */
export interface StudyCard {
  item_id: string;
  node_id: string;
  node_title: string;
  front: string;
  reason: 'due' | 'new';
}

export interface CardsNextResponse {
  cards: StudyCard[];
  counts: CardCounts;
  done: boolean;
}

export interface CardRevealResponse {
  item_id: string;
  front: string;
  back: string;
  source: string | null;
}

export type CardRating = 'again' | 'hard' | 'good' | 'easy';

export const CARD_RATINGS: CardRating[] = ['again', 'hard', 'good', 'easy'];

/** Self-report: schedules FSRS, never evidence - `counts_toward_mastery` is always false. */
export interface CardReviewResponse {
  item_id: string;
  rating: CardRating;
  schedule: { due: string; state: string };
  counts_toward_mastery: false;
  note: string | null;
}

/* ------------------------------------------------------------------ exam prep
 * CONTRACTS.md "Exam blueprint, mixed practice and sealed mock exams (added 2026-09-26)".
 * Still no model anywhere. Percentages below are count ratios (first-try accuracy, share of
 * the exam) and always travel with their counts; nothing here is a mastery probability. */

export interface BlueprintSubarea {
  ref: string;
  title: string;
  node_id: string | null;
  node_title: string | null;
  exam_items: number;
  /** Share of the exam's total items, 0..1. */
  share: number;
}

export interface BlueprintArea {
  code: string;
  title: string;
  exam_items: number;
  /** Share of the exam's total items, 0..1. */
  share: number;
  subareas: BlueprintSubarea[];
}

/** GET /api/goals/{g}/blueprint - 404 when the goal has none. */
export interface Blueprint {
  goal_id: string;
  exam: string;
  source: string;
  total_items: number;
  areas: BlueprintArea[];
}

/**
 * Counts over a set of items. `low`/`high` are the 95% Wilson range of first-try accuracy in
 * percent (null with no first attempt).
 */
export interface Tally {
  bank: number;
  checked: number;
  sealed: number;
  seen: number;
  first_attempts: number;
  first_correct: number;
  low: number | null;
  high: number | null;
  attempts: number;
  correct: number;
  due_now: number;
}

export interface NodeProgress extends Tally {
  ref: string | null;
  node_id: string;
  title: string;
  exam_items: number | null;
  share: number | null;
  state: NodeStateName;
}

export interface AreaProgress extends Tally {
  code: string;
  title: string;
  exam_items: number;
  share: number;
  subareas: NodeProgress[];
}

export interface MockSummary {
  session_id: string;
  started_at: string;
  submitted_at: string | null;
  n: number;
  answered: number | null;
  correct: number | null;
  minutes: number;
  minutes_used: number | null;
  /** Per-area score; null while the mock is open. */
  areas: { code: string; n: number; correct: number }[] | null;
}

/** GET /api/goals/{g}/progress */
export interface Progress {
  goal_id: string;
  today: string;
  deadline: string | null;
  days_left: number | null;
  has_blueprint: boolean;
  totals: Tally;
  disciplinar: {
    /** Blueprint-weighted first-try accuracy, percent; null until every area has >= 5 first attempts. */
    weighted_accuracy: number | null;
    /** Share of exam weight (0..1) with at least one first attempt. */
    coverage: number;
    note: string | null;
  };
  areas: AreaProgress[];
  unassigned: NodeProgress[];
  /** The last 28 local days, oldest first. */
  activity: { date: string; answers: number; correct: number; cards: number }[];
  /** Questions + cards coming due per local day, today..deadline (<= 60 days). */
  forecast: { date: string; due: number }[];
  mocks: MockSummary[];
}

export interface MocksResponse {
  sealed_available: number;
  /** Sealed questions still waiting for a blind check (not drawable yet). */
  sealed_unchecked: number;
  sealed_by_area: { code: string; title: string; available: number }[];
  open: MockSummary | null;
  mocks: MockSummary[];
}

export interface MockQuestion {
  item_id: string;
  ref: string | null;
  area: { code: string; title: string } | null;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  order: number[];
}

export interface MockOpen {
  session_id: string;
  status: 'open';
  started_at: string;
  minutes: number;
  ends_at: string;
  n: number;
  questions: MockQuestion[];
}

export interface MockResultItem {
  item_id: string;
  ref: string | null;
  area_code: string | null;
  node_title: string;
  stem: string;
  options: QuestionOption[];
  your_answer: QuestionOption | null;
  correct_answer: QuestionOption;
  correct: boolean;
  explanation: string | null;
}

export interface MockResult {
  session_id: string;
  status: 'submitted';
  started_at: string;
  submitted_at: string;
  minutes: number;
  minutes_used: number;
  overtime: boolean;
  n: number;
  answered: number;
  correct: number;
  areas: {
    code: string;
    title: string;
    n: number;
    correct: number;
    subareas: { ref: string; title: string; n: number; correct: number }[];
  }[];
  items: MockResultItem[];
}

export type MockSession = MockOpen | MockResult;

export interface MockAnswer {
  item_id: string;
  response: string | null;
  order?: number[];
  confidence?: number;
}
