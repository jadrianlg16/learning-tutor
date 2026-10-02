'use client';

import { useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { MisconceptionStep, MisconceptionState } from '@/lib/types';
import { ErrorNote } from './Bits';
import { Markdown } from './Markdown';

/**
 * The three-step misconception protocol (skills/teach/prompts/v1/misconception.md):
 * reasoning -> reworded prediction -> counterexample. A suspicion is a hypothesis until
 * all three hold, so the dialog shows the claim and where in the sequence we are, and
 * closes as soon as a step drops it.
 */

const STEPS: MisconceptionStep[] = ['reasoning', 'prediction', 'counterexample'];

const PROMPTS: Record<MisconceptionStep, string> = {
  reasoning: 'Walk me through how you got to that answer - what were you thinking?',
  prediction:
    'Same idea, different wording. What do you predict happens here, and why?',
  counterexample:
    'Here is a case that your rule does not fit. What does that tell you about the rule?',
};

export function MisconceptionDialog({
  goalId,
  sessionId,
  nodeId,
  claim,
  onClose,
}: {
  goalId: string;
  sessionId: string;
  nodeId: string;
  claim: string;
  onClose: () => void;
}) {
  const [step, setStep] = useState<MisconceptionStep>('reasoning');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [state, setState] = useState<MisconceptionState | null>(null);
  const [prompt, setPrompt] = useState<string | null>(null);
  const [done, setDone] = useState(false);

  const send = async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.misconceptionStep(goalId, {
        session_id: sessionId,
        node_id: nodeId,
        claim,
        step,
        learner_response: text,
      });
      setState(res.state);
      setPrompt(res.prompt_markdown ?? null);
      setText('');
      if (res.next_step) setStep(res.next_step);
      else setDone(true);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Misconception check"
      onKeyDown={(e) => {
        if (e.key === 'Escape') onClose();
      }}
    >
      <div className="dialog">
        <div className="row between">
          <span className="eyebrow">misconception check</span>
          <span className={`pill ${state === 'active' ? 'misconception' : 'accent'}`}>
            {state ?? 'suspected'}
          </span>
        </div>

        <h2>Something in your reasoning may be systematic</h2>
        <p className="note warn small">
          Hypothesis, not a verdict: <strong>&ldquo;{claim}&rdquo;</strong>. Nothing is recorded
          against you until all three steps hold.
        </p>

        <ol className="row small mono" style={{ padding: 0, margin: 0, listStyle: 'none' }}>
          {STEPS.map((s, i) => (
            <li key={s}>
              <span className={`pill ${STEPS.indexOf(step) > i || done ? 'known' : s === step ? 'accent' : 'unknown'}`}>
                {i + 1}. {s}
              </span>
            </li>
          ))}
        </ol>

        {done ? (
          <>
            <p className="note good">
              Sequence finished - final state <code>{state}</code>. Only an{' '}
              <code>active</code> misconception shows on your map.
            </p>
            <button type="button" className="btn primary" onClick={onClose}>
              Back to the step
            </button>
          </>
        ) : (
          <>
            {prompt ? <Markdown>{prompt}</Markdown> : <p>{PROMPTS[step]}</p>}
            <label className="field">
              <span className="lbl">your answer &middot; step: {step}</span>
              <textarea
                value={text}
                onChange={(e) => setText(e.target.value)}
                autoFocus
                placeholder="In your own words."
              />
            </label>
            <ErrorNote message={error} />
            <div className="row">
              <button
                type="button"
                className="btn primary"
                disabled={busy || !text.trim()}
                onClick={send}
              >
                {busy ? 'checking...' : 'Send'}
              </button>
              <button type="button" className="btn" onClick={onClose}>
                Not now
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
