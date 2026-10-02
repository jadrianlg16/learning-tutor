'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { conceptLabel } from '@/lib/progress';
import { allowShortcut, when } from '@/lib/study';
import type {
  Blueprint,
  PracticeAnswerResponse,
  PracticeNextResponse,
  PracticeQuestion,
  ReviewItem,
} from '@/lib/types';
import { ErrorNote, Loading, StatePill } from './Bits';
import { Markdown } from './Markdown';
import { Collapsible } from './Panels';

// Same rule as QuestionCard: an item that carries its own "I do not know" option is not
// offered a second one.
const IDK_RE = /\b(i (do not|don't) know)\b/i;

/** "" = mixed; otherwise an area code ("3") or a subárea ref ("3.2"). Remembered per goal. */
const focusKey = (goalId: string) => `lt-practice-focus:${goalId}`;

/**
 * Practice from the question bank: due reviews first, then new questions up to the daily
 * cap - interleaved across the exam blueprint by weight ("Mixed"), or narrowed to one area
 * or subárea with the focus selector. Grading is server-side - the page gets the key only in
 * the answer's response, and sends back the option `order` it showed - and the feedback says
 * out loud whether the answer counts: only a question that passed a blind check by a solver
 * other than its author is evidence. Everything else is practice.
 */
export function PracticePanel({
  goalId,
  rejected,
  blueprint,
  blueprintReady = true,
  initialFocus,
  onChange,
}: {
  goalId: string;
  /** How many questions the checker disagreed with (from GET /study), for the panel title. */
  rejected: number;
  /** The goal's exam blueprint; the focus selector is hidden without one. */
  blueprint: Blueprint | null;
  /**
   * False while the page still does not know whether there is a blueprint: the first
   * question waits for it, so a stored focus is applied before anything is served.
   */
  blueprintReady?: boolean;
  /** A focus handed over by another tab ("practise area 2"); wins over the stored one. */
  initialFocus?: string | null;
  /** Called after every recorded answer, so the page's counts can refresh. */
  onChange: () => void;
}) {
  const [q, setQ] = useState<PracticeQuestion | null>(null);
  const [counts, setCounts] = useState<PracticeNextResponse['counts'] | null>(null);
  // null until the stored focus has been read (after mount; the export is prerendered).
  const [focus, setFocus] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<PracticeAnswerResponse | null>(null);
  const [choice, setChoice] = useState<string | null>(null);
  const [idk, setIdk] = useState(false);
  const [confidence, setConfidence] = useState<number | null>(null);
  const nextRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    let f = '';
    try {
      if (initialFocus === undefined || initialFocus === null) {
        f = window.localStorage.getItem(focusKey(goalId)) ?? '';
      } else {
        // "practise area 2" from the Progress tab becomes the remembered choice too.
        f = initialFocus;
        window.localStorage.setItem(focusKey(goalId), f);
      }
    } catch {
      f = initialFocus ?? '';
    }
    setFocus(f);
  }, [goalId, initialFocus]);

  // A stored focus the blueprint does not have (or no blueprint at all) falls back to mixed.
  const focusValid = useMemo(() => {
    if (!focus || !blueprint) return '';
    const known = blueprint.areas.some(
      (a) => a.code === focus || a.subareas.some((x) => x.ref === focus || x.node_id === focus),
    );
    return known ? focus : '';
  }, [focus, blueprint]);

  const chooseFocus = (f: string) => {
    setFocus(f);
    try {
      window.localStorage.setItem(focusKey(goalId), f);
    } catch {
      /* storage disabled: the focus still applies for this page view */
    }
  };

  const next = useCallback(async () => {
    if (focus === null || !blueprintReady) return;
    setLoading(true);
    setError(null);
    try {
      const r = await api.practiceNext(goalId, 1, focusValid);
      setQ(r.questions[0] ?? null);
      setCounts(r.counts);
      setDone(r.done || !r.questions.length);
      setNote(r.note);
      setResult(null);
      setChoice(null);
      setIdk(false);
      setConfidence(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [goalId, focus, focusValid, blueprintReady]);

  useEffect(() => {
    void next();
  }, [next]);

  // After an answer, focus goes to "Next question": Enter moves on, and a screen reader lands
  // right after the feedback it has to read.
  useEffect(() => {
    if (result) nextRef.current?.focus();
  }, [result]);

  const hasIdkOption = useMemo(() => !!q?.options.some((o) => IDK_RE.test(o.text)), [q]);
  const locked = busy || !!result;
  const canSubmit = !!q && !locked && (choice !== null || idk);

  const pick = useCallback(
    (key: string) => {
      if (locked || !q) return;
      setChoice(key);
      setIdk(hasIdkOption && IDK_RE.test(q.options.find((o) => o.key === key)?.text ?? ''));
    },
    [locked, q, hasIdkOption],
  );

  const submit = useCallback(async () => {
    if (!q || !canSubmit) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api.practiceAnswer(goalId, {
        item_id: q.item_id,
        response: idk && !choice ? 'IDK' : (choice as string),
        // The shown option order: the key is a letter of what was on screen.
        order: q.order,
        confidence: confidence ?? undefined,
        idk: idk || undefined,
      });
      setResult(r);
      onChange();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [q, canSubmit, goalId, idk, choice, confidence, onChange]);

  // A-E pick, 1-5 set (or unset) confidence, Enter submits and then moves on.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (!q || loading || !allowShortcut(ev)) return;
      if (ev.key === 'Enter') {
        if (result) {
          ev.preventDefault();
          void next();
        } else if (canSubmit) {
          ev.preventDefault();
          void submit();
        }
        return;
      }
      if (locked) return;
      const opt = q.options.find((o) => o.key.toUpperCase() === ev.key.toUpperCase());
      if (opt) {
        ev.preventDefault();
        pick(opt.key);
        return;
      }
      if (/^[1-5]$/.test(ev.key)) {
        ev.preventDefault();
        const n = Number(ev.key);
        setConfidence((c) => (c === n ? null : n));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [q, loading, result, canSubmit, locked, next, submit, pick]);

  const lastKey = q?.options[q.options.length - 1]?.key ?? 'E';

  return (
    <div className="stack gap-lg">
      <div className="stack gap-sm">
        <p className="small muted" style={{ maxWidth: '68ch' }}>
          Due reviews first, then new questions up to a daily cap so reviews never pile up. The
          answer key stays on the server until you have answered.
        </p>
        {blueprint ? (
          <FocusPicker blueprint={blueprint} value={focusValid} onChange={chooseFocus} />
        ) : null}
        {counts ? (
          <p className="small mono muted" aria-live="polite">
            {counts.focus && counts.focus.kind !== 'mixed' ? `focus: ${counts.focus.label} · whole bank: ` : ''}
            due now {counts.due_now} &middot; new today {counts.new_today} of {counts.new_limit}
            {counts.new_available ? ` · ${counts.new_available} new waiting` : ''}
          </p>
        ) : null}
      </div>

      <ErrorNote message={error} />
      {loading && !q ? <Loading what="the next question" /> : null}

      {!loading && done ? (
        <div className="card">
          <span className="eyebrow">practice</span>
          <h2>Nothing due right now</h2>
          <p className="small muted" style={{ maxWidth: '64ch' }}>
            {note ??
              'Every review is scheduled for later and there are no new questions left for today.'}{' '}
            That is the schedule working, not a gap: come back when reviews fall due.
          </p>
        </div>
      ) : null}

      {q && !done ? (
        <section className="qcard" aria-label="Practice question">
          <div className="qhead">
            <span className="stack" style={{ gap: 2 }}>
              <span className="eyebrow">
                practice &middot; {conceptLabel(q.ref, q.node_title)}
              </span>
              {q.area ? (
                <span className="small muted">
                  Area {q.area.code} &middot; {q.area.title}
                </span>
              ) : null}
            </span>
            <span className="row" style={{ gap: 6 }}>
              <span className="pill">{q.reason === 'due' ? 'due review' : 'new'}</span>
              {q.context === 'delayed' ? (
                <span className="pill accent" title="Last answered long enough ago to count as a delayed retrieval">
                  delayed
                </span>
              ) : null}
              {!q.checked ? (
                <span
                  className="pill fragile"
                  title="No independent solver has checked this question's key yet. Practice only."
                >
                  unchecked
                </span>
              ) : null}
            </span>
          </div>

          <div className="qstem">
            <Markdown className="prose">{q.stem}</Markdown>
          </div>

          <div className="options" role="group" aria-label="Options" data-enter-submits>
            {q.options.map((o) => {
              const isKey = result?.correct_answer.key === o.key;
              const isMine = result?.your_answer?.key === o.key;
              const verdict = result ? (isKey ? ' right' : isMine ? ' wrong' : '') : '';
              return (
                <button
                  type="button"
                  key={o.key}
                  className={`opt${choice === o.key ? ' selected' : ''}${verdict}`}
                  aria-pressed={choice === o.key}
                  disabled={locked}
                  onClick={() => pick(o.key)}
                >
                  <span className="key" aria-hidden="true">
                    {o.key}
                  </span>
                  <span>
                    <Markdown inline>{o.text.replace(new RegExp(`^${o.key}[.)]\\s*`), '')}</Markdown>
                    {isKey ? <span className="opt-tag right">correct answer</span> : null}
                    {isMine && !isKey ? <span className="opt-tag wrong">your answer</span> : null}
                  </span>
                </button>
              );
            })}
            {q.allow_idk && !hasIdkOption ? (
              <button
                type="button"
                className={`opt idk${idk ? ' selected' : ''}`}
                aria-pressed={idk}
                disabled={locked}
                onClick={() => {
                  setChoice(null);
                  setIdk(true);
                }}
              >
                <span className="key" aria-hidden="true">
                  ?
                </span>
                <span>I do not know</span>
              </button>
            ) : null}
          </div>

          <div className="stack gap-sm">
            <span className="eyebrow" id="practice-conf">
              How confident? 1 (guess) to 5 (certain) &middot; optional
            </span>
            <div className="confidence" role="group" aria-labelledby="practice-conf" data-enter-submits>
              {[1, 2, 3, 4, 5].map((n) => (
                <button
                  key={n}
                  type="button"
                  className={confidence === n ? 'on' : ''}
                  aria-pressed={confidence === n}
                  disabled={locked}
                  onClick={() => setConfidence((c) => (c === n ? null : n))}
                >
                  {n}
                </button>
              ))}
            </div>
          </div>

          {result ? (
            <PracticeFeedback r={result} nodeTitle={q.node_title} />
          ) : (
            <div className="row between">
              <span className="small muted mono">
                A-{lastKey} pick &middot; 1-5 confidence &middot; Enter submits
              </span>
              <button type="button" className="btn primary" disabled={!canSubmit} onClick={submit}>
                {busy ? 'recording...' : 'Submit answer'}
              </button>
            </div>
          )}

          {result ? (
            <div className="row">
              <button
                type="button"
                ref={nextRef}
                className="btn primary"
                disabled={loading}
                onClick={() => void next()}
              >
                {loading ? 'loading...' : 'Next question'}
              </button>
              <span className="small muted mono">Enter</span>
            </div>
          ) : null}
        </section>
      ) : null}

      <Collapsible
        title={`Questions the checker disagreed with (${rejected})`}
        storageKey={`study-review:${goalId}`}
        defaultOpen={false}
        subtitle="Out of practice until someone rules on them. Keys stay hidden until you ask."
      >
        <ReviewList goalId={goalId} />
      </Collapsible>
    </div>
  );
}

/**
 * Mixed (the default: new questions interleave across the blueprint by exam weight), one
 * area, or one subárea. A select, not buttons: 19 choices, and it stays one row on a phone.
 */
function FocusPicker({
  blueprint,
  value,
  onChange,
}: {
  blueprint: Blueprint;
  value: string;
  onChange: (f: string) => void;
}) {
  return (
    <label className="focuspick">
      <span className="eyebrow">focus</span>
      <select value={value} onChange={(e) => onChange(e.target.value)} aria-describedby="focus-hint">
        <option value="">Mixed — every area, by exam weight</option>
        <optgroup label="One area">
          {blueprint.areas.map((a) => (
            <option key={a.code} value={a.code}>
              {a.code} {a.title} ({a.exam_items} items)
            </option>
          ))}
        </optgroup>
        {blueprint.areas.map((a) => (
          <optgroup key={a.code} label={`Area ${a.code} subáreas`}>
            {a.subareas.map((x) => (
              <option key={x.ref} value={x.ref}>
                {x.ref} {x.title}
              </option>
            ))}
          </optgroup>
        ))}
      </select>
      <span id="focus-hint" className="small muted">
        {value
          ? 'Only this part of the blueprint, for a first pass. Switch back to Mixed to interleave.'
          : 'New questions go to whichever part of the exam you have seen least for its weight.'}
      </span>
    </label>
  );
}

function PracticeFeedback({ r, nodeTitle }: { r: PracticeAnswerResponse; nodeTitle: string }) {
  const tone = r.idk ? 'warn' : r.correct ? 'good' : 'bad';
  return (
    <div className="stack" role="status">
      <p className={`note ${tone}`}>
        <strong>{r.idk ? 'Recorded as "I do not know".' : r.correct ? 'Correct.' : 'Not quite.'}</strong>{' '}
        {r.your_answer ? (
          <>
            Your answer: <strong>{r.your_answer.key}</strong>{' '}
            <Markdown inline>{r.your_answer.text}</Markdown>.{' '}
          </>
        ) : null}
        Correct answer: <strong>{r.correct_answer.key}</strong>{' '}
        <Markdown inline>{r.correct_answer.text}</Markdown>.
      </p>

      {r.explanation ? <Markdown>{r.explanation}</Markdown> : null}

      {r.counts_toward_mastery ? (
        <p className="row small">
          <span className="pill known">counts toward mastery</span>
          <span className="muted">
            This question passed an independent check, so the answer is evidence.
          </span>
          <span className="muted">{nodeTitle} is now</span>
          <StatePill state={r.node_state.state} />
        </p>
      ) : !r.checked ? (
        <p className="row small">
          <span className="pill fragile">practice only</span>
          <span>
            Not yet verified by an independent checker &mdash; practice only, not proof.
          </span>
        </p>
      ) : (
        <p className="row small">
          <span className="pill">does not count</span>
          <span className="muted">Recorded for scheduling; this answer is not evidence.</span>
        </p>
      )}

      <p className="small muted mono">
        {r.context === 'delayed' ? 'delayed retrieval · ' : ''}
        {r.schedule ? `next review ${when(r.schedule.due)}` : 'not scheduled'}
      </p>
      {r.note ? <p className="small muted">{r.note}</p> : null}
    </div>
  );
}

/**
 * Questions a blind solver disagreed with. They carry their keys (the learner is asked to
 * judge them), so each key, the solver's pick and the notes stay out of the DOM until the
 * learner asks for them one item at a time.
 */
function ReviewList({ goalId }: { goalId: string }) {
  const [items, setItems] = useState<ReviewItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [shown, setShown] = useState<Set<string>>(new Set());

  useEffect(() => {
    api
      .practiceReview(goalId)
      .then((r) => setItems(r.items))
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  const toggle = (id: string) =>
    setShown((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  if (error) return <ErrorNote message={error} />;
  if (!items) return <Loading what="the disputed questions" />;
  if (!items.length) {
    return <p className="small muted">None. Every checked question agreed with its key.</p>;
  }

  return (
    <div className="stack">
      <p className="small muted" style={{ maxWidth: '68ch' }}>
        A solver that is not the author answered these blind and did not agree with the key, so
        none of them is served for practice. Judge them yourself: open one only when you have
        decided what you think.
      </p>
      {items.map((it) => {
        const open = shown.has(it.item_id);
        return (
          <article key={it.item_id} className="card tight">
            <div className="row between">
              <span className="eyebrow">{it.node_title}</span>
              {it.ambiguous ? <span className="pill fragile">flagged ambiguous</span> : null}
            </div>
            <Markdown className="prose">{it.stem}</Markdown>
            <ul className="optlist">
              {it.options.map((o) => (
                <li key={o.key}>
                  <span className="mono">{o.key})</span> <Markdown inline>{o.text}</Markdown>
                </li>
              ))}
            </ul>
            <div>
              <button
                type="button"
                className="btn sm"
                aria-expanded={open}
                onClick={() => toggle(it.item_id)}
              >
                {open ? 'hide answer' : 'show answer'}
              </button>
            </div>
            {open ? (
              <div className="stack gap-sm">
                <p className="small">
                  Key: <strong>{it.answer.key}</strong> <Markdown inline>{it.answer.text}</Markdown>
                </p>
                <p className="small">
                  Solver answered:{' '}
                  {it.solver_answer ? (
                    <>
                      <strong>{it.solver_answer.key}</strong>{' '}
                      <Markdown inline>{it.solver_answer.text}</Markdown>
                    </>
                  ) : (
                    'no answer (abstained)'
                  )}
                </p>
                {it.notes ? (
                  <p className="small muted">
                    <Markdown inline>{it.notes}</Markdown>
                  </p>
                ) : null}
                {it.explanation ? <Markdown>{it.explanation}</Markdown> : null}
              </div>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}
