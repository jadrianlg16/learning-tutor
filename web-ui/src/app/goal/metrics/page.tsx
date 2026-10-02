'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { MetricsResponse } from '@/lib/types';
import { ErrorNote, Loading } from '@/components/Bits';

/**
 * The product gate, made visible. Delayed holdout performance is *the* metric; everything
 * else is context. A metric with no observations behind it shows as "not measurable yet"
 * rather than as a zero.
 */

const EXPLAIN: Record<string, string> = {
  holdout_success_7d:
    'Share of holdout items answered correctly after a delay. This is the metric the whole thing is judged on.',
  false_mastery_rate:
    'How often a node called known then failed a delayed or transfer check. Lower is honest.',
  item_rejection_rate:
    'Share of generated items rejected in validation before they were ever allowed to carry evidence.',
  mean_assistance:
    'Mean hint level across recorded answers. Falling over time is the self-removing tutor working.',
  sessions: 'Sessions recorded for this learner model.',
  events_total: 'Rows in the append-only event log.',
};

function fmt(v: unknown): string {
  if (v === null || v === undefined) return 'not measurable yet';
  if (typeof v === 'number') return Number.isInteger(v) ? String(v) : v.toFixed(2);
  return String(v);
}

function MetricsScreen() {
  const params = useSearchParams();
  const goalId = params.get('g');
  const [data, setData] = useState<MetricsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!goalId) return;
    api
      .metrics(goalId)
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  if (!goalId) {
    return (
      <main className="main" id="main">
        <p className="note warn">
          No goal selected. <Link href="/">Pick one.</Link>
        </p>
      </main>
    );
  }

  // <key>_n and <key>_note are annotations on another row, not rows of their own.
  const rows = data
    ? Object.entries(data).filter(
        ([k]) => !k.endsWith('_note') && !k.endsWith('_n') && k !== 'measured_at',
      )
    : [];
  const measuredAt = typeof data?.measured_at === 'string' ? data.measured_at : null;

  return (
    <main className="main" id="main">
      <div className="stack gap-sm">
        <span className="eyebrow">metrics</span>
        <h1>Is any of this working?</h1>
        <p className="muted small" style={{ maxWidth: '66ch' }}>
          Numbers carry the observation count that produced them. A rate over three
          observations is not a rate.
        </p>
      </div>

      <ErrorNote message={error} />
      {!data && !error ? <Loading what="metrics" /> : null}

      {data ? (
        <div className="card">
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>metric</th>
                  <th>value</th>
                  <th>n</th>
                  <th>what it means</th>
                </tr>
              </thead>
              <tbody>
                {rows.map(([k, v]) => (
                  <tr key={k}>
                    <td className="mono">{k}</td>
                    <td className="mono">
                      {v === null ? (
                        <span className="pill unknown">not measurable yet</span>
                      ) : (
                        fmt(v)
                      )}
                    </td>
                    <td className="mono">{data[`${k}_n`] == null ? '-' : fmt(data[`${k}_n`])}</td>
                    <td className="small muted">
                      {[EXPLAIN[k], data[`${k}_note`] as string | undefined]
                        .filter(Boolean)
                        .join(' ')}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="small muted">
            {measuredAt ? `Measured at ${measuredAt}. ` : ''}Passed straight through from{' '}
            <code>learner-svc</code>. This page renders whatever keys the gateway sends, so a
            new metric appears here with no UI change: <code>x_n</code> lands in the n column
            and <code>x_note</code> next to the explanation.
          </p>
        </div>
      ) : null}

      <div className="row">
        <Link className="btn" href={`/goal/?g=${encodeURIComponent(goalId)}`}>
          Back to the session
        </Link>
        <Link className="btn" href="/passport/">
          Export the passport
        </Link>
      </div>
    </main>
  );
}

export default function MetricsPage() {
  return (
    <Suspense
      fallback={
        <main className="main" id="main">
          <Loading what="metrics" />
        </main>
      }
    >
      <MetricsScreen />
    </Suspense>
  );
}
