'use client';

import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { computeFeasibility, feasibilitySentence } from '@/lib/feasibility';
import { buildOverlay, reactivos } from '@/lib/mapBlueprint';
import { useHashScroll } from '@/lib/useHashScroll';
import type {
  Feasibility,
  Goal,
  GraphEdge,
  GraphNode,
  MapResponse,
  PlanResponse,
} from '@/lib/types';
import { formatCitation } from '@/lib/types';
import { ErrorNote, Loading } from './Bits';
import { GraphCanvas, StateLegend } from './GraphCanvas';
import { useExamData, WeightLegend } from './MapExam';
import { Collapsible, TruncatedList } from './Panels';

/**
 * Phase 2. The graph's stated purpose is to show the learner what is coming; its real
 * purpose is to stop the model winging it - so the verification list is shown in full,
 * abstentions included, and approval is an explicit one-time act with an edit-ops box.
 *
 * `POST /plan` runs the model and re-imports the graph, so it is **not** fired on mount
 * when a graph already exists: the stored graph is read from `GET /map` and drawn, and
 * rebuilding is a button you press on purpose. Only a goal with no graph yet builds
 * automatically, because there is nothing else to show.
 *
 * With an exam blueprint the dependency graph is grouped and ordered by the exam's areas
 * (official order) and each subárea box is as wide as its exam weight, the same drawing as
 * the map; colours stay the four states here. Without one, nothing changes.
 */
