'use client';

import { useCallback, useEffect, useState } from 'react';
import { api, ApiError, errorMessage } from '@/lib/api';
import { useHashScroll } from '@/lib/useHashScroll';
import {
  HINT_LEVEL_NAMES,
  type TeachAnswerResponse,
  type TeachBackResponse,
  type TeachDecision,
  type TeachNextResponse,
  formatCitation,
} from '@/lib/types';
import { ErrorNote, Loading, StatePill } from './Bits';
import { DisputeMenu } from './DisputeMenu';
import { Markdown } from './Markdown';
import { MisconceptionDialog } from './MisconceptionDialog';
import { QuestionCard, type AnswerPayload } from './QuestionCard';

const DECISION_TEXT: Record<TeachDecision, string> = {
  continue: 'Moving on to the next concept.',
  repeat: 'Same concept, another angle - that one did not stick yet.',
  back_up: 'Backing up a node: a prerequisite is doing the damage, not this step.',
  switch_strategy: 'Switching strategy. Three in a row means the explanation is wrong, not you.',
  teach_back_due: 'Teach-back due: explain it back before this counts.',
  end_session: 'Ending the session here. Stopping while it is still working is the point.',
};

interface Hint {
  level: number;
  markdown: string;
}

export function TeachPanel({
  goalId,
  sessionId,
  onEnded,
}: {
  goalId: string;
  sessionId: string;
  onEnded: (logPath: string, summary: string) => void;
}) {
  const [data, setData] = useState<TeachNextResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [hints, setHints] = useState<Hint[]>([]);
  const [assistance, setAssistance] = useState(0);
  const [attempted, setAttempted] = useState(false);
  const [result, setResult] = useState<TeachAnswerResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const [stepSvg, setStepSvg] = useState<string | null>(null);

  const [interrupt, setInterrupt] = useState('');
  const [interruptAnswer, setInterruptAnswer] = useState<string | null>(null);
  const [interruptError, setInterruptError] = useState<string | null>(null);
  const [interruptBusy, setInterruptBusy] = useState(false);

  const [teachBackText, setTeachBackText] = useState('');
  const [teachBack, setTeachBack] = useState<TeachBackResponse | null>(null);

  const [miscon, setMiscon] = useState<string | null>(null);
  const [ending, setEnding] = useState(false);

  const loadStep = useCallback(async () => {
    setLoading(true);
    setError(null);
    setHints([]);
    setAssistance(0);
    setAttempted(false);
    setResult(null);
    setStepSvg(null);
    setTeachBack(null);
    setTeachBackText('');
    setInterruptAnswer(null);
    try {
      const res = await api.teachNext(goalId, sessionId);
      setData(res);
      setAssistance(res.assistance_level ?? 0);
      if (res.step.svg) setStepSvg(res.step.svg);
      else if (res.step.mermaid) {
        try {
          const r = await api.render(res.step.mermaid);
          setStepSvg(r.svg);
        } catch {
          /* the diagram is an aid; the step still reads without it */
        }
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [goalId, sessionId]);

  useEffect(() => {
    void loadStep();
  }, [loadStep]);

  // The checkpoint anchor only exists once the step has arrived.
  useHashScroll(!!data);

  const askHint = async (level: number) => {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.teachHint(goalId, {
        session_id: sessionId,
        item_id: data.checkpoint.item_id,
        level,
      });
      setHints((h) => [...h, { level: res.level, markdown: res.hint_markdown }]);
      setAssistance(res.level);
    } catch (e) {
      setError(
        e instanceof ApiError && e.code === 'reveal_before_attempt'
          ? 'The reveal is only available after a genuine attempt. Answer first - a wrong answer is fine.'
          : errorMessage(e),
      );
    } finally {
      setBusy(false);
    }
  };

  const answer = async (p: AnswerPayload) => {
    if (!data) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.teachAnswer(goalId, {
        session_id: sessionId,
        item_id: data.checkpoint.item_id,
        response: p.response,
        confidence: p.confidence,
        idk: p.idk,
        assistance_level: assistance,
      });
      setResult(res);
      setAttempted(true);
      if (res.misconception_suspected) setMiscon(res.misconception_suspected.claim);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const sendInterrupt = async () => {
    if (!data || !interrupt.trim()) return;
    setInterruptBusy(true);
    setInterruptError(null);
    try {
      const res = await api.interrupt(goalId, {
        session_id: sessionId,
        node_id: data.node.id,
        question: interrupt,
      });
      setInterruptAnswer(res.answer_markdown);
      setInterrupt('');
    } catch (e) {
      setInterruptError(
        e instanceof ApiError && (e.status === 404 || e.status === 405)
          ? 'This gateway build does not implement mid-step interrupts yet (POST /teach/interrupt). Your question was not sent anywhere - nothing was silently swallowed.'
          : errorMessage(e),
      );
    } finally {
      setInterruptBusy(false);
    }
  };

  const sendTeachBack = async () => {
    if (!data) return;
    setBusy(true);
    try {
      const res = await api.teachBack(goalId, {
        session_id: sessionId,
        node_id: data.node.id,
        explanation: teachBackText,
      });
      setTeachBack(res);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const endSession = async () => {
    setEnding(true);
    try {
      const res = await api.endSession(goalId, { session_id: sessionId });
      onEnded(res.log_path, res.session.summary ?? '');
    } catch (e) {
      setError(errorMessage(e));
      setEnding(false);
    }
  };

  if (loading && !data) return <Loading what="the next step" />;
  if (!data) return <ErrorNote message={error ?? 'No step available.'} />;

  const nextHintLevel = Math.min(6, assistance + 1);
  const revealBlocked = nextHintLevel === 6 && !attempted;

  return (
    <div className="stack gap-lg">
      {/* ------------------------------------------------------------- step */}
      <article className="card">
        <div className="row between">
          <span className="eyebrow">
            teach &middot; {data.node.title} &middot; strategy: {data.step.strategy}
          </span>
          <DisputeMenu
            goalId={goalId}
            nodeId={data.node.id}
            itemId={data.checkpoint.item_id}
            // A dispute that comes back with a check replaces the checkpoint with it: the
            // claim is settled by answering, not by asserting.
            onCheckItems={(items) => {
              if (!items.length) return;
              setData((d) => (d ? { ...d, checkpoint: items[0] } : d));
              setResult(null);
              setHints([]);
              setAssistance(0);
              setAttempted(false);
            }}
          />
        </div>

        <Markdown>{data.step.markdown}</Markdown>

        {stepSvg ? (
          <figure style={{ margin: 0 }} className="stack gap-sm">
            <div className="graphwrap" dangerouslySetInnerHTML={{ __html: stepSvg }} />
            <figcaption className="small muted">Diagram rendered by the gateway.</figcaption>
          </figure>
        ) : null}

        {data.step.citations.length ? (
          <p className="small muted">
            <strong>Sources:</strong> {data.step.citations.map(formatCitation).join('  |  ')}
          </p>
        ) : (
          <p className="small muted">
            No citation for this step - taught from model knowledge, not from your sources.
          </p>
        )}
      </article>

      {/* --------------------------------------------------------- interrupt */}
      <div className="card tight">
        <span className="eyebrow">wait, why?</span>
        <p className="small muted">
          Interrupt mid-step. This is a conversation, not a slideshow - nothing moves on until
          you are done asking.
        </p>
        <label className="field">
          <span className="lbl">your question</span>
          <textarea
            value={interrupt}
            onChange={(e) => setInterrupt(e.target.value)}
            style={{ minHeight: 60 }}
            placeholder="Why does the output have to be a scalar?"
          />
        </label>
        <div className="row">
          <button
            type="button"
            className="btn sm"
            disabled={interruptBusy || !interrupt.trim()}
            onClick={sendInterrupt}
          >
            {interruptBusy ? 'asking...' : 'Ask'}
          </button>
        </div>
        <ErrorNote message={interruptError} />
        {interruptAnswer ? (
          <div className="note">
            <Markdown>{interruptAnswer}</Markdown>
          </div>
        ) : null}
      </div>

      {/* -------------------------------------------------------- checkpoint */}
      <QuestionCard
        question={data.checkpoint}
        busy={busy}
        answered={!!result}
        assistanceLevel={assistance}
        label="Checkpoint"
        onSubmit={answer}
        footer={
          <div className="stack gap-sm">
            <hr className="rule-hr" />
            <div className="row between">
              <div className="row">
                <button
                  type="button"
                  className="btn sm"
                  disabled={busy || assistance >= 6 || revealBlocked || (nextHintLevel < 6 && !!result)}
                  onClick={() => askHint(nextHintLevel)}
                  title={
                    revealBlocked
                      ? 'Answer first. No reveal before an attempt.'
                      : `Request hint level ${nextHintLevel}`
                  }
                >
                  {nextHintLevel === 6
                    ? 'Reveal the worked solution'
                    : `Hint (level ${nextHintLevel}: ${HINT_LEVEL_NAMES[nextHintLevel]})`}
                </button>
                {revealBlocked ? (
                  <span className="small muted">Reveal unlocks after an attempt.</span>
                ) : null}
              </div>
              <span className="small muted mono">
                highest level reached: {assistance}
              </span>
            </div>

            {hints.map((h) => (
              <div key={h.level} className={`note ${h.level >= 5 ? 'warn' : ''}`}>
                <span className="eyebrow">
                  level {h.level} &middot; {HINT_LEVEL_NAMES[h.level]}
                  {h.level >= 5 ? ' - will not count toward mastery' : ''}
                </span>
                <Markdown>{h.markdown}</Markdown>
              </div>
            ))}
          </div>
        }
      />

      {/* ----------------------------------------------------------- feedback */}
      {result ? (
        <div className={`card ${result.correct ? '' : ''}`}>
          <div className="row between">
            <span className="eyebrow">result</span>
            <div className="row">
              <span className={`pill ${result.correct ? 'known' : 'misconception'}`}>
                {result.correct ? 'pass' : 'not a pass'}
              </span>
              <StatePill state={result.node_state.state} />
            </div>
          </div>

          <Markdown>{result.feedback_markdown}</Markdown>

          <p className="small muted mono">
            recorded: assistance {result.recorded.assistance_level} &middot; evaluated by{' '}
            {result.recorded.evaluation_method ?? 'unrecorded'} &middot; context{' '}
            {result.recorded.context}
          </p>

          {result.misconception_suspected ? (
            <div className="note bad">
              <strong>Possible misconception:</strong> &ldquo;
              {result.misconception_suspected.claim}&rdquo;
              <div className="row" style={{ marginTop: 8 }}>
                <button
                  type="button"
                  className="btn sm"
                  onClick={() => setMiscon(result.misconception_suspected!.claim)}
                >
                  Run the three-step check
                </button>
              </div>
            </div>
          ) : null}

          <div className="banner">
            <span>
              <strong>Decision:</strong> {DECISION_TEXT[result.decision]}
            </span>
            <div className="row">
              {result.decision === 'end_session' ? (
                <button type="button" className="btn primary" disabled={ending} onClick={endSession}>
                  End session
                </button>
              ) : result.decision === 'teach_back_due' ? null : (
                <button type="button" className="btn primary" onClick={loadStep}>
                  Next step
                </button>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {/* ---------------------------------------------------------- teach-back */}
      {result?.decision === 'teach_back_due' ? (
        <div className="card">
          <span className="eyebrow">teach-back &middot; rubric-graded</span>
          <h2>Explain it back, in your own words</h2>
          <p className="small muted">
            Say what the object is, then what it does, then one example. A restatement of the
            step scores 1.
          </p>
          <label className="field">
            <span className="lbl">your explanation of {data.node.title}</span>
            <textarea
              value={teachBackText}
              onChange={(e) => setTeachBackText(e.target.value)}
              style={{ minHeight: 130 }}
            />
          </label>
          <div className="row">
            <button
              type="button"
              className="btn primary"
              disabled={busy || !teachBackText.trim() || !!teachBack}
              onClick={sendTeachBack}
            >
              {busy ? 'grading...' : 'Submit teach-back'}
            </button>
            {teachBack ? (
              <span className="pill accent">
                score {teachBack.score}/3 &middot; {teachBack.rubric_version}
              </span>
            ) : null}
          </div>
          {teachBack ? (
            <>
              <Markdown>{teachBack.feedback_markdown}</Markdown>
              <div className="row">
                <button type="button" className="btn primary" onClick={loadStep}>
                  Next step
                </button>
                <button type="button" className="btn" disabled={ending} onClick={endSession}>
                  End session
                </button>
              </div>
            </>
          ) : null}
        </div>
      ) : null}

      <ErrorNote message={error} />

      <div className="row between">
        <span className="small muted">
          Session <code>{sessionId}</code>
        </span>
        <button type="button" className="btn" disabled={ending} onClick={endSession}>
          {ending ? 'closing...' : 'End session'}
        </button>
      </div>

      {miscon ? (
        <MisconceptionDialog
          goalId={goalId}
          sessionId={sessionId}
          nodeId={data.node.id}
          claim={miscon}
          onClose={() => setMiscon(null)}
        />
      ) : null}
    </div>
  );
}
