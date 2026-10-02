/**
 * Small helpers shared by the study screens (/goal/study). No React, no fetching.
 */

/**
 * Whether a single-key shortcut may act on this keydown. Never while typing, never with a
 * modifier held, and Enter/Space on a focused button or link is left to the browser - it
 * already activates that control, and handling it here too would fire twice. Two
 * exceptions: a tab button (right after you click "Cards", Space should show the answer; the
 * tab re-selecting itself is a no-op), and a group marked `data-enter-submits` (answer
 * options, confidence), where Enter means "submit", not "press this option again".
 */
export function allowShortcut(ev: KeyboardEvent): boolean {
  if (ev.defaultPrevented || ev.metaKey || ev.ctrlKey || ev.altKey) return false;
  const t = ev.target as HTMLElement | null;
  if (!t || typeof t.closest !== 'function') return true;
  if (/^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable) return false;
  if (
    (ev.key === 'Enter' || ev.key === ' ') &&
    t.closest('button:not([role="tab"]), a, summary') &&
    !(ev.key === 'Enter' && t.closest('[data-enter-submits]'))
  ) {
    return false;
  }
  return true;
}

/** A due date in the learner's local time: "today 14:05", "tomorrow 09:00" or "2026-09-30". */
export function when(iso: string, now = new Date()): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const day = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const hm = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  const diff = Math.round((day(d) - day(now)) / 86_400_000);
  if (diff === 0) return `today ${hm}`;
  if (diff === 1) return `tomorrow ${hm}`;
  const pad = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

/** Deterministic shuffle (mulberry32), so a re-render never reorders a fill-in's options. */
export function shuffled<T>(items: T[], seed: number): T[] {
  let a = seed >>> 0;
  const rand = () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const out = [...items];
  for (let i = out.length - 1; i > 0; i -= 1) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function kb(bytes: number): string {
  return bytes < 1024 ? `${bytes} B` : `${Math.round(bytes / 1024)} KB`;
}