export function PlanPanel({
  goalId,
  goal,
  onApproved,
  readOnly = false,
  edited = null,
}: {
  goalId: string;
  goal: Goal;
  onApproved: () => void;
  /** Reviewing a finished phase: draw everything, write nothing. */
  readOnly?: boolean;
  /** The last goal edit on this screen (a new object per edit), with PATCH's feasibility. */
  edited?: { feasibility: Feasibility | null } | null;
}) {
  const [plan, setPlan] = useState<PlanResponse | null>(null);
  /** Feasibility recomputed by a goal edit; supersedes the one a plan build brought back. */
  const [fresh, setFresh] = useState<Feasibility | null>(null);
  const [graph, setGraph] = useState<{ nodes: GraphNode[]; edges: GraphEdge[] } | null>(null);
  const [states, setStates] = useState<MapResponse['states'] | undefined>(undefined);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [ops, setOps] = useState('[]');
  const [opsError, setOpsError] = useState<string | null>(null);
  const [showOps, setShowOps] = useState(false);
  const [svg, setSvg] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const examData = useExamData(goalId);
  const overlay = useMemo(
    () =>
      graph && examData.blueprint
        ? buildOverlay(examData.blueprint, examData.progress, graph.nodes)
        : null,
    [graph, examData.blueprint, examData.progress],
  );
  const exam = useMemo(() => (overlay ? { overlay, colourBy: 'state' as const } : null), [overlay]);

  const build = useCallback(() => {
    setBusy(true);
    setError(null);
    api
      .plan(goalId)
      .then(async (p) => {
        setPlan(p);
        setFresh(null);
        setGraph({ nodes: p.nodes, edges: p.edges });
        try {
          const m = await api.map(goalId);
          setStates(m.states);
        } catch {
          /* map is optional before the first probe */
        }
      })
      .catch((e) => setError(errorMessage(e)))
      .finally(() => setBusy(false));
  }, [goalId]);

  // Read the stored graph first. Only an empty one triggers a build.
  useEffect(() => {
    let live = true;
    api
      .map(goalId)
      .then((m) => {
        if (!live) return;
        setStates(m.states);
        if (m.curriculum.nodes.length) {
          setGraph({ nodes: m.curriculum.nodes, edges: m.curriculum.edges });
          setLoading(false);
        } else {
          setLoading(false);
          if (!readOnly) build();
        }
      })
      .catch(() => {
        if (!live) return;
        setLoading(false);
        if (!readOnly) build();
      });
    return () => {
      live = false;
    };
  }, [goalId, build, readOnly]);

  useHashScroll(!!graph);

  // A goal edit makes a plan's own feasibility stale. Take the gateway's recomputed one when
  // it sent one; otherwise drop the stale one so the fallback below reads the new goal fields.
  useEffect(() => {
    if (!edited) return;
    setFresh(edited.feasibility);
    setPlan((p) => (p?.feasibility ? { ...p, feasibility: null } : p));
  }, [edited]);

  const approve = async () => {
    let parsed: unknown[] = [];
    try {
      const v = JSON.parse(ops || '[]');
      if (!Array.isArray(v)) throw new Error('edit ops must be a JSON array');
      parsed = v;
    } catch (e) {
      setOpsError(e instanceof Error ? e.message : String(e));
      return;
    }
    setOpsError(null);
    setBusy(true);
    try {
      await api.approvePlan(goalId, parsed);
      onApproved();
    } catch (e) {
      setError(errorMessage(e));
      setBusy(false);
    }
  };

  const renderMermaid = async () => {
    if (!plan) return;
    try {
      const r = await api.render(plan.mermaid);
      setSvg(r.svg);
    } catch (e) {
      setError(errorMessage(e));
    }
  };

  // Wait for the blueprint too, so a blueprint goal's graph does not re-flow after first paint.
  if (loading || (busy && !graph) || (graph && !examData.ready)) {
    return (
      <div className="card">
        <ErrorNote message={error} />
        <Loading what={busy ? 'the plan (the model is drafting it)' : 'the stored graph'} />
      </div>
    );
  }

  if (!graph) {
    return (
      <div className="card">
        <ErrorNote message={error} />
        <p className="small muted">
          No graph for this goal yet. Building one runs the model over your sources and the goal
          contract; it takes a minute or so.
        </p>
        <button className="btn primary" onClick={build} disabled={busy}>
          Build the plan
        </button>
      </div>
    );
  }

  const feasibility =
    fresh ?? plan?.feasibility ?? computeFeasibility(goal, graph.nodes, states);
  const abstained = plan?.verification.filter((v) => v.status === 'abstain') ?? [];
  const title = (id: string) => {
    const info = overlay?.nodes.get(id);
    if (info) return `${info.ref} ${info.title}`;
    return graph.nodes.find((n) => n.id === id)?.title ?? id;
  };
  const selectedInfo = selected ? overlay?.nodes.get(selected) : undefined;

  return (
    <div className="stack gap-lg">
      <Collapsible
        id="plan-graph"
        title={`plan · ${plan ? `graph ${plan.graph_version} · ` : ''}${graph.nodes.length} concepts, ${graph.edges.length} dependencies`}
        storageKey={`plan-graph:${goalId}`}
        subtitle={`${graph.nodes.length} concepts, ${graph.edges.length} dependencies.`}
        aside={
          overlay ? (
            <>
              <StateLegend />
              <WeightLegend />
            </>
          ) : (
            <StateLegend />
          )
        }
      >
        <p
          className={`note ${
            feasibility.verdict === 'not-feasible'
              ? 'bad'
              : feasibility.verdict === 'tight'
                ? 'warn'
                : feasibility.verdict === 'comfortable'
                  ? 'good'
                  : '' /* unknown: neutral. A green box over "we cannot work it out" is a lie. */
          }`}
        >
          <strong>Feasibility:</strong> {feasibilitySentence(feasibility, goal)}
          <br />
          <span className="small muted">{feasibility.assumption}</span>
        </p>

        {overlay && examData.blueprint ? (
          <p className="small muted">
            Grouped by the exam blueprint: {examData.blueprint.areas.length} areas,{' '}
            {reactivos(examData.blueprint.total_items)}, subáreas in the official order. A wider
            box is worth more reactivos.
            {overlay.ghosts.length
              ? ` ${overlay.ghosts.length} subárea${overlay.ghosts.length === 1 ? ' has' : 's have'} no concept in this graph (dashed).`
              : ''}
          </p>
        ) : null}

        <GraphCanvas
          nodes={graph.nodes}
          edges={graph.edges}
          states={states}
          selected={selected}
          onSelect={setSelected}
          title="dependency graph"
          storageKey={`plan:${goalId}`}
          exam={exam}
        />
        {selected ? (
          <p className="small muted">
            Selected: <strong>{title(selected)}</strong>
            {selectedInfo
              ? ` · ${reactivos(selectedInfo.examItems)} on the exam`
              : ''}
            . Full receipts live on the map page.
          </p>
        ) : null}

        {plan ? (
          <details>
            <summary className="eyebrow" style={{ cursor: 'pointer' }}>
              mermaid source (as the gateway emitted it)
            </summary>
            <div className="stack gap-sm" style={{ marginTop: 10 }}>
              <pre>{plan.mermaid}</pre>
              <div className="row">
                <button type="button" className="btn sm" onClick={renderMermaid}>
                  Render via gateway /render
                </button>
                {svg ? <span className="pill known">rendered</span> : null}
              </div>
              {svg ? <div className="graphwrap" dangerouslySetInnerHTML={{ __html: svg }} /> : null}
            </div>
          </details>
        ) : null}
      </Collapsible>

      <Collapsible
        title="verification · cite or abstain"
        storageKey={`plan-verify:${goalId}`}
        defaultOpen={!!plan}
        subtitle={
          plan
            ? `${plan.verification.length - abstained.length} cited, ${abstained.length} abstained.`
            : 'not run in this view.'
        }
      >
        {!plan ? (
          <p className="small muted">
            The cite-or-abstain table comes back with a plan build. This view is showing the
            graph that is already stored, so nothing was re-run against your sources.
          </p>
        ) : (
          <>
            <p className="muted small">
              Abstentions are shown, not hidden. A node with no citation is a node the tutor
              could not ground in your sources - it will still teach it, and it will say so.
            </p>
            {abstained.length ? (
              <div className="note warn small">
                <strong>Abstained:</strong>{' '}
                <TruncatedList
                  items={abstained.map((v) => title(v.node_id))}
                  limit={6}
                  label="abstentions"
                />
              </div>
            ) : null}
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>concept</th>
                    <th>status</th>
                    <th>citations</th>
                  </tr>
                </thead>
                <tbody>
                  {plan.verification.map((v) => (
                    <tr key={v.node_id}>
                      <td>{title(v.node_id)}</td>
                      <td>
                        <span className={`pill ${v.status === 'cited' ? 'known' : 'fragile'}`}>
                          {v.status}
                        </span>
                      </td>
                      <td className="small muted">
                        {v.citations.length
                          ? v.citations.map(formatCitation).join('; ')
                          : 'no source supports this claim'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </Collapsible>

      {readOnly ? (
        <p className="small muted">
          Read-only review. The plan was approved already; rebuilding or re-approving happens in
          the live phase.
        </p>
      ) : (
        <div className="card">
          <div className="stack gap-sm">
            <span className="eyebrow">review &amp; approve &middot; your one manual review</span>
            <h2>Does this graph match the course you are actually taking?</h2>
            <p className="muted small">
              This is the only place you edit the map by hand. After this, the map moves on
              evidence.
            </p>
          </div>

          <div className="row">
            <button type="button" className="btn sm" onClick={() => setShowOps((v) => !v)}>
              {showOps ? 'hide edit ops' : 'add edit ops'}
            </button>
            <button type="button" className="btn sm" onClick={build} disabled={busy}>
              {busy ? 'the model is drafting...' : plan ? 'rebuild with the model' : 'rebuild the plan with the model'}
            </button>
            <span className="small muted">
              Rebuilding re-runs the model over your sources and re-imports the graph.
            </span>
          </div>

          {showOps ? (
            <label className="field">
              <span className="lbl">edit ops (JSON array: split / merge / add / remove)</span>
              <textarea
                value={ops}
                onChange={(e) => setOps(e.target.value)}
                spellCheck={false}
                style={{ fontFamily: 'var(--mono)', fontSize: 13 }}
                placeholder='[{"op":"remove","node":"Pullback","why":"not on my midterm"}]'
              />
              <span className="hint">
                Passed through to <code>POST /plan/approve</code> as <code>ops</code>. Evidence on
                a split or merged node is migrated by the learner service, not by this form.
              </span>
            </label>
          ) : null}

          <ErrorNote message={opsError} />
          <ErrorNote message={error} />

          <div className="row">
            <button type="button" className="btn primary" disabled={busy} onClick={approve}>
              {busy ? 'approving...' : 'Approve plan and start the probe'}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
