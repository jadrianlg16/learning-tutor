'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ApiError, api, errorMessage } from '@/lib/api';
import { clearMockDraft, loadMockDraft, saveMockDraft, type MockDraft } from '@/lib/mockDraft';
import { FLOOR, TARGET, clock, conceptLabel, dayLabel, pace, pct, timeLabel, wilson } from '@/lib/progress';
import { allowShortcut } from '@/lib/study';
import type { MockAnswer, MockOpen, MockResult, MockSummary, MocksResponse } from '@/lib/types';
import { ErrorNote, Loading } from './Bits';
import { AccuracyStrip, ChartFrame, Key, StripAxis } from './Charts';
import { Markdown } from './Markdown';

type View =
  | { kind: 'loading' }
  | { kind: 'lobby' }
  | { kind: 'exam'; open: MockOpen }
  | { kind: 'result'; result: MockResult; auto: boolean };

/**
 * Sealed mock exams. Questions the learner has never practised, timed at the exam's pace,
 * and NO feedback of any kind until submit: while a mock is open nothing on screen says
 * right, wrong, area or key. Answers live in localStorage (per session id) until submit, so a
 * reload resumes; the gateway grades everything at once.
 */
export function MockExamPanel({ goalId, onChange }: { goalId: string; onChange: () => void }) {
  const [view, setView] = useState<View>({ kind: 'loading' });
  const [list, setList] = useState<MocksResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const l = await api.listMocks(goalId);
      setList(l);
      return l;
    } catch (e) {
      setError(errorMessage(e));
      return null;
    }
  }, [goalId]);

  const openSession = useCallback(
    async (sessionId: string) => {
      setError(null);
      try {
        const m = await api.getMock(goalId, sessionId);
        setView(m.status === 'open' ? { kind: 'exam', open: m } : { kind: 'result', result: m, auto: false });
      } catch (e) {
        setError(errorMessage(e));
        setView({ kind: 'lobby' });
      }
    },
    [goalId],
  );

  // A mock that is already open is resumed straight away: the tab never shows the lobby
  // over an exam in progress.
  useEffect(() => {
    void (async () => {
      const l = await refresh();
      if (l?.open) await openSession(l.open.session_id);
      else setView({ kind: 'lobby' });
    })();
  }, [refresh, openSession]);

  const backToLobby = useCallback(() => {
    setView({ kind: 'lobby' });
    void refresh();
  }, [refresh]);

  if (view.kind === 'loading') return error ? <ErrorNote message={error} /> : <Loading what="mock exams" />;

  if (view.kind === 'exam') {
    return (
      <MockExam
        goalId={goalId}
        open={view.open}
        onSubmitted={(result, auto) => {
          setView({ kind: 'result', result, auto });
          onChange();
        }}
      />
    );
  }

  if (view.kind === 'result') {
    return <MockResultView result={view.result} auto={view.auto} onBack={backToLobby} />;
  }

  return (
    <Lobby
      goalId={goalId}
      list={list}
      error={error}
      onStarted={(open) => {
        setView({ kind: 'exam', open });
        onChange();
      }}
      onOpenExisting={(id) => void openSession(id)}
    />
  );
}

/* ------------------------------------------------------------------ lobby */

const SIZES = [30, 60];

