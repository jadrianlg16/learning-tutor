'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { Depth, Domain, GoalContract, GoalListRow, SourcePriority } from '@/lib/types';
import { ErrorNote, Loading } from '@/components/Bits';

const DEPTHS: Depth[] = ['recognize', 'explain', 'apply', 'analyze'];
const DOMAINS: Domain[] = ['math-cs', 'empirical', 'procedural'];
const CAPABILITIES = ['explain', 'trace', 'implement', 'analyse', 'select'];

const DOMAIN_HINT: Record<Domain, string> = {
  'math-cs': 'Executable or provable. Checks can be graded by a solver.',
  empirical: 'Needs citations. Claims are supported, not derived.',
  procedural: 'Needs practice reps. Correctness is in the doing.',
};

function StateBar({ g }: { g: GoalListRow }) {
  const total = Math.max(1, g.node_count);
  const seg = (n: number, colour: string, label: string) =>
    n > 0 ? (
      <i
        key={label}
        style={{ width: `${(n / total) * 100}%`, background: colour }}
        title={`${n} ${label}`}
      />
    ) : null;
  return (
    <>
      <div className="statebar" aria-hidden="true">
        {seg(g.known, 'var(--known)', 'known')}
        {seg(g.fragile, 'var(--fragile)', 'fragile')}
        {seg(g.misconception, 'var(--miscon)', 'misconception')}
        {seg(g.unknown, 'var(--unknown)', 'unknown')}
      </div>
      <span className="small muted mono">
        {g.node_count
          ? `${g.known} known - ${g.fragile} fragile - ${g.misconception} misconception - ${g.unknown} unknown`
          : 'no graph yet'}
      </span>
    </>
  );
}

const BLANK: GoalContract = {
  title: '',
  concept: '',
  depth: 'apply',
  purpose: '',
  deadline: '',
  minutes_per_session: 45,
  sessions_per_week: 3,
  assessment: '',
  source_priority: 'alignment',
  target_capabilities: ['explain', 'apply'],
  transfer_required: true,
  domain: 'math-cs',
};

