/**
 * An open mock's answers, kept in this browser until submit (the contract has no "save an
 * answer" route: a mock is graded once, on submit). Keyed by session id, so a reload - or a
 * second tab - resumes the same draft. Every access is wrapped: with storage blocked the
 * mock still works for the page view, and the screen says a reload would lose it.
 */

export interface MockDraft {
  /**
   * item_id -> the chosen option's STORED index (`order[i]` of the shown letter), not the
   * letter: a reload can never re-point an answer at a different option.
   */
  answers: Record<string, number>;
  flags: string[];
  confidence: Record<string, number>;
  /** The question on screen, 0-based. */
  current: number;
  /** This browser's clock minus the server's at start, so the countdown follows the server. */
  skewMs: number;
}

const key = (sessionId: string) => `lt-mock:${sessionId}`;

function valid(v: unknown): v is MockDraft {
  if (!v || typeof v !== 'object') return false;
  const d = v as Partial<MockDraft>;
  return (
    typeof d.answers === 'object' &&
    d.answers !== null &&
    Array.isArray(d.flags) &&
    typeof d.confidence === 'object' &&
    d.confidence !== null &&
    typeof d.current === 'number'
  );
}

/** `ok: false` = storage is unavailable; `draft: null` = nothing saved for this session. */
export function loadMockDraft(sessionId: string): { ok: boolean; draft: MockDraft | null } {
  try {
    const raw = window.localStorage.getItem(key(sessionId));
    if (!raw) return { ok: true, draft: null };
    const parsed: unknown = JSON.parse(raw);
    if (!valid(parsed)) return { ok: true, draft: null };
    return { ok: true, draft: { ...parsed, skewMs: Number(parsed.skewMs) || 0 } };
  } catch {
    return { ok: false, draft: null };
  }
}

/** Returns false when the write failed (storage blocked or full). */
export function saveMockDraft(sessionId: string, draft: MockDraft): boolean {
  try {
    window.localStorage.setItem(key(sessionId), JSON.stringify(draft));
    return true;
  } catch {
    return false;
  }
}

export function clearMockDraft(sessionId: string): void {
  try {
    window.localStorage.removeItem(key(sessionId));
  } catch {
    /* nothing to clear */
  }
}
