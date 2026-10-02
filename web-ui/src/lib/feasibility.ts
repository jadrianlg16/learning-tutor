import type { Feasibility, Goal, NodeState } from './types';

/** Minutes budgeted per not-yet-known concept: teach step + checkpoint + teach-back. */
export const MINUTES_PER_NODE = 20;

/**
 * The feasibility line shown after the plan.
 *
 * The gateway may send its own `feasibility` on the plan response; when it does, that wins.
 * This is the fallback so the learner is never left without the honest version of
 * "3 sessions left of ~6 needed". Every number here is derived from the goal contract plus
 * the node count, and the assumption behind it is printed next to it rather than hidden.
 */
export function computeFeasibility(
  goal: Goal,
  nodes: { id: string }[],
  states?: Record<string, NodeState>,
  today = new Date(),
): Feasibility {
  const toTeach = states
    ? nodes.filter((n) => (states[n.id]?.state ?? 'unknown') !== 'known').length
    : nodes.length;

  const minutes = goal.minutes_per_session || 45;
  const sessionsNeeded = Math.max(1, Math.ceil((toTeach * MINUTES_PER_NODE) / minutes));

  let sessionsAvailable: number | null = null;
  if (goal.deadline && goal.sessions_per_week) {
    const end = new Date(`${goal.deadline}T00:00:00Z`);
    const days = (end.getTime() - today.getTime()) / 86_400_000;
    sessionsAvailable = Math.max(0, Math.floor((days / 7) * goal.sessions_per_week));
  }

  let verdict: Feasibility['verdict'] = 'unknown';
  if (sessionsAvailable !== null) {
    if (sessionsAvailable >= sessionsNeeded * 1.25) verdict = 'comfortable';
    else if (sessionsAvailable >= sessionsNeeded) verdict = 'tight';
    else verdict = 'not-feasible';
  }

  return {
    sessions_needed: sessionsNeeded,
    sessions_available: sessionsAvailable,
    verdict,
    assumption: `${toTeach} concept${toTeach === 1 ? '' : 's'} not yet known, ~${MINUTES_PER_NODE} min each (one teach step, one checkpoint, one teach-back), ${minutes} min per session${
      goal.sessions_per_week ? `, ${goal.sessions_per_week} sessions/week` : ''
    }. Estimate, not a measurement.`,
  };
}

export function feasibilitySentence(f: Feasibility, goal?: Goal): string {
  if (f.sessions_available === null) {
    // Say *which* input is missing. "No deadline set" next to a goal that plainly shows a
    // deadline is the kind of small lie that makes a learner stop trusting the screen.
    const why = goal?.deadline
      ? `Deadline ${goal.deadline}, but the goal carries no sessions-per-week, so how many sessions are left is not something this screen can work out.`
      : 'No deadline set, so there is nothing to be behind on.';
    return `About ${f.sessions_needed} session${f.sessions_needed === 1 ? '' : 's'} needed. ${why}`;
  }
  const verdict =
    f.verdict === 'comfortable'
      ? 'that fits'
      : f.verdict === 'tight'
        ? 'that is tight'
        : 'that does not fit - cut scope or add sessions';
  return `${f.sessions_available} session${f.sessions_available === 1 ? '' : 's'} left before the deadline, about ${f.sessions_needed} needed: ${verdict}.`;
}
