/**
 * The fake gateway's study state and its exam-prep engine (mock chunk only: imported by
 * ./gateway.ts and nothing else).
 *
 * What it models, from CONTRACTS.md "Exam blueprint, mixed practice and sealed mock exams":
 *
 * - a per-goal study record (bank, cards, tables) with real timestamps, persisted to
 *   sessionStorage so a reload - in particular a reload in the middle of a mock - resumes
 *   instead of resetting (the teaching flow in ./gateway.ts still resets on reload);
 * - mixed practice: due first, then new picks to the concept furthest below its blueprint
 *   share of the questions seen so far (equal shares without a blueprint), narrowed by
 *   `focus`;
 * - shuffled options: every serve carries `order` (a per-item, per-local-day shuffle), keys
 *   are letters of that order, and grading maps the letter back through the order it gets;
 * - sealed mocks: a blueprint-weighted draw, no feedback while open, graded on submit, the
 *   answers recorded as ordinary attempts, and the items then joining practice rotation;
 * - progress computed only from attempts recorded here, so the charts move as you practise.
 */

import { shuffled } from '@/lib/study';
import type {
  AreaProgress,
  BankCounts,
  Blueprint,
  CardCounts,
  MockOpen,
  MockResult,
  MockSummary,
  MocksResponse,
  NodeProgress,
  NodeState,
  NodeStateName,
  PracticeQuestion,
  Progress,
  QuestionOption,
  Tally,
} from '@/lib/types';
import {
  CARDS,
  IMPORTABLE,
  PRACTICE,
  TABLES,
  type MockCard,
  type MockPractice,
  type MockTable,
} from './fixtures';
import {
  ISOFT_AREAS,
  ISOFT_CARDS,
  ISOFT_GOAL,
  ISOFT_TABLES,
  ISOFT_TOTAL_ITEMS,
  isoftBank,
  isoftNodeId,
} from './isoft';

export const HOUR = 3_600_000;
const DAY = 24 * HOUR;
export const PRACTICE_NEW_PER_DAY = 20; // LT_PRACTICE_NEW_PER_DAY
export const CARDS_NEW_PER_DAY = 20; // LT_CARDS_NEW_PER_DAY
export const DELAYED_MIN_HOURS = 20; // LT_DELAYED_MIN_HOURS
const MOCK_MINUTES_PER_ITEM = 2.9; // LT_MOCK_MINUTES_PER_ITEM (420 min / 143 items)
const MOCK_DEFAULT_MAX = 60;
const LETTERS = ['A', 'B', 'C', 'D', 'E', 'F'];

/* ------------------------------------------------------------------ records */

export interface StudyItem extends MockPractice {
  /** ms timestamp; null = new, never answered. */
  due: number | null;
  last: number | null;
}

export interface StudyCardRec extends MockCard {
  due: number | null;
  state: string;
}

export interface Attempt {
  item_id: string;
  node_id: string;
  ts: number;
  correct: boolean;
  idk: boolean;
  /** The item's first attempt ever (practice or mock): what first-try accuracy counts. */
  first: boolean;
  /** `payload.mock` - the session id when answered in a mock. */
  mock: string | null;
  /** `payload.shown_order`. */
  order: number[];
  context: 'delayed' | 'in-session';
}

export interface MockRec {
  session_id: string;
  started_at: number;
  minutes: number;
  n: number;
  items: { item_id: string; order: number[] }[];
  submitted_at: number | null;
  answers: { item_id: string; response: string | null; chosen: number | null; correct: boolean }[];
}

export interface StudyRecord {
  practice: StudyItem[];
  cards: StudyCardRec[];
  tables: MockTable[];
  importable: Record<string, string>;
  /** import tag -> node, so a re-import reuses the node it created. */
  nodes: Map<string, { node_id: string; title: string }>;
  newPracticeToday: number;
  newCardsToday: number;
  /** The local day the two "today" counters belong to. */
  day: string;
  attempts: Attempt[];
  /** Timestamps of card reviews (self-report; activity only, never evidence). */
  cardLog: number[];
  mocks: MockRec[];
  blueprint: Blueprint | null;
}

export class FakeError extends Error {
  constructor(
    message: string,
    readonly code: string,
    readonly status: number,
  ) {
    super(message);
  }
}

/* ------------------------------------------------------------------ small helpers */

