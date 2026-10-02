/**
 * Pure helpers for the exam-prep screens (Progress, Mock exam). No React, no fetching.
 *
 * Evidence, never decimals: every percentage these helpers produce is a count ratio
 * (first-try accuracy, a mock score, a share of the exam) and callers always print the
 * counts next to it. Nothing here estimates mastery.
 */

import type { AreaProgress, BankCounts, CardCounts, MockSummary, Progress, Tally } from './types';

/**
 * Generic reference lines for first-try accuracy and mock scores: an 80% target and a 70%
 * floor. They are defaults, not a property of any exam; change them here. Drawn as reference
 * lines and bands, never used to compute anything the gateway did not send.
 */
export const TARGET = 80;
export const FLOOR = 70;

/** k/n as a whole percent, or null with no attempts - "no data" is not 0%. */
export function pct(k: number, n: number): number | null {
  return n > 0 ? Math.round((k / n) * 100) : null;
}

/** 95% Wilson score range in whole percent (the same rule the gateway uses for Tally). */
export function wilson(k: number, n: number): { low: number; high: number } | null {
  if (n <= 0) return null;
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

export type Tone = 'good' | 'warn' | 'bad' | 'none';

/**
 * Where a first-try accuracy sits against the plan's 80/70 lines, worded by how sure the
 * range is. Few attempts give a wide range, and the words say so instead of a verdict.
 */
export function verdict(k: number, n: number, low: number | null, high: number | null): { label: string; tone: Tone } {
  const p = pct(k, n);
  if (p === null || low === null || high === null) return { label: 'no data yet', tone: 'none' };
  if (low >= TARGET) return { label: `meets the ${TARGET}% target`, tone: 'good' };
  if (high < FLOOR) return { label: `under the ${FLOOR}% floor`, tone: 'bad' };
  if (p >= TARGET) return { label: 'on target so far, range still wide', tone: 'good' };
  if (p >= FLOOR) return { label: `between floor and target`, tone: 'warn' };
  return { label: `below the floor so far, range still wide`, tone: 'bad' };
}

export function tallyVerdict(t: Pick<Tally, 'first_correct' | 'first_attempts' | 'low' | 'high'>) {
  return verdict(t.first_correct, t.first_attempts, t.low, t.high);
}

/** "7 of 10 first try" - the counts that always travel with a first-try percentage. */
export function firstTry(t: Pick<Tally, 'first_correct' | 'first_attempts'>): string {
  return `${t.first_correct} of ${t.first_attempts} first try`;
}

const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function parseDay(day: string): Date | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(day);
  return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
}

/** "Fri 4 Dec" from YYYY-MM-DD (local, no time-zone shift). */
export function dayLabel(day: string, withWeekday = true): string {
  const d = parseDay(day);
  if (!d) return day;
  const core = `${d.getDate()} ${MONTHS[d.getMonth()]}`;
  return withWeekday ? `${WEEKDAYS[d.getDay()]} ${core}` : core;
}

/** "14:05" or "26 Sep 14:05" for an ISO timestamp, in local time. */
export function timeLabel(iso: string, withDay = true): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  return withDay ? `${d.getDate()} ${MONTHS[d.getMonth()]} ${hm}` : hm;
}