export default function GoalsPage() {
  const router = useRouter();
  const [goals, setGoals] = useState<GoalListRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<GoalContract>(BLANK);
  const [busy, setBusy] = useState(false);

  const load = useCallback(() => {
    api
      .listGoals()
      .then((d) => setGoals(d.goals))
      .catch((e) => setError(errorMessage(e)));
  }, []);

  useEffect(load, [load]);

  const set = <K extends keyof GoalContract>(k: K, v: GoalContract[K]) =>
    setForm((f) => ({ ...f, [k]: v }));

  const toggleCap = (c: string) =>
    setForm((f) => {
      const cur = f.target_capabilities ?? [];
      return {
        ...f,
        target_capabilities: cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c],
      };
    });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const payload: GoalContract = {
        ...form,
        deadline: form.deadline || null,
        assessment: form.assessment || null,
      };
      const res = await api.createGoal(payload);
      router.push(`/goal/?g=${encodeURIComponent(res.goal.goal_id)}`);
    } catch (err) {
      setError(errorMessage(err));
      setBusy(false);
    }
  };

  return (
    <main className="main" id="main">
      <div className="stack gap-sm">
        <span className="eyebrow">goals</span>
        <h1>What are you trying to be able to do?</h1>
        <p className="muted" style={{ maxWidth: '62ch' }}>
          A goal is a contract, not a topic. Depth, deadline and how you will be assessed decide
          what &ldquo;done&rdquo; means - and the stopping rule that follows from it.
        </p>
      </div>

      <ErrorNote message={error} />

      {goals === null ? <Loading what="goals" /> : null}

      {goals?.length ? (
        <div className="stack">
          {goals.map((g) => (
            // A div, not one big link: the study link below would otherwise be a link inside
            // a link, which is invalid HTML and unreachable by keyboard.
            <div key={g.goal_id} className="goalcard">
              <Link className="goalcard-main" href={`/goal/?g=${encodeURIComponent(g.goal_id)}`}>
                <div className="row between">
                  <h2>{g.title}</h2>
                  <span className="pill accent">{g.phase}</span>
                </div>
                <p className="small muted">
                  {g.depth} &middot; {g.domain}
                  {g.deadline ? ` · deadline ${g.deadline}` : ''} &middot; {g.minutes_per_session}{' '}
                  min &times; {g.sessions_per_week ?? '?'}/week
                </p>
                <StateBar g={g} />
              </Link>
              <div className="row">
                <Link className="btn sm" href={`/goal/study/?g=${encodeURIComponent(g.goal_id)}`}>
                  Study tools
                </Link>
              </div>
            </div>
          ))}
        </div>
      ) : goals ? (
        <p className="muted">No goals yet.</p>
      ) : null}

      {!showForm ? (
        <div>
          <button type="button" className="btn primary" onClick={() => setShowForm(true)}>
            New goal
          </button>
        </div>
      ) : (
        <form className="card" onSubmit={submit}>
          <div className="row between">
            <h2>Goal contract</h2>
            <button type="button" className="btn sm" onClick={() => setShowForm(false)}>
              cancel
            </button>
          </div>

          <label className="field">
            <span className="lbl">title</span>
            <input
              type="text"
              required
              value={form.title}
              onChange={(e) => set('title', e.target.value)}
              placeholder="Differential forms for Maxwell"
            />
          </label>

          <label className="field">
            <span className="lbl">concept</span>
            <input
              type="text"
              required
              value={form.concept}
              onChange={(e) => set('concept', e.target.value)}
              placeholder="Differential forms and the generalized Stokes theorem"
            />
          </label>

          <label className="field">
            <span className="lbl">purpose</span>
            <textarea
              required
              value={form.purpose}
              onChange={(e) => set('purpose', e.target.value)}
              placeholder="Why you want it. This decides what gets cut when time runs short."
              style={{ minHeight: 64 }}
            />
          </label>

          <div className="grid-3">
            <label className="field">
              <span className="lbl">depth</span>
              <select value={form.depth} onChange={(e) => set('depth', e.target.value as Depth)}>
                {DEPTHS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </label>

            <label className="field">
              <span className="lbl">domain</span>
              <select value={form.domain} onChange={(e) => set('domain', e.target.value as Domain)}>
                {DOMAINS.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
              <span className="hint">{DOMAIN_HINT[form.domain]}</span>
            </label>

            <label className="field">
              <span className="lbl">source priority</span>
              <select
                value={form.source_priority ?? 'alignment'}
                onChange={(e) => set('source_priority', e.target.value as SourcePriority)}
              >
                <option value="alignment">alignment (your course wins)</option>
                <option value="authority">authority (the textbook wins)</option>
              </select>
            </label>
          </div>

          <div className="grid-3">
            <label className="field">
              <span className="lbl">deadline</span>
              <input
                type="date"
                value={form.deadline ?? ''}
                onChange={(e) => set('deadline', e.target.value)}
              />
            </label>
            <label className="field">
              <span className="lbl">minutes / session</span>
              <input
                type="number"
                min={10}
                max={180}
                required
                value={form.minutes_per_session}
                onChange={(e) => set('minutes_per_session', Number(e.target.value))}
              />
            </label>
            <label className="field">
              <span className="lbl">sessions / week</span>
              <input
                type="number"
                min={1}
                max={14}
                value={form.sessions_per_week ?? 3}
                onChange={(e) => set('sessions_per_week', Number(e.target.value))}
              />
            </label>
          </div>

          <label className="field">
            <span className="lbl">assessment</span>
            <input
              type="text"
              value={form.assessment ?? ''}
              onChange={(e) => set('assessment', e.target.value)}
              placeholder="Written midterm, problems in the style of the problem sets"
            />
          </label>

          <fieldset
            style={{ border: 0, padding: 0, margin: 0 }}
            className="stack gap-sm"
          >
            <legend className="lbl mono" style={{ padding: 0 }}>
              TARGET CAPABILITIES
            </legend>
            <div className="row">
              {CAPABILITIES.map((c) => (
                <label key={c} className="pill" style={{ cursor: 'pointer' }}>
                  <input
                    type="checkbox"
                    checked={(form.target_capabilities ?? []).includes(c)}
                    onChange={() => toggleCap(c)}
                    style={{ width: 'auto', margin: 0 }}
                  />
                  {c}
                </label>
              ))}
            </div>
          </fieldset>

          <label className="row" style={{ gap: 8 }}>
            <input
              type="checkbox"
              checked={!!form.transfer_required}
              onChange={(e) => set('transfer_required', e.target.checked)}
              style={{ width: 'auto' }}
            />
            <span className="small">
              Require transfer evidence - quiz in a different surface form than the one taught.
            </span>
          </label>

          <div className="row">
            <button type="submit" className="btn primary" disabled={busy}>
              {busy ? 'creating...' : 'Create goal'}
            </button>
            <span className="small muted">
              Feasibility is shown after the plan, once the graph says how many nodes there are.
            </span>
          </div>
        </form>
      )}
    </main>
  );
}