/** YYYY-MM-DD in the browser's time zone (the real service uses the process's TZ). */
export function localDay(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function dayStart(day: string): number {
  const [y, m, d] = day.split('-').map(Number);
  return new Date(y, m - 1, d).getTime();
}

function addDays(day: string, n: number): string {
  const [y, m, d] = day.split('-').map(Number);
  return localDay(new Date(y, m - 1, d + n, 12).getTime());
}

function daysBetween(from: string, to: string): number {
  return Math.round((dayStart(to) - dayStart(from)) / DAY);
}

/** FNV-1a, for stable per-item seeds. */
function hash(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i += 1) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** 95% Wilson score interval, in whole percent. */
export function wilson(k: number, n: number): { low: number | null; high: number | null } {
  if (n <= 0) return { low: null, high: null };
  const z = 1.96;
  const p = k / n;
  const den = 1 + (z * z) / n;
  const mid = (p + (z * z) / (2 * n)) / den;
  const half = (z * Math.sqrt((p * (1 - p)) / n + (z * z) / (4 * n * n))) / den;
  return {
    low: Math.max(0, Math.round((mid - half) * 100)),
    high: Math.min(100, Math.round((mid + half) * 100)),
  };
}

function answerIndex(p: MockPractice): number {
  return Math.max(
    0,
    p.options.findIndex((o) => o.key === p.answer),
  );
}

/** The per-item, per-local-day shuffle the server would serve today. */
function dayOrder(p: MockPractice, now: number): number[] {
  return shuffled(
    p.options.map((_, i) => i),
    hash(`${p.item_id}|${localDay(now)}`),
  );
}

function shownOptions(p: MockPractice, order: number[]): QuestionOption[] {
  return order.map((si, i) => ({ key: LETTERS[i], text: p.options[si].text }));
}

function validOrder(p: MockPractice, raw: unknown): number[] | null {
  if (!Array.isArray(raw)) return null;
  const n = p.options.length;
  const o = raw.map(Number);
  if (o.length !== n || !o.every((x) => Number.isInteger(x) && x >= 0 && x < n)) return null;
  return new Set(o).size === n ? o : null;
}

function isSealed(p: MockPractice): boolean {
  return p.pool === 'mock' && !p.mocked;
}

/** Servable by practice: not held back by a failed blind check, not still sealed. */
export function servable(p: StudyItem): boolean {
  return p.check !== 'rejected' && !isSealed(p);
}

export function contextOf(p: StudyItem, now = Date.now()): 'delayed' | 'in-session' {
  return p.last !== null && now - p.last >= DELAYED_MIN_HOURS * HOUR ? 'delayed' : 'in-session';
}

/* ------------------------------------------------------------------ build + persist */

function isoftBlueprint(goalId: string): Blueprint {
  return {
    goal_id: goalId,
    exam: 'EGEL Plus ISOFT - Sección Disciplinar',
    source: 'Guía para el sustentante EGEL Plus ISOFT, Ceneval, julio 2024, pp. 10-11',
    total_items: ISOFT_TOTAL_ITEMS,
    areas: ISOFT_AREAS.map((a) => {
      const items = a.subareas.reduce((n, s) => n + s.items, 0);
      return {
        code: a.code,
        title: a.title,
        exam_items: items,
        share: items / ISOFT_TOTAL_ITEMS,
        subareas: a.subareas.map((s) => ({
          ref: s.ref,
          title: s.title,
          node_id: isoftNodeId(s.ref),
          node_title: s.title,
          exam_items: s.items,
          share: s.items / ISOFT_TOTAL_ITEMS,
        })),
      };
    }),
  };
}

/**
 * Two weeks of history on part of the bank, so the charts have something to show on first
 * load: area 2 and most of area 4 are untouched on purpose ("no data yet" must be visible,
 * and mixed practice then has somewhere to pull toward).
 */
function seedIsoftHistory(s: StudyRecord, now: number): void {
  const r = rng(424242);
  const today = localDay(now);
  const byRef = new Map<string, StudyItem[]>();
  for (const p of s.practice) {
    if (p.pool !== 'practice' || !p.ref) continue;
    byRef.set(p.ref, [...(byRef.get(p.ref) ?? []), p]);
  }
  const plan: Array<[string, number, number]> = [
    // ref, how many of its 3 practice items were tried, first-try hit rate
    ['1.1', 2, 0.8],
    ['1.2', 2, 0.7],
    ['1.3', 2, 0.75],
    ['3.1', 3, 0.7],
    ['3.2', 3, 0.6],
    ['3.3', 2, 0.8],
    ['3.4', 3, 0.5],
    ['3.5', 2, 0.7],
    ['4.3', 2, 0.8],
  ];
  let slot = 0;
  for (const [ref, k, hit] of plan) {
    for (const p of (byRef.get(ref) ?? []).slice(0, k)) {
      const daysAgo = 13 - (slot % 13);
      slot += 1;
      const ts = dayStart(addDays(today, -daysAgo)) + (19 + r() * 3) * HOUR;
      const correct = r() < hit;
      const order = shuffled([0, 1, 2], hash(`${p.item_id}|seed`));
      s.attempts.push({ item_id: p.item_id, node_id: p.node_id, ts, correct, idk: false, first: true, mock: null, order, context: 'in-session' });
      let last = ts;
      let lastCorrect = correct;
      if (correct && daysAgo >= 4) {
        const ts2 = ts + 3 * DAY + r() * 2 * HOUR;
        lastCorrect = r() < 0.85;
        s.attempts.push({ item_id: p.item_id, node_id: p.node_id, ts: ts2, correct: lastCorrect, idk: false, first: false, mock: null, order, context: 'delayed' });
        last = ts2;
      }
      p.last = last;
      p.due = last + (lastCorrect ? 72 : 0.25) * HOUR;
    }
  }
  s.attempts.sort((a, b) => a.ts - b.ts);
  for (let d = 13; d >= 1; d -= 1) {
    if (r() < 0.4) continue;
    const n = 1 + Math.floor(r() * 5);
    for (let i = 0; i < n; i += 1) s.cardLog.push(dayStart(addDays(today, -d)) + (20 + r()) * HOUR);
  }
}

function freshRecord(goalId: string, now: number): StudyRecord {
  const isoft = goalId === ISOFT_GOAL.goal_id;
  const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;
  const practice: StudyItem[] = (isoft ? isoftBank() : PRACTICE).map((p) => ({
    ...clone(p),
    due: p.due_in_hours === undefined ? null : now + p.due_in_hours * HOUR,
    last: p.answered_hours_ago === undefined ? null : now - p.answered_hours_ago * HOUR,
  }));
  const cards: StudyCardRec[] = (isoft ? ISOFT_CARDS : CARDS).map((c) => ({
    ...clone(c),
    due: c.due_in_hours === undefined ? null : now + c.due_in_hours * HOUR,
    state: c.due_in_hours === undefined ? 'new' : 'review',
  }));
  const s: StudyRecord = {
    practice,
    cards,
    tables: clone(isoft ? ISOFT_TABLES : TABLES),
    importable: isoft ? {} : { ...IMPORTABLE },
    nodes: new Map(),
    newPracticeToday: isoft ? 0 : 2,
    newCardsToday: 0,
    day: localDay(now),
    attempts: [],
    cardLog: [],
    mocks: [],
    blueprint: isoft ? isoftBlueprint(goalId) : null,
  };
  if (isoft) seedIsoftHistory(s, now);
  return s;
}

const studies = new Map<string, StudyRecord>();
const STORE_KEY = (goalId: string) => `lt-fake-study:${goalId}`;

export function studyFor(goalId: string): StudyRecord {
  const now = Date.now();
  let s = studies.get(goalId);
  if (!s) {
    try {
      const raw = window.sessionStorage.getItem(STORE_KEY(goalId));
      if (raw) {
        const parsed = JSON.parse(raw) as Omit<StudyRecord, 'nodes'> & {
          nodes: [string, { node_id: string; title: string }][];
        };
        s = { ...parsed, nodes: new Map(parsed.nodes) };
      }
    } catch {
      s = undefined; // storage off or a stale shape: start from the seed
    }
    s ??= freshRecord(goalId, now);
    studies.set(goalId, s);
  }
  const today = localDay(now);
  if (s.day !== today) {
    s.day = today;
    s.newPracticeToday = 0;
    s.newCardsToday = 0;
  }
  return s;
}

/** Called after every write, so a reload resumes (mid-mock included). */
export function persistStudy(goalId: string): void {
  const s = studies.get(goalId);
  if (!s) return;
  try {
    window.sessionStorage.setItem(
      STORE_KEY(goalId),
      JSON.stringify({ ...s, nodes: Array.from(s.nodes.entries()) }),
    );
  } catch {
    /* storage off: state lives for this page view only */
  }
}

/* ------------------------------------------------------------------ counts */

/** Sealed items are counted apart (`sealed`) and never in `total` or the check counts. */
export function bankCounts(s: StudyRecord): BankCounts {
  const t = Date.now();
  const bank = s.practice.filter((p) => !isSealed(p));
  const live = s.practice.filter(servable);
  return {
    total: bank.length,
    checked: bank.filter((p) => p.check === 'checked').length,
    unchecked: bank.filter((p) => p.check === 'unchecked').length,
    rejected: bank.filter((p) => p.check === 'rejected').length,
    due_now: live.filter((p) => p.due !== null && p.due <= t).length,
    new_available: live.filter((p) => p.due === null).length,
    new_today: s.newPracticeToday,
    new_limit: PRACTICE_NEW_PER_DAY,
    sealed: s.practice.filter(isSealed).length,
  };
}

export function cardCounts(s: StudyRecord): CardCounts {
  const t = Date.now();
  return {
    total: s.cards.length,
    due_now: s.cards.filter((c) => c.due !== null && c.due <= t).length,
    new_available: s.cards.filter((c) => c.due === null).length,
    new_today: s.newCardsToday,
    new_limit: CARDS_NEW_PER_DAY,
  };
}

function sealedAvailable(s: StudyRecord): StudyItem[] {
  return s.practice.filter((p) => isSealed(p) && p.check === 'checked');
}

export function openMock(s: StudyRecord): MockRec | null {
  return s.mocks.find((m) => m.submitted_at === null) ?? null;
}

export function mockFlags(s: StudyRecord): { sealed_available: number; open: string | null } {
  return { sealed_available: sealedAvailable(s).length, open: openMock(s)?.session_id ?? null };
}

/* ------------------------------------------------------------------ practice */

interface Focus {
  kind: 'mixed' | 'area' | 'node';
  label: string;
  match: (p: StudyItem) => boolean;
}

function areaOf(s: StudyRecord, ref: string | undefined): { code: string; title: string } | null {
  if (!ref || !s.blueprint) return null;
  const a = s.blueprint.areas.find((x) => x.subareas.some((sub) => sub.ref === ref));
  return a ? { code: a.code, title: a.title } : null;
}

function resolveFocus(s: StudyRecord, raw: string): Focus {
  const f = raw.trim();
  if (!f) {
    return {
      kind: 'mixed',
      label: s.blueprint ? 'Mixed - every area, by exam weight' : 'Mixed - every concept in turn',
      match: () => true,
    };
  }
  const area = s.blueprint?.areas.find((a) => a.code === f);
  if (area) {
    return {
      kind: 'area',
      label: `${area.code} ${area.title}`,
      match: (p) => area.subareas.some((sub) => sub.ref === p.ref),
    };
  }
  const sub = s.blueprint?.areas.flatMap((a) => a.subareas).find((x) => x.ref === f || x.node_id === f);
  if (sub) {
    return { kind: 'node', label: `${sub.ref} ${sub.title}`, match: (p) => p.ref === sub.ref };
  }
  const node = s.practice.find((p) => p.node_id === f);
  if (node) return { kind: 'node', label: node.node_title, match: (p) => p.node_id === f };
  throw new FakeError(`focus "${f}" is not an area code or a concept of this goal`, 'bad_focus', 400);
}

/** Blueprint share per concept (equal shares without one). */
function sharesOf(s: StudyRecord): Map<string, number> {
  const out = new Map<string, number>();
  if (s.blueprint) {
    for (const a of s.blueprint.areas) for (const sub of a.subareas) out.set(sub.node_id ?? sub.ref, sub.share);
    return out;
  }
  const nodes = Array.from(new Set(s.practice.map((p) => p.node_id)));
  for (const n of nodes) out.set(n, 1 / nodes.length);
  return out;
}

/**
 * New questions go to the concept furthest below its blueprint share of the questions seen
 * so far: deficit = share x (seen_total + 1) - seen_concept, largest first.
 */
function pickNew(s: StudyRecord, candidates: StudyItem[], k: number): StudyItem[] {
  if (k <= 0 || !candidates.length) return [];
  const shares = sharesOf(s);
  const seen = new Map<string, number>();
  const seenIds = new Set(s.attempts.map((a) => a.item_id));
  for (const p of s.practice) {
    if (seenIds.has(p.item_id) || p.last !== null) seen.set(p.node_id, (seen.get(p.node_id) ?? 0) + 1);
  }
  let total = Array.from(seen.values()).reduce((a, b) => a + b, 0);
  const queues = new Map<string, StudyItem[]>();
  for (const p of candidates) queues.set(p.node_id, [...(queues.get(p.node_id) ?? []), p]);
  const order = Array.from(shares.keys());
  const picks: StudyItem[] = [];
  while (picks.length < k) {
    let best: string | null = null;
    let bestScore = -Infinity;
    for (const [node, q] of queues) {
      if (!q.length) continue;
      const score = (shares.get(node) ?? 0) * (total + 1) - (seen.get(node) ?? 0);
      const better =
        score > bestScore + 1e-9 ||
        (Math.abs(score - bestScore) <= 1e-9 && best !== null && order.indexOf(node) < order.indexOf(best));
      if (better) {
        best = node;
        bestScore = score;
      }
    }
    if (best === null) break;
    picks.push(queues.get(best)!.shift()!);
    seen.set(best, (seen.get(best) ?? 0) + 1);
    total += 1;
  }
  return picks;
}

function serve(s: StudyRecord, p: StudyItem, reason: 'due' | 'new', now: number): PracticeQuestion {
  const order = dayOrder(p, now);
  return {
    item_id: p.item_id,
    item_version_id: p.item_version_id,
    node_id: p.node_id,
    node_title: p.node_title,
    stem: p.stem,
    options: shownOptions(p, order),
    allow_idk: true,
    checked: p.check === 'checked',
    reason,
    context: contextOf(p, now),
    order,
    ref: p.ref ?? null,
    area: areaOf(s, p.ref),
  };
}

function nothingDueNote(s: StudyRecord, pool: StudyItem[], focus: Focus): string {
  const t = Date.now();
  const upcoming = pool
    .map((x) => x.due)
    .filter((d): d is number => d !== null && d > t)
    .sort((a, b) => a - b)[0];
  const parts: string[] = [];
  if (focus.kind !== 'mixed') parts.push(`Nothing left in ${focus.label} for now.`);
  if (upcoming) parts.push(`Next review ${new Date(upcoming).toISOString().slice(0, 16).replace('T', ' ')} UTC.`);
  else parts.push('No reviews scheduled.');
  if (s.newPracticeToday >= PRACTICE_NEW_PER_DAY) {
    parts.push(`New questions for today are used up (${s.newPracticeToday} of ${PRACTICE_NEW_PER_DAY}).`);
  }
  return parts.join(' ');
}

export function practiceNext(s: StudyRecord, n: number, focusRaw: string) {
  const now = Date.now();
  const focus = resolveFocus(s, focusRaw);
  const pool = s.practice.filter((p) => servable(p) && focus.match(p));
  const due = pool
    .filter((p) => p.due !== null && p.due <= now)
    .sort((a, b) => (a.due as number) - (b.due as number))
    .slice(0, n);
  const room = Math.max(0, PRACTICE_NEW_PER_DAY - s.newPracticeToday);
  const fresh = pickNew(
    s,
    pool.filter((p) => p.due === null),
    Math.min(room, n - due.length),
  );
  const questions = [
    ...due.map((p) => serve(s, p, 'due', now)),
    ...fresh.map((p) => serve(s, p, 'new', now)),
  ];
  return {
    questions,
    counts: { ...bankCounts(s), focus: { kind: focus.kind, label: focus.label } },
    done: questions.length === 0,
    note: questions.length ? null : nothingDueNote(s, pool, focus),
  };
}

/** Rule-based and transparent, like learner-svc's evidence rules; never a probability. */
export function stateFromAttempts(s: StudyRecord, nodeId: string): NodeState {
  const as = s.attempts.filter((a) => a.node_id === nodeId);
  const passes = as.filter((a) => a.correct);
  const fails = as.filter((a) => !a.correct).length;
  const delayed = as.filter((a) => a.context === 'delayed');
  const lastDelayedPass = delayed.some((a) => a.correct);
  const lastOk = as.length ? as[as.length - 1].correct : false;
  let state: NodeStateName = 'unknown';
  if (passes.length >= 2 && lastDelayedPass && lastOk) state = 'known';
  else if (passes.length >= 1) state = 'fragile';
  return {
    state,
    independent_passes: passes.length,
    assisted_passes: 0,
    self_graded_passes: 0,
    fails,
    last_delayed: delayed.length ? localDay(delayed[delayed.length - 1].ts) : null,
    transfer_passes: 0,
    uncertainty: passes.length >= 2 ? 'low' : as.length ? 'medium' : 'high',
  };
}

export interface GradedPractice {
  item: StudyItem;
  correct: boolean;
  idk: boolean;
  your_answer: QuestionOption | null;
  correct_answer: QuestionOption;
  context: 'delayed' | 'in-session';
  due: number;
}

/**
 * Grade a practice answer: the key is a letter of `order` (or of today's order when the
 * client did not send one), mapped back to the stored option before comparing.
 */
export function practiceAnswer(s: StudyRecord, body: Record<string, unknown>): GradedPractice {
  const id = String(body.item_id ?? '');
  const p = s.practice.find((x) => x.item_id === id);
  if (!p) {
    if (s.cards.some((c) => c.item_id === id)) {
      throw new FakeError('a card is self-rated through cards/review, not graded here', 'bad_request', 400);
    }
    throw new FakeError(`item ${id} not found`, 'not_found', 404);
  }
  if (p.check === 'rejected') {
    throw new FakeError('this question failed its blind check and is not served', 'bad_request', 400);
  }
  if (isSealed(p)) {
    throw new FakeError('this question is sealed for a mock exam and is not served by practice', 'sealed', 400);
  }
  const now = Date.now();
  let order = dayOrder(p, now);
  if (body.order !== undefined && body.order !== null) {
    const o = validOrder(p, body.order);
    if (!o) throw new FakeError('order must be a permutation of the option indexes', 'bad_order', 400);
    order = o;
  }
  const response = String(body.response ?? '');
  const idk = Boolean(body.idk) || response === 'IDK';
  const shown = LETTERS.indexOf(response.toUpperCase());
  if (!idk && (shown < 0 || shown >= order.length)) {
    throw new FakeError('response must be one of the shown option keys', 'bad_request', 400);
  }
  const key = answerIndex(p);
  const chosen = idk ? null : order[shown];
  const correct = chosen === key;
  const context = contextOf(p, now);
  const first = !s.attempts.some((a) => a.item_id === p.item_id);
  if (p.due === null) s.newPracticeToday += 1;
  p.due = now + (correct ? 72 : 0.25) * HOUR;
  p.last = now;
  s.attempts.push({ item_id: p.item_id, node_id: p.node_id, ts: now, correct, idk, first, mock: null, order, context });
  return {
    item: p,
    correct,
    idk,
    your_answer: chosen === null ? null : { key: LETTERS[shown], text: p.options[chosen].text },
    correct_answer: { key: LETTERS[order.indexOf(key)], text: p.options[key].text },
    context,
    due: p.due,
  };
}

/* ------------------------------------------------------------------ progress */

function tally(s: StudyRecord, items: StudyItem[], now: number): Tally {
  const ids = new Set(items.map((p) => p.item_id));
  const as = s.attempts.filter((a) => ids.has(a.item_id));
  const firsts = as.filter((a) => a.first);
  const firstCorrect = firsts.filter((a) => a.correct).length;
  const { low, high } = wilson(firstCorrect, firsts.length);
  return {
    // Like BankCounts.total: sealed items are counted apart, so `seen` never exceeds `bank`.
    bank: items.filter((p) => p.check !== 'rejected' && !isSealed(p)).length,
    // like learner/exam.py: checked counts the bank only, never sealed questions
    checked: items.filter((p) => p.check === 'checked' && !isSealed(p)).length,
    sealed: items.filter(isSealed).length,
    seen: new Set(as.map((a) => a.item_id)).size,
    first_attempts: firsts.length,
    first_correct: firstCorrect,
    low,
    high,
    attempts: as.length,
    correct: as.filter((a) => a.correct).length,
    due_now: items.filter((p) => servable(p) && p.due !== null && p.due <= now).length,
  };
}

function summary(s: StudyRecord, m: MockRec): MockSummary {
  return {
    session_id: m.session_id,
    started_at: new Date(m.started_at).toISOString(),
    submitted_at: m.submitted_at === null ? null : new Date(m.submitted_at).toISOString(),
    n: m.n,
    answered: m.submitted_at === null ? null : m.answers.filter((a) => a.response !== null).length,
    correct: m.submitted_at === null ? null : m.answers.filter((a) => a.correct).length,
    minutes: m.minutes,
    minutes_used: m.submitted_at === null ? null : Math.max(1, Math.ceil((m.submitted_at - m.started_at) / 60_000)),
    areas: m.submitted_at === null ? null : areaScores(s, m).map(({ code, n, correct }) => ({ code, n, correct })),
  };
}

export function progress(s: StudyRecord, goalId: string, deadline: string | null | undefined): Progress {
  const now = Date.now();
  const today = localDay(now);
  const bp = s.blueprint;
  const areas: AreaProgress[] = [];
  const inBlueprint = new Set<string>();
  if (bp) {
    for (const a of bp.areas) {
      const refs = new Set(a.subareas.map((x) => x.ref));
      const items = s.practice.filter((p) => p.ref && refs.has(p.ref));
      const subareas: NodeProgress[] = a.subareas.map((sub) => {
        const nodeId = sub.node_id ?? sub.ref;
        inBlueprint.add(nodeId);
        return {
          ref: sub.ref,
          node_id: nodeId,
          title: sub.title,
          exam_items: sub.exam_items,
          share: sub.share,
          state: stateFromAttempts(s, nodeId).state,
          ...tally(
            s,
            s.practice.filter((p) => p.ref === sub.ref),
            now,
          ),
        };
      });
      areas.push({ code: a.code, title: a.title, exam_items: a.exam_items, share: a.share, ...tally(s, items, now), subareas });
    }
  }
  const unassignedIds = Array.from(new Set(s.practice.map((p) => p.node_id))).filter((n) => !inBlueprint.has(n));
  const unassigned: NodeProgress[] = unassignedIds.map((nodeId) => ({
    ref: null,
    node_id: nodeId,
    title: s.practice.find((p) => p.node_id === nodeId)?.node_title ?? nodeId,
    exam_items: null,
    share: null,
    state: stateFromAttempts(s, nodeId).state,
    ...tally(
      s,
      s.practice.filter((p) => p.node_id === nodeId),
      now,
    ),
  }));

  let weighted: number | null = null;
  let note: string | null = null;
  let coverage = 0;
  if (bp) {
    coverage = areas
      .flatMap((a) => a.subareas)
      .filter((x) => x.first_attempts > 0)
      .reduce((n, x) => n + (x.share ?? 0), 0);
    const thin = areas.filter((a) => a.first_attempts < 5);
    // Same wording as learner/exam.py::_headline.
    if (thin.length) {
      note = `needs 5 first tries in every area; ${thin
        .map((a) => `área ${a.code} has ${a.first_attempts}`)
        .join(', ')}`;
    } else {
      const w = areas.reduce((n, a) => n + a.share * (a.first_correct / a.first_attempts), 0);
      weighted = Math.round((w / areas.reduce((n, a) => n + a.share, 0)) * 100);
      note = 'first tries only, weighted like the exam; not an ICNE score';
    }
  } else {
    note = 'no blueprint: set one to weigh areas like the exam does';
  }

  const activity: Progress['activity'] = [];
  for (let d = 27; d >= 0; d -= 1) {
    const day = addDays(today, -d);
    const as = s.attempts.filter((a) => localDay(a.ts) === day);
    activity.push({
      date: day,
      answers: as.length,
      correct: as.filter((a) => a.correct).length,
      cards: s.cardLog.filter((t) => localDay(t) === day).length,
    });
  }

  const horizon = deadline ? Math.min(60, Math.max(0, daysBetween(today, deadline))) : 14;
  const forecast: Progress['forecast'] = [];
  const dues = [
    ...s.practice.filter(servable).map((p) => p.due),
    ...s.cards.map((c) => c.due),
  ].filter((d): d is number => d !== null);
  for (let d = 0; d <= horizon; d += 1) {
    const day = addDays(today, d);
    forecast.push({
      date: day,
      due: dues.filter((t) => (d === 0 ? localDay(t) <= day : localDay(t) === day)).length,
    });
  }

  return {
    goal_id: goalId,
    today,
    deadline: deadline ?? null,
    days_left: deadline ? daysBetween(today, deadline) : null,
    has_blueprint: !!bp,
    totals: tally(s, s.practice, now),
    disciplinar: { weighted_accuracy: weighted, coverage, note },
    areas,
    unassigned,
    activity,
    forecast,
    mocks: s.mocks.map((m) => summary(s, m)).reverse(),
  };
}

/* ------------------------------------------------------------------ mocks */

export function listMocks(s: StudyRecord): MocksResponse {
  const avail = sealedAvailable(s);
  const open = openMock(s);
  return {
    sealed_available: avail.length,
    sealed_unchecked: s.practice.filter((p) => isSealed(p) && p.check === 'unchecked').length,
    sealed_by_area: (s.blueprint?.areas ?? []).map((a) => ({
      code: a.code,
      title: a.title,
      available: avail.filter((p) => a.subareas.some((x) => x.ref === p.ref)).length,
    })),
    open: open ? summary(s, open) : null,
    mocks: s.mocks.map((m) => summary(s, m)).reverse(),
  };
}

/** Largest-remainder allocation of n over the blueprint, capped by what each row has. */
function draw(s: StudyRecord, avail: StudyItem[], n: number, r: () => number): StudyItem[] {
  const pick = (list: StudyItem[], k: number) => shuffled(list, Math.floor(r() * 2 ** 31)).slice(0, k);
  if (!s.blueprint) return pick(avail, n);
  const rows = s.blueprint.areas.flatMap((a) => a.subareas);
  const byRef = new Map(rows.map((x) => [x.ref, avail.filter((p) => p.ref === x.ref)]));
  const total = rows.reduce((a, x) => a + x.share, 0);
  const want = rows.map((x) => {
    const exact = (n * x.share) / total;
    return { ref: x.ref, share: x.share, take: Math.min(Math.floor(exact), byRef.get(x.ref)!.length), rem: exact - Math.floor(exact) };
  });
  let left = n - want.reduce((a, w) => a + w.take, 0);
  const byRem = [...want].sort((a, b) => b.rem - a.rem || b.share - a.share);
  while (left > 0) {
    let moved = false;
    for (const w of byRem) {
      if (left <= 0) break;
      if (w.take < byRef.get(w.ref)!.length) {
        w.take += 1;
        left -= 1;
        moved = true;
      }
    }
    if (!moved) break;
  }
  const chosen = want.flatMap((w) => pick(byRef.get(w.ref)!, w.take));
  const rest = avail.filter((p) => !chosen.includes(p) && !p.ref);
  return [...chosen, ...pick(rest, Math.max(0, n - chosen.length))];
}

function mockOpenShape(s: StudyRecord, m: MockRec): MockOpen {
  return {
    session_id: m.session_id,
    status: 'open',
    started_at: new Date(m.started_at).toISOString(),
    minutes: m.minutes,
    ends_at: new Date(m.started_at + m.minutes * 60_000).toISOString(),
    n: m.n,
    questions: m.items.map(({ item_id, order }) => {
      const p = s.practice.find((x) => x.item_id === item_id)!;
      // No key, no explanation: nothing that could be feedback while the mock is open.
      return {
        item_id,
        ref: p.ref ?? null,
        area: areaOf(s, p.ref),
        node_title: p.node_title,
        stem: p.stem,
        options: shownOptions(p, order),
        order,
      };
    }),
  };
}

function mockRows(s: StudyRecord, m: MockRec) {
  return m.items.map(({ item_id, order }) => {
    const p = s.practice.find((x) => x.item_id === item_id)!;
    const a = m.answers.find((x) => x.item_id === item_id);
    const key = answerIndex(p);
    return { p, order, a, key };
  });
}

function areaScores(s: StudyRecord, m: MockRec): MockResult['areas'] {
  const rows = mockRows(s, m);
  return (s.blueprint?.areas ?? [])
    .map((area) => {
      const inArea = rows.filter((r) => area.subareas.some((x) => x.ref === r.p.ref));
      return {
        code: area.code,
        title: area.title,
        n: inArea.length,
        correct: inArea.filter((r) => r.a?.correct).length,
        subareas: area.subareas
          .map((x) => {
            const inSub = inArea.filter((r) => r.p.ref === x.ref);
            return { ref: x.ref, title: x.title, n: inSub.length, correct: inSub.filter((r) => r.a?.correct).length };
          })
          .filter((x) => x.n > 0),
      };
    })
    .filter((a) => a.n > 0);
}

function mockResultShape(s: StudyRecord, m: MockRec): MockResult {
  const sum = summary(s, m);
  const rows = mockRows(s, m);
  const areas = areaScores(s, m);
  const endsAt = m.started_at + m.minutes * 60_000;
  return {
    session_id: m.session_id,
    status: 'submitted',
    started_at: sum.started_at,
    submitted_at: sum.submitted_at!,
    minutes: m.minutes,
    minutes_used: sum.minutes_used!,
    overtime: (m.submitted_at ?? 0) > endsAt + 60_000,
    n: m.n,
    answered: sum.answered!,
    correct: sum.correct!,
    areas,
    items: rows.map(({ p, order, a, key }) => ({
      item_id: p.item_id,
      ref: p.ref ?? null,
      area_code: areaOf(s, p.ref)?.code ?? null,
      node_title: p.node_title,
      stem: p.stem,
      options: shownOptions(p, order),
      your_answer:
        a && a.chosen !== null ? { key: LETTERS[order.indexOf(a.chosen)], text: p.options[a.chosen].text } : null,
      correct_answer: { key: LETTERS[order.indexOf(key)], text: p.options[key].text },
      correct: Boolean(a?.correct),
      explanation: p.explanation,
    })),
  };
}

export function startMock(s: StudyRecord, body: Record<string, unknown>): MockOpen {
  if (openMock(s)) throw new FakeError('a mock exam is already open; submit it first', 'mock_open', 409);
  const avail = sealedAvailable(s);
  if (!avail.length) {
    throw new FakeError('no checked sealed questions are left for a mock', 'no_sealed_items', 400);
  }
  const want = body.n === undefined || body.n === null ? Math.min(MOCK_DEFAULT_MAX, avail.length) : Number(body.n);
  if (!Number.isInteger(want) || want < 1) throw new FakeError('n must be a positive whole number', 'bad_request', 400);
  const n = Math.min(want, avail.length);
  const minutes =
    body.minutes === undefined || body.minutes === null
      ? Math.round(n * MOCK_MINUTES_PER_ITEM)
      : Number(body.minutes);
  if (!Number.isFinite(minutes) || minutes < 1) throw new FakeError('minutes must be positive', 'bad_request', 400);
  const now = Date.now();
  const sessionId = `mk_${now.toString(36)}`;
  const r = rng(hash(sessionId));
  const items = shuffled(draw(s, avail, n, r), Math.floor(r() * 2 ** 31)).map((p) => ({
    item_id: p.item_id,
    order: shuffled(
      p.options.map((_, i) => i),
      Math.floor(r() * 2 ** 31),
    ),
  }));
  const m: MockRec = { session_id: sessionId, started_at: now, minutes: Math.round(minutes), n: items.length, items, submitted_at: null, answers: [] };
  s.mocks.push(m);
  return mockOpenShape(s, m);
}

export function getMock(s: StudyRecord, sessionId: string): MockOpen | MockResult {
  const m = s.mocks.find((x) => x.session_id === sessionId);
  if (!m) throw new FakeError(`mock ${sessionId} not found`, 'not_found', 404);
  return m.submitted_at === null ? mockOpenShape(s, m) : mockResultShape(s, m);
}

export function submitMock(s: StudyRecord, sessionId: string, body: Record<string, unknown>): MockResult {
  const m = s.mocks.find((x) => x.session_id === sessionId);
  if (!m) throw new FakeError(`mock ${sessionId} not found`, 'not_found', 404);
  if (m.submitted_at !== null) throw new FakeError('this mock was already submitted', 'already_submitted', 409);
  const given = new Map<string, Record<string, unknown>>();
  for (const a of Array.isArray(body.answers) ? (body.answers as Record<string, unknown>[]) : []) {
    given.set(String(a.item_id), a);
  }
  const now = Date.now();
  const answers: MockRec['answers'] = [];
  for (const { item_id, order: servedOrder } of m.items) {
    const p = s.practice.find((x) => x.item_id === item_id)!;
    const a = given.get(item_id);
    let order = servedOrder;
    if (a?.order !== undefined && a.order !== null) {
      const o = validOrder(p, a.order);
      if (!o) throw new FakeError(`order for ${item_id} is not a permutation of its options`, 'bad_order', 400);
      order = o;
    }
    const raw = a?.response === undefined || a.response === null ? null : String(a.response).toUpperCase();
    const shown = raw === null || raw === 'IDK' ? -1 : LETTERS.indexOf(raw);
    if (raw !== null && raw !== 'IDK' && (shown < 0 || shown >= order.length)) {
      throw new FakeError(`response for ${item_id} must be one of its option keys`, 'bad_request', 400);
    }
    const chosen = shown < 0 ? null : order[shown];
    const correct = chosen !== null && chosen === answerIndex(p);
    answers.push({ item_id, response: chosen === null ? null : raw, chosen, correct });
    // An ordinary answer event (rubric, bank-key-v1, payload.mock); unanswered = idk.
    s.attempts.push({
      item_id,
      node_id: p.node_id,
      ts: now,
      correct,
      idk: chosen === null,
      first: !s.attempts.some((x) => x.item_id === item_id),
      mock: m.session_id,
      order,
      context: 'in-session',
    });
    // ...and the item joins practice rotation like any other.
    p.mocked = true;
    p.last = now;
    p.due = now + (correct ? 72 : 0.25) * HOUR;
  }
  m.answers = answers;
  m.submitted_at = now;
  return mockResultShape(s, m);
}

/** A card flip, logged for the activity chart (self-report, never evidence). */
export function logCardReview(s: StudyRecord): void {
  s.cardLog.push(Date.now());
}
