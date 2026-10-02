'use client';

import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { Goal, GoalPatch, GoalPatchResponse } from '@/lib/types';
import { ErrorNote } from './Bits';

const LABEL: Record<string, string> = {
  deadline: 'deadline',
  minutes_per_session: 'min/session',
  sessions_per_week: 'sessions/week',
};

/** "deadline 2026-10-01 → 2026-12-15 · min/session 45 → 60", from PATCH's `changed`. */
export function describeChanges(changed: GoalPatchResponse['changed']): string {
  const show = (v: unknown) => (v === null || v === undefined || v === '' ? 'none' : String(v));
  return Object.entries(changed)
    .map(([k, { from, to }]) => `${LABEL[k] ?? k} ${show(from)} → ${show(to)}`)
    .join(' · ');
}

/**
 * Date and cadence, fixed in place from the sticky goal header. Only what changed is sent
 * (absent = unchanged in the contract) and an emptied deadline goes as `""`, which clears it.
 * The caller re-reads the goal afterwards, so the header pills and the plan's feasibility
 * line are computed from what the gateway stored, not from what this form hoped it stored.
 */
export function GoalEditForm({
  goal,
  onSaved,
  onCancel,
}: {
  goal: Goal;
  onSaved: (res: GoalPatchResponse) => void;
  onCancel: () => void;
}) {
  const [deadline, setDeadline] = useState(goal.deadline ?? '');
  const [minutes, setMinutes] = useState(String(goal.minutes_per_session ?? ''));
  const [perWeek, setPerWeek] = useState(goal.sessions_per_week ? String(goal.sessions_per_week) : '');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const first = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    first.current?.focus();
  }, []);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const patch: GoalPatch = {};
    if (deadline !== (goal.deadline ?? '')) patch.deadline = deadline;
    if (minutes && Number(minutes) !== goal.minutes_per_session) {
      patch.minutes_per_session = Number(minutes);
    }
    // The contract has no "clear" for sessions/week; an empty box means "leave it".
    if (perWeek && Number(perWeek) !== (goal.sessions_per_week ?? null)) {
      patch.sessions_per_week = Number(perWeek);
    }
    if (!Object.keys(patch).length) {
      onCancel();
      return;
    }
    setBusy(true);
    setError(null);
    try {
      onSaved(await api.updateGoal(goal.goal_id, patch));
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <form
      className="goaledit"
      onSubmit={submit}
      aria-label="Edit deadline and cadence"
      onKeyDown={(e) => {
        if (e.key === 'Escape' && !busy) onCancel();
      }}
    >
      <div className="grid-3">
        <div className="stack gap-sm">
          <label className="field">
            <span className="lbl">deadline</span>
            <input
              ref={first}
              type="date"
              value={deadline}
              onChange={(e) => setDeadline(e.target.value)}
            />
          </label>
          <span className="small muted">
            {deadline ? (
              <button type="button" className="linkbtn" onClick={() => setDeadline('')}>
                clear the deadline
              </button>
            ) : goal.deadline ? (
              'Empty: saving clears the deadline.'
            ) : (
              'No deadline set.'
            )}
          </span>
        </div>
        <label className="field">
          <span className="lbl">minutes / session</span>
          <input
            type="number"
            min={10}
            max={180}
            required
            value={minutes}
            onChange={(e) => setMinutes(e.target.value)}
          />
        </label>
        <label className="field">
          <span className="lbl">sessions / week</span>
          <input
            type="number"
            min={1}
            max={14}
            value={perWeek}
            onChange={(e) => setPerWeek(e.target.value)}
          />
        </label>
      </div>

      <ErrorNote message={error} />

      <div className="row">
        <button type="submit" className="btn primary sm" disabled={busy}>
          {busy ? 'saving...' : 'Save'}
        </button>
        <button type="button" className="btn sm" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <span className="small muted">
          Moves the feasibility estimate. Nothing here touches your evidence.
        </span>
      </div>
    </form>
  );
}
