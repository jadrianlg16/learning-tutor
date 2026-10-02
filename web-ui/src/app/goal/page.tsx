'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { useHashScroll } from '@/lib/useHashScroll';
import type { Feasibility, GoalDetailResponse, MapResponse, Phase, Source } from '@/lib/types';
import { ErrorNote, Loading, PhaseRail } from '@/components/Bits';
import { GoalEditForm, describeChanges } from '@/components/GoalEditForm';
import { GroundingPanel } from '@/components/GroundingPanel';
import { Markdown } from '@/components/Markdown';
import { Collapsible } from '@/components/Panels';
import { PlanPanel } from '@/components/PlanPanel';
import { ProbePanel } from '@/components/ProbePanel';
import { TeachPanel } from '@/components/TeachPanel';

/** The generated `learner.md` - evidence, never decimals. Collapsed until asked for. */
function LearnerModelPanel({ md, goalId }: { md: string; goalId: string }) {
  if (!md) return null;
  return (
    <Collapsible
      title="learner.md · what the tutor loads at session start"
      storageKey={`learner-md:${goalId}`}
      defaultOpen={false}
      subtitle="The compact projection of your learner model. Evidence and dates, never mastery percentages."
    >
      <Markdown>{md}</Markdown>
    </Collapsible>
  );
}

/** Read-only review of a finished grounding phase: what is in the corpus, nothing else. */
function SourcesReview({ goalId }: { goalId: string }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    api
      .listSources(goalId)
      .then((d) => setSources(d.sources))
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);
  return (
    <div className="card">
      <span className="eyebrow">grounding &middot; review, read-only</span>
      <ErrorNote message={error} />
      {!sources && !error ? <Loading what="the sources" /> : null}
      {sources?.length ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>source</th>
                <th>role</th>
                <th>added</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.source_id}>
                  <td>{s.filename || s.url || s.source_id}</td>
                  <td className="mono">{s.role}</td>
                  <td className="mono">{(s.ingested_at ?? '').slice(0, 10) || 'unrecorded'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : sources ? (
        <p className="small muted">
          No sources were uploaded for this goal. The tutor taught from what the model already
          knows, and the plan says so where it could not cite anything.
        </p>
      ) : null}
    </div>
  );
}

/** Read-only review of a finished probe: where it left the map. */
function ProbeReview({ goalId }: { goalId: string }) {
  const [map, setMap] = useState<MapResponse | null>(null);
  useEffect(() => {
    api
      .map(goalId)
      .then(setMap)
      .catch(() => setMap(null));
  }, [goalId]);
  const counts = { known: 0, fragile: 0, unknown: 0, misconception: 0 };
  for (const s of Object.values(map?.states ?? {})) counts[s.state] += 1;
  return (
    <div className="card">
      <span className="eyebrow">probe &middot; review, read-only</span>
      <h2>What the probe found</h2>
      <p className="small muted">
        The probe writes into the learner model, so there is nothing to re-run here: what it
        found is the colour of every concept on your map.
      </p>
      <div className="row small mono muted">
        <span className="pill known">{counts.known} known</span>
        <span className="pill fragile">{counts.fragile} fragile</span>
        <span className="pill misconception">{counts.misconception} misconception</span>
        <span className="pill unknown">{counts.unknown} unknown</span>
      </div>
      <div className="row">
        <Link className="btn sm" href={`/goal/map/?g=${encodeURIComponent(goalId)}`}>
          Open the map
        </Link>
      </div>
    </div>
  );
}

