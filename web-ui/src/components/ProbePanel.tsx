'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { NodeState, Question } from '@/lib/types';
import { ErrorNote, Loading, StatePill } from './Bits';
import { QuestionCard, type AnswerPayload } from './QuestionCard';
import { DisputeMenu } from './DisputeMenu';

/**
 * Phase 1. Graded multiple choice, starting broad, binary-searching the edge of what the
 * learner can do. The probe stops when it has located that edge, which is usually well
 * before the budget - so `asked/budget` is shown, not a countdown to a fixed length.
 */
export function ProbePanel({
  goalId,
  onDone,
  onSession,
}: {
  goalId: string;
  onDone: (sessionId: string) => void;
  onSession: (sessionId: string) => void;
}) {
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [question, setQuestion] = useState<Question | null>(null);
  const [asked, setAsked] = useState(0);
  const [budget, setBudget] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState(false);
  const [lastState, setLastState] = useState<{ title: string; state: NodeState } | null>(null);
  const [started, setStarted] = useState(false);

  const start = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const res = await api.probeStart(goalId);
      setSessionId(res.session_id);
      onSession(res.session_id);
      setBudget(res.budget);
      setAsked(res.asked ?? 0);
      setQuestion(res.question);
      setDone(res.done);
      setStarted(true);
      if (res.done) onDone(res.session_id);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [goalId, onDone, onSession]);

  useEffect(() => {
    if (!started) void start();
  }, [start, started]);

  const answer = async (p: AnswerPayload) => {
    if (!sessionId || !question) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.probeAnswer(goalId, {
        session_id: sessionId,
        item_id: question.item_id,
        response: p.response,
        confidence: p.confidence,
        idk: p.idk,
      });
      setLastState({ title: question.node_title, state: res.node_state });
      setAsked(res.asked);
      setBudget(res.budget);
      setQuestion(res.next);
      setDone(res.done);
      if (res.done) onDone(sessionId);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack gap-lg">
      <div className="stack gap-sm">
        <span className="eyebrow">probe &middot; no teaching yet</span>
        <h2>Finding the edge of what you can already do</h2>
        <p className="muted small" style={{ maxWidth: '64ch' }}>
          Wrong answers here cost nothing and are the fastest way to a map that is about you.
          &ldquo;I do not know&rdquo; is worth more than a guess.
        </p>
      </div>

      <ErrorNote message={error} />

      {lastState ? (
        <p className="row small">
          <span className="muted">{lastState.title} is now</span>
          <StatePill state={lastState.state.state} />
          <span className="muted mono">
            {lastState.state.independent_passes} unaided &middot; {lastState.state.fails} failed
            &middot; uncertainty {lastState.state.uncertainty}
          </span>
        </p>
      ) : null}

      {busy && !question ? <Loading what="the probe" /> : null}

      {question ? (
        <QuestionCard
          question={question}
          asked={asked}
          budget={budget}
          busy={busy}
          label="Probe"
          onSubmit={answer}
          footer={
            <DisputeMenu
              goalId={goalId}
              nodeId={question.node_id}
              itemId={question.item_id}
              // "I already know this" / "test me instead" come back with a check. Serve it
              // right here - a self-report never becomes mastery on its own.
              onCheckItems={(items) => items.length && setQuestion(items[0])}
            />
          }
        />
      ) : null}

      {done ? (
        <div className="banner">
          <span>
            Probe finished after {asked} of a possible {budget} questions - the edge is located.
          </span>
          <span className="pill known">ready to teach</span>
        </div>
      ) : null}
    </div>
  );
}