/** h:mm:ss or m:ss for a countdown. */
export function clock(ms: number): string {
  const total = Math.max(0, Math.ceil(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h ? String(m).padStart(2, '0') : String(m);
  return `${h ? `${h}:` : ''}${mm}:${String(s).padStart(2, '0')}`;
}

/**
 * Minutes per question, from data only. Your own pace counts only from mocks you actually
 * sat - at least 10 answers, at least half the questions answered, and at least 30 s per
 * answer (a judgement: faster than that is clicking through, not sitting) - so a mock
 * submitted after a minute cannot claim you answer in 0.03 min. Otherwise the pace the
 * largest mock's time limit implies (the gateway's exam-pace setting); else null.
 */
export function pace(mocks: MockSummary[]): { minutes: number; source: 'yours' | 'exam' } | null {
  const sat = mocks.filter(
    (m) =>
      m.submitted_at &&
      m.minutes_used !== null &&
      m.answered !== null &&
      m.answered >= 10 &&
      m.answered >= m.n / 2 &&
      m.minutes_used >= m.answered * 0.5,
  );
  if (sat.length) {
    const used = sat.reduce((a, m) => a + (m.minutes_used ?? 0), 0);
    const answered = sat.reduce((a, m) => a + (m.answered ?? 0), 0);
    return { minutes: used / answered, source: 'yours' };
  }
  const biggest = [...mocks].filter((m) => m.n > 0 && m.minutes > 0).sort((a, b) => b.n - a.n)[0];
  return biggest ? { minutes: biggest.minutes / biggest.n, source: 'exam' } : null;
}

export interface TodayPlan {
  newQuestions: number;
  dueQuestions: number;
  cards: number;
  sentence: string;
  /** A second, optional line: where to point practice, derived from the per-area counts. */
  focus: string | null;
}

/**
 * The one plain sentence of what to do today. Every number is read from the gateway: the
 * daily new-question room, what is due, and a minute estimate only when a mock has measured
 * (or its limit implies) a pace.
 */
export function todayPlan(
  bank: BankCounts,
  cards: CardCounts,
  progress: Progress | null,
  mocks: MockSummary[],
): TodayPlan {
  const newQuestions = Math.min(bank.new_available, Math.max(0, bank.new_limit - bank.new_today));
  const dueQuestions = bank.due_now;
  const cardCount =
    cards.due_now + Math.min(cards.new_available, Math.max(0, cards.new_limit - cards.new_today));
  const q = newQuestions + dueQuestions;
  const p = pace(mocks);

  let sentence: string;
  if (!q && !cardCount) {
    sentence = 'Nothing is due and today’s new questions are done.';
  } else {
    const parts: string[] = [];
    if (q) parts.push(`${newQuestions} new + ${dueQuestions} due question${q === 1 ? '' : 's'}`);
    if (cardCount) parts.push(`${cardCount} card${cardCount === 1 ? '' : 's'}`);
    const est =
      p && q
        ? ` — about ${Math.max(1, Math.round(q * p.minutes))} min for the questions at ${
            p.source === 'yours' ? 'your mock pace' : 'the exam’s pace'
          } (${p.minutes.toFixed(1)} min each)`
        : '';
    sentence = `Today: ${parts.join(' and ')}${est}.`;
  }

  return { newQuestions, dueQuestions, cards: cardCount, sentence, focus: focusHint(progress) };
}

/** Which area to point practice at, in words, with the counts that justify it. */
export function focusHint(progress: Progress | null): string | null {
  if (!progress || !progress.areas.length) return null;
  const byWeight = [...progress.areas].sort((a, b) => b.share - a.share);
  const untouched = byWeight.find((a) => a.first_attempts === 0);
  if (untouched) {
    return `No first attempts yet in area ${untouched.code} (${untouched.title}, ${untouched.exam_items} exam items): Mixed practice will pull toward it, or focus it directly.`;
  }
  const under = byWeight.find((a) => a.high !== null && a.high < FLOOR);
  if (under) {
    return `Area ${under.code} (${under.title}) is under the ${FLOOR}% floor even at the top of its range (${firstTry(under)}): focus it.`;
  }
  const scored = progress.areas.filter((a) => a.first_attempts >= 5);
  const weakest = scored.sort(
    (a, b) => a.first_correct / a.first_attempts - b.first_correct / b.first_attempts,
  )[0];
  if (weakest && (pct(weakest.first_correct, weakest.first_attempts) ?? 100) < TARGET) {
    return `Weakest so far: area ${weakest.code} (${weakest.title}), ${firstTry(weakest)}.`;
  }
  const thin = byWeight.find((a) => a.first_attempts < 5);
  if (thin) return `Area ${thin.code} has only ${thin.first_attempts} first attempts; a few more will narrow its range.`;
  return null;
}

/** Seen of the practice bank, 0..100, or null for an empty bank. */
export function seenPct(t: Pick<Tally, 'seen' | 'bank'>): number | null {
  return t.bank > 0 ? Math.min(100, Math.round((t.seen / t.bank) * 100)) : null;
}

export function areaLabel(a: Pick<AreaProgress, 'code' | 'title'>): string {
  return `${a.code} ${a.title}`;
}

/** "3.5 Plataformas…" once: concepts imported from tagged headings already carry their ref. */
export function conceptLabel(ref: string | null | undefined, title: string | null | undefined): string {
  const name = (title ?? '').trim();
  if (!ref) return name;
  return name === ref || name.startsWith(`${ref} `) ? name : `${ref} ${name}`.trim();
}

/** Nice, round y-axis ticks from 0 to at least `max`; whole steps for counts. */
export function ticks(max: number, count = 4, integer = true): number[] {
  if (max <= 0) return [0, 1];
  const raw = max / count;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const nice = [1, 2, 5, 10].map((m) => m * mag).find((s) => s >= raw) ?? raw;
  const step = integer ? Math.max(1, nice) : nice;
  const out: number[] = [];
  for (let v = 0; v <= max + step * 0.001; v += step) out.push(Math.round(v * 1000) / 1000);
  if (out[out.length - 1] < max) out.push(out[out.length - 1] + step);
  return out;
}