function GoalScreen() {
  const params = useSearchParams();
  const goalId = params.get('g');

  const [detail, setDetail] = useState<GoalDetailResponse | null>(null);
  const [phase, setPhase] = useState<Phase | null>(null);
  /** The phase on screen. Equals `phase` unless the learner stepped back to review one. */
  const [viewing, setViewing] = useState<Phase | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ended, setEnded] = useState<{ logPath: string; summary: string } | null>(null);
  const [editing, setEditing] = useState(false);
  /** The last saved edit: what changed, and PATCH's recomputed feasibility for the plan. */
  const [edited, setEdited] = useState<{ feasibility: Feasibility | null; summary: string } | null>(
    null,
  );

  const load = useCallback(() => {
    if (!goalId) return;
    api
      .getGoal(goalId)
      .then((d) => {
        setDetail(d);
        setPhase((p) => p ?? d.phase);
        setSessionId((s) => s ?? d.session?.session_id ?? null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  useEffect(load, [load]);
  useHashScroll(!!detail);

  const goPhase = useCallback((p: Phase) => setPhase(p), []);

  if (!goalId) {
    return (
      <main className="main" id="main">
        <p className="note warn">
          No goal selected. <Link href="/">Pick one from the goals list.</Link>
        </p>
      </main>
    );
  }

  if (error) {
    return (
      <main className="main" id="main">
        <ErrorNote message={error} />
        <Link className="btn" href="/">
          Back to goals
        </Link>
      </main>
    );
  }

  if (!detail || !phase) {
    return (
      <main className="main" id="main">
        <Loading what="the goal" />
      </main>
    );
  }

  const { goal } = detail;
  const shown = viewing ?? phase;
  const reviewing = shown !== phase;

  const live = ended ? (
    <div className="card">
      <span className="eyebrow">session end</span>
      <h2>Session closed</h2>
      <p>{ended.summary || 'Summary written to the session log.'}</p>
      <p className="small muted">
        Log written to <code>{ended.logPath}</code> - the Obsidian vault when{' '}
        <code>LT_VAULT_DIR</code> is set, otherwise <code>data/vault/</code>.
      </p>
      <div className="row">
        <Link className="btn primary" href={`/goal/map/?g=${encodeURIComponent(goalId)}`}>
          See what moved on the map
        </Link>
        <button
          type="button"
          className="btn"
          onClick={() => {
            setEnded(null);
            setSessionId(null);
            goPhase('probe');
          }}
        >
          Start another session
        </button>
      </div>
    </div>
  ) : phase === 'grounding' ? (
    <GroundingPanel goalId={goalId} onPlan={() => goPhase('plan')} />
  ) : phase === 'plan' ? (
    <PlanPanel goalId={goalId} goal={goal} onApproved={() => goPhase('probe')} edited={edited} />
  ) : phase === 'probe' ? (
    <ProbePanel
      goalId={goalId}
      onSession={setSessionId}
      onDone={(s) => {
        setSessionId(s);
        goPhase('teach');
      }}
    />
  ) : phase === 'teach' && sessionId ? (
    <TeachPanel
      goalId={goalId}
      sessionId={sessionId}
      onEnded={(logPath, summary) => {
        setEnded({ logPath, summary });
        goPhase('done');
        load();
      }}
    />
  ) : phase === 'teach' ? (
    <div className="banner">
      <span>No open session. Start one to keep teaching.</span>
      <button type="button" className="btn primary" onClick={() => goPhase('probe')}>
        Start a session
      </button>
    </div>
  ) : (
    <div className="banner">
      <span>This goal has no open session.</span>
      <button type="button" className="btn primary" onClick={() => goPhase('probe')}>
        Start a session
      </button>
    </div>
  );

  const review =
    shown === 'grounding' ? (
      <SourcesReview goalId={goalId} />
    ) : shown === 'plan' ? (
      <PlanPanel
        goalId={goalId}
        goal={goal}
        onApproved={() => goPhase('probe')}
        readOnly
        edited={edited}
      />
    ) : shown === 'probe' ? (
      <ProbeReview goalId={goalId} />
    ) : (
      <div className="card">
        <span className="eyebrow">{shown} &middot; review</span>
        <p className="small muted">
          A teaching session is a live thing, not a document: what it produced is on the map and
          in the session log, and there is nothing here to replay.
        </p>
        <Link className="btn sm" href={`/goal/map/?g=${encodeURIComponent(goalId)}`}>
          Open the map
        </Link>
      </div>
    );

  return (
    <main className="main" id="main">
      <div className="goalhead">
        <h1>{goal.title}</h1>
        <div className="row small muted mono">
          {goal.deadline ? <span className="pill">deadline {goal.deadline}</span> : null}
          <span className="pill">{goal.minutes_per_session} min/session</span>
          {goal.sessions_per_week ? (
            <span className="pill">{goal.sessions_per_week} sessions/week</span>
          ) : null}
          {goal.assessment ? <span className="pill">{goal.assessment}</span> : null}
          {goal.transfer_required ? <span className="pill accent">transfer required</span> : null}
          <span className="pill accent">{phase}</span>
        </div>
        <div className="row goalhead-actions">
          <button
            type="button"
            className="btn sm"
            aria-expanded={editing}
            onClick={() => setEditing((v) => !v)}
          >
            {editing ? 'close edit' : 'edit dates'}
          </button>
          <Link className="btn sm" href={`/goal/study/?g=${encodeURIComponent(goalId)}`}>
            Study tools
          </Link>
        </div>
        {editing ? (
          <GoalEditForm
            goal={goal}
            onCancel={() => setEditing(false)}
            onSaved={(res) => {
              setDetail((d) => (d ? { ...d, goal: res.goal } : d));
              // A new object per save: PlanPanel reacts to the identity, not the contents.
              setEdited({ feasibility: res.feasibility, summary: describeChanges(res.changed) });
              setEditing(false);
              load();
            }}
          />
        ) : edited?.summary ? (
          <p className="small muted goaledit-saved" role="status">
            Saved: {edited.summary}
          </p>
        ) : null}
      </div>

      <div className="stack gap-sm">
        <span className="eyebrow">
          goal {goal.goal_id}
          {goal.domain ? ` · ${goal.domain}` : ''} &middot; depth {goal.depth}
        </span>
        <p className="muted small" style={{ maxWidth: '66ch' }}>
          {goal.purpose}
        </p>
      </div>

      <PhaseRail phase={phase} viewing={shown} onReview={(p) => setViewing(p === phase ? null : p)} />

      <div className="row">
        <Link className="btn sm" href={`/goal/map/?g=${encodeURIComponent(goalId)}`}>
          Your map
        </Link>
        <Link className="btn sm" href={`/goal/metrics/?g=${encodeURIComponent(goalId)}`}>
          Metrics
        </Link>
      </div>

      <LearnerModelPanel md={detail.summary_md} goalId={goalId} />

      {reviewing ? (
        <>
          <div className="banner">
            <span>
              Reviewing <strong>{shown}</strong>, read-only. Nothing here writes to your learner
              model.
            </span>
            <button type="button" className="btn primary" onClick={() => setViewing(null)}>
              Back to {phase}
            </button>
          </div>
          {review}
        </>
      ) : (
        live
      )}
    </main>
  );
}

export default function GoalPage() {
  return (
    <Suspense
      fallback={
        <main className="main" id="main">
          <Loading what="the goal" />
        </main>
      }
    >
      <GoalScreen />
    </Suspense>
  );
}
