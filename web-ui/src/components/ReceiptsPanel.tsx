'use client';

import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { ExamNode } from '@/lib/mapBlueprint';
import { HINT_LEVEL_NAMES, type ReceiptsResponse } from '@/lib/types';
import { ErrorNote, Loading, StatePill } from './Bits';
import { ExamNumbers } from './MapExam';

/**
 * Evidence receipts: "why is this fragile?" answered with rows you can point at.
 *
 * Deliberately shows counts, dates, assistance level and evaluation method - never a
 * mastery probability. `learner.md` shows evidence, never decimals, and so does this.
 */
export function ReceiptsPanel({
  goalId,
  nodeId,
  onClose,
  flat = false,
  exam = null,
}: {
  goalId: string;
  nodeId: string | null;
  onClose?: () => void;
  /** Inside the map drawer the surrounding chrome is the drawer's, not a card's. */
  flat?: boolean;
  /** The concept is a blueprint subárea: its exam numbers go above the receipts. */
  exam?: { info: ExamNode; hasProgress: boolean } | null;
}) {
  const [data, setData] = useState<ReceiptsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!nodeId) {
      setData(null);
      return;
    }
    let live = true;
    setLoading(true);
    setError(null);
    api
      .receipts(goalId, nodeId)
      .then((d) => live && setData(d))
      .catch((e) => live && setError(errorMessage(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [goalId, nodeId]);

  const shell = flat ? 'receipts flat' : 'card receipts';

  if (!nodeId) {
    return (
      <aside className={shell}>
        <span className="eyebrow">receipts</span>
        <p className="muted small">
          Click any concept on either graph to see the evidence behind its colour.
        </p>
      </aside>
    );
  }

  return (
    <aside className={shell} id="receipts" aria-live="polite">
      {flat ? null : (
        <div className="row between">
          <span className="eyebrow">receipts</span>
          {onClose ? (
            <button type="button" className="btn sm" onClick={onClose}>
              close
            </button>
          ) : null}
        </div>
      )}

      {exam ? (
        <>
          <ExamNumbers info={exam.info} hasProgress={exam.hasProgress} />
          <hr className="rule-hr" />
        </>
      ) : null}

      <ErrorNote message={error} />
      {loading ? <Loading what="receipts" /> : null}

      {data ? (
        <>
          <div className="row between">
            <h2>{data.node.title}</h2>
            <StatePill state={data.state.state} />
          </div>

          <div className="table-wrap">
            <table>
              <caption className="eyebrow" style={{ textAlign: 'left', paddingBottom: 6 }}>
                state detail
              </caption>
              <tbody>
                <tr>
                  <td className="mono muted">independent passes</td>
                  <td className="mono">{data.state.independent_passes}</td>
                </tr>
                <tr>
                  <td className="mono muted">assisted passes</td>
                  <td className="mono">{data.state.assisted_passes}</td>
                </tr>
                <tr>
                  <td className="mono muted">self-graded passes</td>
                  <td className="mono">{data.state.self_graded_passes}</td>
                </tr>
                <tr>
                  <td className="mono muted">fails</td>
                  <td className="mono">{data.state.fails}</td>
                </tr>
                <tr>
                  <td className="mono muted">transfer passes</td>
                  <td className="mono">{data.state.transfer_passes}</td>
                </tr>
                <tr>
                  <td className="mono muted">last delayed retrieval</td>
                  <td className="mono">{data.state.last_delayed ?? 'none yet'}</td>
                </tr>
                <tr>
                  <td className="mono muted">uncertainty</td>
                  <td className="mono">{data.state.uncertainty}</td>
                </tr>
              </tbody>
            </table>
          </div>

          {data.why.length ? (
            <div className="stack gap-sm">
              <span className="eyebrow">why this colour</span>
              <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
                {data.why.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <div className="stack gap-sm">
            <span className="eyebrow">evidence ({data.evidence.length})</span>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>when</th>
                    <th>kind</th>
                    <th>result</th>
                    <th>assistance</th>
                    <th>evaluated by</th>
                    <th>context</th>
                  </tr>
                </thead>
                <tbody>
                  {data.evidence.map((e) => (
                    <tr key={e.event_id} className="evidence-row">
                      <td className="mono">{e.ts.slice(0, 10)}</td>
                      <td className="mono">{e.kind}</td>
                      <td>
                        {e.idk ? (
                          <span className="pill unknown">I do not know</span>
                        ) : e.correct === 1 ? (
                          <span className="pill known">pass</span>
                        ) : e.correct === 0 ? (
                          <span className="pill misconception">fail</span>
                        ) : (
                          <span className="pill">-</span>
                        )}
                        {e.confidence ? (
                          <span className="mono small muted"> conf {e.confidence}</span>
                        ) : null}
                      </td>
                      <td className="mono">
                        {e.assistance_level}
                        <span className="muted"> {HINT_LEVEL_NAMES[e.assistance_level] ?? ''}</span>
                      </td>
                      <td className="mono">{e.evaluation_method ?? 'unrecorded'}</td>
                      <td className="mono">{e.context}</td>
                    </tr>
                  ))}
                  {!data.evidence.length ? (
                    <tr>
                      <td colSpan={6} className="muted small">
                        No evidence yet. That is what <code>unknown</code> means here - not a
                        guess about you.
                      </td>
                    </tr>
                  ) : null}
                </tbody>
              </table>
            </div>
            <p className="small muted">
              A pass at assistance 5 or 6 is recorded and never counts toward mastery.
            </p>
          </div>

          {data.misconceptions.length ? (
            <div className="stack gap-sm">
              <span className="eyebrow">misconceptions</span>
              {data.misconceptions.map((m) => (
                <div key={m.misconception_id} className="note bad small">
                  <strong>&ldquo;{m.claim}&rdquo;</strong> &middot; {m.state}
                  {m.steps_held.length ? ` - confirmed by ${m.steps_held.join(' + ')}` : ''}
                </div>
              ))}
            </div>
          ) : null}

          {data.disputes.length ? (
            <div className="stack gap-sm">
              <span className="eyebrow">disputes</span>
              {data.disputes.map((d) => (
                <div key={d.dispute_id} className="note warn small">
                  <strong>{d.type}</strong> &middot; {d.outcome ?? 'open'}
                  <br />
                  {d.note}
                  {d.evidence ? (
                    <>
                      <br />
                      <span className="muted">settled by: {d.evidence}</span>
                    </>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}
        </>
      ) : null}
    </aside>
  );
}