function Lobby({
  goalId,
  list,
  error,
  onStarted,
  onOpenExisting,
}: {
  goalId: string;
  list: MocksResponse | null;
  error: string | null;
  onStarted: (open: MockOpen) => void;
  onOpenExisting: (sessionId: string) => void;
}) {
  const avail = list?.sealed_available ?? 0;
  const options = useMemo(() => {
    const o = SIZES.filter((n) => n < avail).map((n) => ({ n, label: `${n} questions` }));
    if (avail) o.push({ n: avail, label: `all ${avail} available` });
    return o;
  }, [avail]);
  const [size, setSize] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [startError, setStartError] = useState<string | null>(null);

  useEffect(() => {
    if (!avail) return;
    setSize((s) => (s && s <= avail ? s : avail >= 60 ? 60 : avail));
  }, [avail]);

  const done = (list?.mocks ?? []).filter((m) => m.submitted_at);
  const p = pace(list?.mocks ?? []);

  const start = async () => {
    if (!size) return;
    setBusy(true);
    setStartError(null);
    try {
      const open = await api.startMock(goalId, { n: size });
      // Remember how far this browser's clock is from the server's, so the countdown and
      // the auto-submit use the server's end time even if the local clock is off.
      saveMockDraft(open.session_id, {
        answers: {},
        flags: [],
        confidence: {},
        current: 0,
        skewMs: Date.now() - Date.parse(open.started_at),
      });
      onStarted(open);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409 && list?.open) onOpenExisting(list.open.session_id);
      else setStartError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack gap-lg">
      <p className="small muted" style={{ maxWidth: '70ch' }}>
        A mock draws <strong>sealed</strong> questions you have never practised, weighted by the
        exam blueprint, shuffles questions and options, and runs against a clock at the
        exam&apos;s pace. Nothing tells you right or wrong until you submit; then every answer is
        graded and recorded, and those questions join normal practice.
      </p>
      <ErrorNote message={error} />
      {!list ? (
        <Loading what="sealed questions" />
      ) : avail === 0 ? (
        <div className="card empty">
          <span className="eyebrow">mock exam</span>
          <h2>No sealed questions available</h2>
          <p className="small muted" style={{ maxWidth: '64ch' }}>
            Sealed questions are imported by the harness with <code>--pool mock</code> and never
            appear in practice until a mock uses them.
            {list.sealed_unchecked
              ? ` ${list.sealed_unchecked} sealed question${list.sealed_unchecked === 1 ? ' is' : 's are'} waiting for a blind check; they become drawable once a solver that is not their author agrees with the key.`
              : ' There are none waiting for a blind check either.'}
          </p>
        </div>
      ) : (
        <section className="card" aria-labelledby="mock-start-h">
          <span className="eyebrow">new mock</span>
          <h2 id="mock-start-h">
            {avail} sealed question{avail === 1 ? '' : 's'} ready
          </h2>
          {list.sealed_by_area.length ? (
            <ul className="sealedlist" aria-label="Sealed questions by area">
              {list.sealed_by_area.map((a) => (
                <li key={a.code}>
                  <span className="mono">{a.available}</span>
                  <span>
                    {a.code} {a.title}
                  </span>
                </li>
              ))}
            </ul>
          ) : null}
          {list.sealed_unchecked ? (
            <p className="small muted">
              {list.sealed_unchecked} more sealed question{list.sealed_unchecked === 1 ? ' is' : 's are'} waiting for a
              blind check and cannot be drawn yet.
            </p>
          ) : null}
          <fieldset className="plainset">
            <legend className="eyebrow">How many questions</legend>
            <div className="sizes" role="radiogroup" aria-label="Mock size">
              {options.map((o) => (
                <label key={o.n} className={`sizepick${size === o.n ? ' on' : ''}`}>
                  <input type="radio" name="mock-size" value={o.n} checked={size === o.n} onChange={() => setSize(o.n)} />
                  <span>
                    <strong>{o.label}</strong>
                    <span className="small muted block">
                      {p ? `about ${Math.round(o.n * p.minutes)} min at ${p.source === 'yours' ? 'your pace' : 'exam pace'}` : 'timed at exam pace'}
                    </span>
                  </span>
                </label>
              ))}
            </div>
          </fieldset>
          <p className="small muted">
            The time limit is set by the gateway when the mock starts. Leaving the page is fine: your
            answers stay in this browser and the mock resumes where you were.
          </p>
          <ErrorNote message={startError} />
          <div>
            <button type="button" className="btn primary" disabled={!size || busy} onClick={start}>
              {busy ? 'starting...' : `Start a ${size ?? ''}-question mock`}
            </button>
          </div>
        </section>
      )}

      <section className="stack" aria-labelledby="mock-past-h">
        <h3 id="mock-past-h">Past mocks</h3>
        {done.length ? (
          <div className="table-wrap">
            <table className="compact">
              <thead>
                <tr>
                  <th scope="col">Started</th>
                  <th scope="col">Score</th>
                  <th scope="col">Answered</th>
                  <th scope="col">Time</th>
                  <th scope="col">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {done.map((m) => (
                  <PastRow key={m.session_id} m={m} onOpen={() => onOpenExisting(m.session_id)} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="small muted">None yet.</p>
        )}
      </section>
    </div>
  );
}

function PastRow({ m, onOpen }: { m: MockSummary; onOpen: () => void }) {
  return (
    <tr>
      <th scope="row">{timeLabel(m.started_at)}</th>
      <td className="mono">
        {m.correct} of {m.n} ({pct(m.correct ?? 0, m.n)}%)
      </td>
      <td className="mono">
        {m.answered} of {m.n}
      </td>
      <td className="mono">
        {m.minutes_used ?? '?'} of {m.minutes} min
      </td>
      <td>
        <button type="button" className="btn sm" onClick={onOpen}>
          Review
        </button>
      </td>
    </tr>
  );
}

/* ------------------------------------------------------------------ the exam */

function MockExam({
  goalId,
  open,
  onSubmitted,
}: {
  goalId: string;
  open: MockOpen;
  onSubmitted: (r: MockResult, auto: boolean) => void;
}) {
  const qs = open.questions;
  const [draft, setDraft] = useState<MockDraft>(() => ({ answers: {}, flags: [], confidence: {}, current: 0, skewMs: 0 }));
  const [restored, setRestored] = useState(false);
  const [storageOk, setStorageOk] = useState(true);
  const [now, setNow] = useState(() => Date.now());
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announce, setAnnounce] = useState('');
  const submitted = useRef(false);
  const confirmRef = useRef<HTMLButtonElement | null>(null);

  // Restore after mount (the export is prerendered; storage is read client-side only).
  useEffect(() => {
    const d = loadMockDraft(open.session_id);
    if (d.ok && d.draft) setDraft({ ...d.draft, current: Math.min(d.draft.current, qs.length - 1) });
    setStorageOk(d.ok);
    setRestored(true);
  }, [open.session_id, qs.length]);

  useEffect(() => {
    if (!restored) return;
    setStorageOk(saveMockDraft(open.session_id, draft));
  }, [draft, restored, open.session_id]);

  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, []);

  const endsAt = Date.parse(open.ends_at) + draft.skewMs;
  const left = endsAt - now;
  const cur = Math.min(draft.current, qs.length - 1);
  const q = qs[cur];

  // Stored per question as the option's stored index (order[i]), mapped back to the letter
  // shown now - so a reload can never re-point an answer at a different option.
  const keyOf = useCallback(
    (itemId: string): string | null => {
      const qq = qs.find((x) => x.item_id === itemId);
      const stored = draft.answers[itemId];
      if (!qq || stored === undefined) return null;
      const i = qq.order.indexOf(stored);
      return i >= 0 ? (qq.options[i]?.key ?? null) : null;
    },
    [qs, draft.answers],
  );

  const answeredCount = qs.filter((x) => draft.answers[x.item_id] !== undefined).length;
  const flagged = new Set(draft.flags);
  const unanswered = qs.length - answeredCount;

  const pick = useCallback(
    (key: string) => {
      const i = q.options.findIndex((o) => o.key === key);
      if (i < 0) return;
      setDraft((d) => ({ ...d, answers: { ...d.answers, [q.item_id]: q.order[i] } }));
    },
    [q],
  );

  const clearAnswer = () =>
    setDraft((d) => {
      const answers = { ...d.answers };
      delete answers[q.item_id];
      return { ...d, answers };
    });

  const go = useCallback(
    (i: number) => setDraft((d) => ({ ...d, current: Math.max(0, Math.min(qs.length - 1, i)) })),
    [qs.length],
  );

  const toggleFlag = useCallback(
    () =>
      setDraft((d) => ({
        ...d,
        flags: d.flags.includes(q.item_id) ? d.flags.filter((x) => x !== q.item_id) : [...d.flags, q.item_id],
      })),
    [q.item_id],
  );

  const setConf = useCallback(
    (n: number) =>
      setDraft((d) => {
        const confidence = { ...d.confidence };
        if (confidence[q.item_id] === n) delete confidence[q.item_id];
        else confidence[q.item_id] = n;
        return { ...d, confidence };
      }),
    [q.item_id],
  );

  const submit = useCallback(
    async (auto: boolean) => {
      if (submitted.current) return;
      submitted.current = true;
      setBusy(true);
      setError(null);
      const answers: MockAnswer[] = qs.map((x) => ({
        item_id: x.item_id,
        response: keyOf(x.item_id),
        order: x.order,
        ...(draft.confidence[x.item_id] ? { confidence: draft.confidence[x.item_id] } : {}),
      }));
      try {
        const r = await api.submitMock(goalId, open.session_id, answers);
        clearMockDraft(open.session_id);
        onSubmitted(r, auto);
      } catch (e) {
        if (e instanceof ApiError && e.status === 409) {
          // Already graded (another tab, or a retried submit): show what the gateway has.
          try {
            const m = await api.getMock(goalId, open.session_id);
            if (m.status === 'submitted') {
              clearMockDraft(open.session_id);
              onSubmitted(m, auto);
              return;
            }
          } catch {
            /* fall through to the error */
          }
        }
        submitted.current = false;
        setError(errorMessage(e));
      } finally {
        setBusy(false);
        setConfirm(false);
      }
    },
    [qs, keyOf, draft.confidence, goalId, open.session_id, onSubmitted],
  );

  // Time is up: submit what there is. Unanswered questions are recorded as "I do not know".
  useEffect(() => {
    if (restored && left <= 0 && !submitted.current) void submit(true);
  }, [left, restored, submit]);

  // Spoken time warnings, not a per-second live region.
  const mins = Math.ceil(left / 60_000);
  useEffect(() => {
    if ([30, 10, 5, 1].includes(mins)) setAnnounce(`${mins} minute${mins === 1 ? '' : 's'} left.`);
  }, [mins]);

  useEffect(() => {
    if (confirm) confirmRef.current?.focus();
  }, [confirm]);

  // Same keys as practice: A-E pick, 1-5 confidence, Enter moves on. Plus F flag and
  // left/right arrows between questions. Nothing here submits the mock.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (confirm) {
        if (ev.key === 'Escape') setConfirm(false);
        return;
      }
      if (busy || !allowShortcut(ev)) return;
      const opt = q.options.find((o) => o.key.toUpperCase() === ev.key.toUpperCase());
      if (opt) {
        ev.preventDefault();
        pick(opt.key);
        return;
      }
      if (/^[1-5]$/.test(ev.key)) {
        ev.preventDefault();
        setConf(Number(ev.key));
      } else if (ev.key === 'f' || ev.key === 'F') {
        ev.preventDefault();
        toggleFlag();
      } else if (ev.key === 'ArrowRight' || ev.key === 'Enter') {
        if (cur < qs.length - 1) {
          ev.preventDefault();
          go(cur + 1);
        }
      } else if (ev.key === 'ArrowLeft') {
        if (cur > 0) {
          ev.preventDefault();
          go(cur - 1);
        }
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [q, cur, qs.length, busy, confirm, pick, setConf, toggleFlag, go]);

  const mine = keyOf(q.item_id);
  const conf = draft.confidence[q.item_id] ?? null;
  const isFlagged = flagged.has(q.item_id);
  const low = left < 5 * 60_000;
  const lastKey = q.options[q.options.length - 1]?.key ?? 'C';

  return (
    <div className="stack gap-lg examwrap">
      <div className="examhead" role="region" aria-label="Mock exam status">
        <span className="eyebrow">mock exam · no feedback until you submit</span>
        <span className="small mono examstat">
          <span className="long">
            {answeredCount} of {qs.length} answered · {flagged.size} flagged
          </span>
          <span className="short" aria-hidden="true">
            {answeredCount}/{qs.length} · ⚑{flagged.size}
          </span>
        </span>
        <span className={`timer${low ? ' low' : ''}`} role="timer" aria-label={`Time left ${clock(left)}`}>
          {clock(left)}
        </span>
        <button type="button" className="btn primary sm" disabled={busy} onClick={() => setConfirm(true)}>
          Submit…
        </button>
      </div>
      <p className="sr-only" aria-live="polite">
        {announce}
      </p>
      {!storageOk ? (
        <p className="note warn">
          This browser is not saving your answers (storage is blocked), so a reload would lose them.
          Stay on this page until you submit.
        </p>
      ) : null}
      <ErrorNote message={error} />

      <div className="examgrid">
        <section className="qcard" aria-label={`Question ${cur + 1} of ${qs.length}`}>
          <div className="qhead">
            <span className="eyebrow">
              question {cur + 1} of {qs.length}
            </span>
            <button
              type="button"
              className={`btn sm flagbtn${isFlagged ? ' on' : ''}`}
              aria-pressed={isFlagged}
              onClick={toggleFlag}
            >
              <span aria-hidden="true">⚑</span> {isFlagged ? 'Flagged for review' : 'Flag for review'}
            </button>
          </div>
          <div className="qstem">
            <Markdown className="prose">{q.stem}</Markdown>
          </div>
          <div className="options" role="group" aria-label="Options" data-enter-submits>
            {q.options.map((o) => (
              <button
                type="button"
                key={o.key}
                className={`opt${mine === o.key ? ' selected' : ''}`}
                aria-pressed={mine === o.key}
                disabled={busy}
                onClick={() => pick(o.key)}
              >
                <span className="key" aria-hidden="true">
                  {o.key}
                </span>
                <span>
                  <Markdown inline>{o.text.replace(new RegExp(`^${o.key}[.)]\\s*`), '')}</Markdown>
                </span>
              </button>
            ))}
          </div>
          <div className="stack gap-sm">
            <span className="eyebrow" id="mock-conf">
              How confident? 1 (guess) to 5 (certain) · optional
            </span>
            <div className="confidence" role="group" aria-labelledby="mock-conf" data-enter-submits>
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  className={conf === n ? 'on' : ''}
                  aria-pressed={conf === n}
                  disabled={busy}
                  onClick={() => setConf(n)}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>
          <div className="row between">
            <div className="row">
              <button type="button" className="btn sm" disabled={cur === 0} onClick={() => go(cur - 1)}>
                ← Previous
              </button>
              <button type="button" className="btn sm" disabled={cur === qs.length - 1} onClick={() => go(cur + 1)}>
                Next →
              </button>
              {mine ? (
                <button type="button" className="linkbtn small" onClick={clearAnswer}>
                  clear answer
                </button>
              ) : null}
            </div>
            <span className="small muted mono">
              A-{lastKey} pick · 1-5 confidence · F flag · ←/→ or Enter move
            </span>
          </div>
        </section>

        <nav className="qnav" aria-label="Questions">
          <div className="qnav-legend small muted">
            <span>
              <i className="qn answered" aria-hidden="true" /> answered
            </span>
            <span>
              <i className="qn flagged" aria-hidden="true" /> flagged
            </span>
            <span>
              <i className="qn current" aria-hidden="true" /> current
            </span>
          </div>
          <ol className="qnav-grid">
            {qs.map((x, i) => {
              const a = draft.answers[x.item_id] !== undefined;
              const f = flagged.has(x.item_id);
              const c = i === cur;
              return (
                <li key={x.item_id}>
                  <button
                    type="button"
                    className={`qn${a ? ' answered' : ''}${f ? ' flagged' : ''}${c ? ' current' : ''}`}
                    aria-current={c ? 'step' : undefined}
                    aria-label={`Question ${i + 1}, ${a ? 'answered' : 'not answered'}${f ? ', flagged' : ''}`}
                    onClick={() => go(i)}
                  >
                    {i + 1}
                  </button>
                </li>
              );
            })}
          </ol>
          {flagged.size ? (
            <button
              type="button"
              className="linkbtn small"
              onClick={() => {
                const next = qs.findIndex((x, i) => i > cur && flagged.has(x.item_id));
                go(next >= 0 ? next : qs.findIndex((x) => flagged.has(x.item_id)));
              }}
            >
              next flagged question
            </button>
          ) : null}
        </nav>
      </div>

      {confirm ? (
        <div className="backdrop" role="presentation" onClick={() => setConfirm(false)}>
          <div
            className="dialog"
            role="dialog"
            aria-modal="true"
            aria-labelledby="mock-confirm-h"
            onClick={(e) => e.stopPropagation()}
          >
            <h2 id="mock-confirm-h">Submit the mock?</h2>
            <p>
              {unanswered ? (
                <>
                  <strong>
                    {unanswered} of {qs.length} unanswered
                  </strong>{' '}
                  — they will be recorded as &ldquo;I do not know&rdquo;.
                </>
              ) : (
                <>All {qs.length} questions answered.</>
              )}
              {flagged.size ? ` ${flagged.size} still flagged for review.` : ''}
            </p>
            <p className="small muted">
              {clock(left)} left. After submitting, every answer is graded and you cannot change them.
            </p>
            <div className="row">
              <button type="button" className="btn" onClick={() => setConfirm(false)}>
                Keep working
              </button>
              <button ref={confirmRef} type="button" className="btn primary" disabled={busy} onClick={() => void submit(false)}>
                {busy ? 'grading...' : 'Submit now'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ------------------------------------------------------------------ results */

/** "2.9 min per question", or seconds under a minute ("20 s per question"). */
function perQuestion(minutes: number, n: number): string {
  const m = minutes / Math.max(1, n);
  return m >= 1 ? `${m.toFixed(1)} min per question` : `${Math.max(1, Math.round(m * 60))} s per question`;
}

function MockResultView({ result: r, auto, onBack }: { result: MockResult; auto: boolean; onBack: () => void }) {
  const [onlyWrong, setOnlyWrong] = useState(false);
  const score = pct(r.correct, r.n);
  const range = wilson(r.correct, r.n);
  const wrong = r.items.filter((i) => !i.correct).length;
  const shown = r.items
    .map((it, i) => ({ it, n: i + 1 }))
    .filter(({ it }) => !onlyWrong || !it.correct);
  const titleOf = new Map(r.areas.map((a) => [a.code, a.title]));

  return (
    <div className="stack gap-lg">
      {auto ? <p className="note warn">Time ran out, so the mock was submitted automatically with the answers you had.</p> : null}
      <section className="card progresshead" aria-label="Mock result">
        <div className="kpis">
          <div className="kpi hero">
            <span className="eyebrow">score</span>
            <span className="kpi-value">
              {r.correct}
              <span className="kpi-unit"> of {r.n}</span>
            </span>
            <span className="small muted">
              {score}% correct{range ? ` · 95% range ${range.low}–${range.high}%` : ''}
            </span>
          </div>
          <div className="kpi">
            <span className="eyebrow">answered</span>
            <span className="kpi-value sm">
              {r.answered} of {r.n}
            </span>
            <span className="small muted">
              {r.n - r.answered ? `${r.n - r.answered} recorded as “I do not know”` : 'none left blank'}
            </span>
          </div>
          <div className="kpi">
            <span className="eyebrow">time used</span>
            <span className="kpi-value sm">
              {r.minutes_used} of {r.minutes} min
            </span>
            <span className="small muted">
              {r.overtime ? 'over the limit' : perQuestion(r.minutes_used, r.n)}
            </span>
          </div>
          <div className="kpi">
            <span className="eyebrow">submitted</span>
            <span className="kpi-value sm">{timeLabel(r.submitted_at, false)}</span>
            <span className="small muted">{dayLabel(r.submitted_at.slice(0, 10))}</span>
          </div>
        </div>
        <div className="row">
          <button type="button" className="btn sm" onClick={onBack}>
            Back to mock exams
          </button>
        </div>
      </section>

      {r.areas.length ? (
        <ChartFrame
          title="By area and subárea"
          sub={`Share correct in this mock, with its 95% range - a mock has few questions per subárea, so the range is wide. Reference lines: ${TARGET}% target, ${FLOOR}% floor.`}
          legend={
            <>
              <Key swatch="dot">share correct</Key>
              <Key swatch="range">95% range</Key>
              <Key swatch="target">{TARGET}% target</Key>
              <Key swatch="floor">{FLOOR}% floor</Key>
            </>
          }
          table={
            <table className="compact">
              <thead>
                <tr>
                  <th scope="col">Area / subárea</th>
                  <th scope="col">Correct</th>
                  <th scope="col">95% range</th>
                </tr>
              </thead>
              <tbody>
                {r.areas.flatMap((a) => {
                  const ra = wilson(a.correct, a.n);
                  return [
                    <tr key={a.code}>
                      <th scope="row">
                        {a.code} {a.title}
                      </th>
                      <td className="mono">
                        {a.correct} of {a.n}
                      </td>
                      <td className="mono">{ra ? `${ra.low}–${ra.high}%` : '—'}</td>
                    </tr>,
                    ...a.subareas.map((s) => {
                      const rs = wilson(s.correct, s.n);
                      return (
                        <tr key={s.ref}>
                          <th scope="row" style={{ paddingLeft: 16, fontWeight: 400 }}>
                            {s.ref} {s.title}
                          </th>
                          <td className="mono">
                            {s.correct} of {s.n}
                          </td>
                          <td className="mono">{rs ? `${rs.low}–${rs.high}%` : '—'}</td>
                        </tr>
                      );
                    }),
                  ];
                })}
              </tbody>
            </table>
          }
        >
          <div className="scorerows">
            <div className="measure axisrow" aria-hidden="true">
              <span />
              <StripAxis />
              <span />
            </div>
            {r.areas.map((a) => {
              const ra = wilson(a.correct, a.n);
              return (
                <div key={a.code} className="scoregroup">
                  <div className="measure strong">
                    <span className="mlabel">
                      {a.code} {a.title}
                    </span>
                    <AccuracyStrip
                      point={pct(a.correct, a.n)}
                      low={ra?.low ?? null}
                      high={ra?.high ?? null}
                      label={`Area ${a.code}: ${a.correct} of ${a.n} correct${ra ? `, range ${ra.low} to ${ra.high} percent` : ''}.`}
                    />
                    <span className="mval">
                      {a.correct} of {a.n} · {pct(a.correct, a.n)}%
                    </span>
                  </div>
                  {a.subareas.map((s) => {
                    const rs = wilson(s.correct, s.n);
                    return (
                      <div key={s.ref} className="measure sub">
                        <span className="mlabel">
                          {s.ref} {s.title}
                        </span>
                        <AccuracyStrip
                          compact
                          point={pct(s.correct, s.n)}
                          low={rs?.low ?? null}
                          high={rs?.high ?? null}
                          label={`Subárea ${s.ref}: ${s.correct} of ${s.n} correct.`}
                        />
                        <span className="mval">
                          {s.correct} of {s.n}
                        </span>
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </ChartFrame>
      ) : null}

      <section className="stack" aria-labelledby="mock-review-h">
        <div className="row between">
          <h3 id="mock-review-h">Review</h3>
          <label className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={onlyWrong} onChange={(e) => setOnlyWrong(e.target.checked)} />
            only wrong or unanswered ({wrong})
          </label>
        </div>
        {shown.length ? null : <p className="small muted">Nothing wrong. Every answer was correct.</p>}
        <ol className="reviewlist">
          {shown.map(({ it, n }) => (
            <li key={it.item_id} className="card tight">
              <div className="row between">
                <span className="eyebrow">
                  {n}. {conceptLabel(it.ref, it.node_title)}
                  {it.area_code && titleOf.get(it.area_code) ? ` · area ${it.area_code}` : ''}
                </span>
                <span className={`vtag ${it.correct ? 'good' : it.your_answer ? 'bad' : 'none'}`}>
                  <b aria-hidden="true">{it.correct ? '✓' : it.your_answer ? '✕' : '○'}</b>{' '}
                  {it.correct ? 'correct' : it.your_answer ? 'wrong' : 'not answered'}
                </span>
              </div>
              <Markdown className="prose">{it.stem}</Markdown>
              <ul className="optlist marked">
                {it.options.map((o) => {
                  const isKey = o.key === it.correct_answer.key;
                  const isMine = o.key === it.your_answer?.key;
                  return (
                    <li key={o.key} className={isKey ? 'right' : isMine ? 'wrong' : ''}>
                      <span className="mono">{o.key})</span> <Markdown inline>{o.text}</Markdown>
                      {isKey ? <span className="opt-tag right">correct answer</span> : null}
                      {isMine ? <span className={`opt-tag ${isKey ? 'right' : 'wrong'}`}>your answer</span> : null}
                    </li>
                  );
                })}
              </ul>
              {it.explanation ? (
                <div className="small">
                  <Markdown>{it.explanation}</Markdown>
                </div>
              ) : null}
            </li>
          ))}
        </ol>
      </section>
    </div>
  );
}
