'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import type { Question } from '@/lib/types';
import { HINT_LEVEL_NAMES } from '@/lib/types';
import { Markdown } from './Markdown';

export interface AnswerPayload {
  response: string;
  confidence?: number;
  idk?: boolean;
}

interface Props {
  question: Question;
  /** Probe progress. Omitted for a teach checkpoint. */
  asked?: number;
  budget?: number;
  busy?: boolean;
  /** Locks the card after an answer is recorded. */
  answered?: boolean;
  assistanceLevel?: number;
  onSubmit: (payload: AnswerPayload) => void;
  /** Hint ladder / dispute menu / reveal, rendered under the options. */
  footer?: React.ReactNode;
  label?: string;
}

const IDK_RE = /\b(i (do not|don't) know)\b/i;

export function QuestionCard({
  question,
  asked,
  budget,
  busy = false,
  answered = false,
  assistanceLevel,
  onSubmit,
  footer,
  label = 'Question',
}: Props) {
  const [choice, setChoice] = useState<string | null>(null);
  const [confidence, setConfidence] = useState<number | null>(null);
  const [idk, setIdk] = useState(false);

  useEffect(() => {
    setChoice(null);
    setConfidence(null);
    setIdk(false);
  }, [question.item_version_id, question.item_id]);

  // Some generated items carry "E. I do not know" as a real option; do not offer it twice.
  const hasIdkOption = useMemo(
    () => question.options.some((o) => IDK_RE.test(o.text)),
    [question.options],
  );
  const showIdkButton = question.allow_idk && !hasIdkOption;

  const locked = busy || answered;

  const pick = useCallback(
    (key: string) => {
      if (locked) return;
      setChoice(key);
      setIdk(hasIdkOption && IDK_RE.test(question.options.find((o) => o.key === key)?.text ?? ''));
    },
    [locked, hasIdkOption, question.options],
  );

  const pickIdk = useCallback(() => {
    if (locked) return;
    setChoice(null);
    setIdk(true);
  }, [locked]);

  // A-E select an option, 1-5 set confidence, Enter submits. The phone surface still
  // works by tapping; this is for the desk.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (locked) return;
      const t = ev.target as HTMLElement | null;
      if (t && /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName)) return;
      if (ev.metaKey || ev.ctrlKey || ev.altKey) return;
      const k = ev.key.toUpperCase();
      const opt = question.options.find((o) => o.key.toUpperCase() === k);
      if (opt) {
        ev.preventDefault();
        pick(opt.key);
        return;
      }
      if (question.ask_confidence && /^[1-5]$/.test(ev.key)) {
        ev.preventDefault();
        setConfidence(Number(ev.key));
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [locked, pick, question.options, question.ask_confidence]);

  const needConfidence = question.ask_confidence && confidence === null;
  const canSubmit = !locked && (choice !== null || idk) && !needConfidence;

  const submit = () => {
    if (!canSubmit) return;
    onSubmit({
      response: idk && !choice ? 'IDK' : (choice as string),
      confidence: question.ask_confidence ? (confidence ?? undefined) : undefined,
      idk: idk || undefined,
    });
  };

  return (
    // id: deep links (and the screenshot script) can jump straight to the question.
    <section className="qcard" aria-label={label} id="checkpoint">
      <div className="qhead">
        <span className="eyebrow">
          {label} &middot; {question.node_title} &middot; {question.kind}
        </span>
        {typeof asked === 'number' && typeof budget === 'number' ? (
          <span className="mono small muted" aria-label={`${asked} of ${budget} asked`}>
            {asked}/{budget} asked
          </span>
        ) : null}
      </div>

      {typeof asked === 'number' && typeof budget === 'number' && budget > 0 ? (
        <div
          className="progress"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={budget}
          aria-valuenow={asked}
        >
          <i style={{ width: `${Math.min(100, (asked / budget) * 100)}%` }} />
        </div>
      ) : null}

      <div className="qstem">
        <Markdown className="prose">{question.stem}</Markdown>
      </div>

      <div className="options" role="group" aria-label="Options">
        {question.options.map((o) => (
          <button
            type="button"
            key={o.key}
            className={`opt${choice === o.key ? ' selected' : ''}${IDK_RE.test(o.text) ? ' idk' : ''}`}
            aria-pressed={choice === o.key}
            disabled={locked}
            onClick={() => pick(o.key)}
          >
            <span className="key" aria-hidden="true">
              {o.key}
            </span>
            <Markdown inline>{o.text.replace(new RegExp(`^${o.key}[.)]\\s*`), '')}</Markdown>
          </button>
        ))}

        {showIdkButton ? (
          <button
            type="button"
            className={`opt idk${idk ? ' selected' : ''}`}
            aria-pressed={idk}
            disabled={locked}
            onClick={pickIdk}
          >
            <span className="key" aria-hidden="true">
              ?
            </span>
            <span>I do not know</span>
          </button>
        ) : null}
      </div>

      {question.ask_confidence ? (
        <div className="stack gap-sm">
          <span className="eyebrow" id="conf-label">
            How confident? 1 (guess) to 5 (certain)
          </span>
          <div className="confidence" role="group" aria-labelledby="conf-label">
            {[1, 2, 3, 4, 5].map((n) => (
              <button
                key={n}
                type="button"
                className={confidence === n ? 'on' : ''}
                aria-pressed={confidence === n}
                disabled={locked}
                onClick={() => setConfidence(n)}
              >
                {n}
              </button>
            ))}
          </div>
        </div>
      ) : null}

      <div className="row between">
        <span className="small muted mono">
          assistance {assistanceLevel ?? question.assistance_level} &middot;{' '}
          {HINT_LEVEL_NAMES[assistanceLevel ?? question.assistance_level] ?? 'none'}
          {(assistanceLevel ?? question.assistance_level) >= 5
            ? ' - will not count toward mastery'
            : ''}
        </span>
        <button type="button" className="btn primary" disabled={!canSubmit} onClick={submit}>
          {busy ? 'recording...' : 'Submit answer'}
        </button>
      </div>

      {needConfidence && (choice !== null || idk) ? (
        <p className="small muted">Pick a confidence level to submit.</p>
      ) : null}

      {footer}
    </section>
  );
}
