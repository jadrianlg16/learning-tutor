'use client';

import type { NodeStateName, Phase } from '@/lib/types';

export function StatePill({ state }: { state: NodeStateName }) {
  return (
    <span className={`pill ${state}`}>
      <i className="dot" aria-hidden="true" />
      {state}
    </span>
  );
}

// Order per CONTRACTS.md `phase: grounding|plan|probe|teach|done`: the plan is built first
// (it is what the probe questions are drawn against), then the probe locates the edge.
const PHASES: Phase[] = ['grounding', 'plan', 'probe', 'teach', 'done'];

/**
 * The phase rail, so the learner always knows where they are - and can step *back* into a
 * finished phase to re-read it. Never forward: a phase you have not reached is inert, and
 * says so, because clicking your way past the probe is exactly the shortcut this tool
 * refuses to offer.
 */
export function PhaseRail({
  phase,
  viewing,
  onReview,
}: {
  /** The goal's real phase. */
  phase: Phase;
  /** The phase currently on screen; equals `phase` unless the learner is reviewing. */
  viewing?: Phase;
  onReview?: (p: Phase) => void;
}) {
  const i = PHASES.indexOf(phase);
  const shown = PHASES.indexOf(viewing ?? phase);
  return (
    <ol className="phaserail" aria-label={`Phase: ${phase}`} style={{ margin: 0, padding: 0 }}>
      {PHASES.map((p, n) => {
        const reachable = n <= i;
        const classes = [
          'ph',
          n < i ? 'done' : '',
          n === i ? 'now' : '',
          n === shown && n !== i ? 'viewing' : '',
          reachable && onReview ? 'clickable' : '',
        ]
          .filter(Boolean)
          .join(' ');
        return (
          <li key={p} aria-current={n === i ? 'step' : undefined} style={{ listStyle: 'none' }}>
            <button
              type="button"
              className={classes}
              disabled={!reachable || !onReview}
              title={
                reachable
                  ? n < i
                    ? `review ${p} (read-only)`
                    : `you are here: ${p}`
                  : `not reached yet: ${p}`
              }
              onClick={() => onReview?.(p)}
            >
              {p}
            </button>
          </li>
        );
      })}
    </ol>
  );
}

export function Loading({ what }: { what: string }) {
  return (
    <p className="spinner" role="status">
      loading {what}...
    </p>
  );
}

export function ErrorNote({ message }: { message: string | null }) {
  if (!message) return null;
  return (
    <p className="note bad" role="alert">
      {message}
    </p>
  );
}
