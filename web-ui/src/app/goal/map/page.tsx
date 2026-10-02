'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { buildOverlay } from '@/lib/mapBlueprint';
import { useHashScroll } from '@/lib/useHashScroll';
import type { MapResponse, NodeStateName } from '@/lib/types';
import { ErrorNote, Loading } from '@/components/Bits';
import { EdgeLegend, GraphCanvas, StateLegend } from '@/components/GraphCanvas';
import {
  ColourByToggle,
  ColourLegend,
  ExamNumbers,
  ExamSummary,
  WeightLegend,
  useColourBy,
  useExamData,
} from '@/components/MapExam';
import { Collapsible, Drawer } from '@/components/Panels';
import { ReceiptsPanel } from '@/components/ReceiptsPanel';

/**
 * Two graphs: the **curriculum map** (everything the subject contains) and the **learner
 * path** (the ordered route through it). Both coloured by state, both pan/zoom/collapse,
 * and clicking a node opens its receipts in a drawer - beside the graph, not below it, so
 * the map never scrolls out from under you.
 *
 * A goal with an exam blueprint gets the exam's own structure instead of graph-shape
 * chapters: one chapter per area in official order, subárea boxes as wide as their weight,
 * counts on every box, a colour-by toggle (state / first-try accuracy / coverage), and the
 * subárea's numbers at the top of the drawer. A goal without one renders exactly as before.
 */
function MapScreen() {
  const params = useSearchParams();
  const goalId = params.get('g');

  const [data, setData] = useState<MapResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  // ?node=<id> deep-links a concept's receipts, so a link to "why is this fragile?" can be
  // shared, bookmarked, or dropped into a session log.
  const [selected, setSelected] = useState<string | null>(params.get('node'));
  const examData = useExamData(goalId);
  const [colourBy, setColourBy] = useColourBy(goalId);

  useEffect(() => {
    if (!goalId) return;
    api
      .map(goalId)
      .then(setData)
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  // Draw once both have arrived, so the chapters do not re-flow from shape to areas.
  const ready = !!data && examData.ready;
  useHashScroll(ready);

  const overlay = useMemo(
    () =>
      data && examData.blueprint
        ? buildOverlay(examData.blueprint, examData.progress, data.curriculum.nodes)
        : null,
    [data, examData.blueprint, examData.progress],
  );
  const exam = useMemo(() => (overlay ? { overlay, colourBy } : null), [overlay, colourBy]);
  const selectedInfo = selected ? (overlay?.nodes.get(selected) ?? null) : null;

  const select = useCallback((nodeId: string | null) => {
    setSelected(nodeId);
    if (typeof window === 'undefined') return;
    const url = new URL(window.location.href);
    if (nodeId) url.searchParams.set('node', nodeId);
    else url.searchParams.delete('node');
    window.history.replaceState(null, '', url.toString());
  }, []);

  const counts = useMemo(() => {
    const c: Record<NodeStateName, number> = {
      known: 0,
      fragile: 0,
      unknown: 0,
      misconception: 0,
    };
    for (const s of Object.values(data?.states ?? {})) c[s.state] += 1;
    return c;
  }, [data]);

  const selectedTitle = selected
    ? (data?.curriculum.nodes.find((n) => n.id === selected)?.title ??
      (selectedInfo ? `${selectedInfo.ref} ${selectedInfo.title}` : 'receipts'))
    : 'receipts';

  if (!goalId) {
    return (
      <main className="main" id="main">
        <p className="note warn">
          No goal selected. <Link href="/">Pick one.</Link>
        </p>
      </main>
    );
  }

  return (
    <main className="main wide" id="main">
      <div className="stack gap-sm">
        <span className="eyebrow">your map</span>
        <h1>What you know, and the route through what you do not</h1>
        <p className="muted small" style={{ maxWidth: '68ch' }}>
          Calibration is itself a skill worth learning, so this is shown to you rather than kept
          in the model. Click any concept for the evidence behind its colour - counts and dates,
          never a mastery percentage.
        </p>
      </div>

      <ErrorNote message={error} />
      {!ready && !error ? <Loading what="the map" /> : null}

      {data && ready ? (
        <>
          {overlay && examData.blueprint ? (
            <ExamSummary blueprint={examData.blueprint} ghosts={overlay.ghosts.length} />
          ) : null}
          {examData.blueprintError ? (
            <p className="small muted">
              The exam blueprint could not be read ({examData.blueprintError}), so the map is
              grouped by graph shape.
            </p>
          ) : null}
          {overlay && examData.progressError ? (
            <p className="note warn small">
              Progress numbers could not be read ({examData.progressError}): the boxes show exam
              weights only.
            </p>
          ) : null}

          {overlay ? (
            <div className="maphead exam">
              <div className="maphead-row">
                <ColourByToggle value={colourBy} onChange={setColourBy} />
                <span className="small mono muted">
                  {counts.known} known &middot; {counts.fragile} fragile &middot;{' '}
                  {counts.misconception} misconception &middot; {counts.unknown} unknown
                </span>
              </div>
              <div className="maphead-row">
                <ColourLegend mode={colourBy} />
              </div>
              <div className="maphead-row">
                <WeightLegend />
                <EdgeLegend />
              </div>
            </div>
          ) : (
            <div className="maphead">
              <StateLegend />
              <EdgeLegend />
              <span className="small mono muted">
                {counts.known} known &middot; {counts.fragile} fragile &middot;{' '}
                {counts.misconception} misconception &middot; {counts.unknown} unknown
              </span>
            </div>
          )}

          <Collapsible
            title="curriculum map · the whole subject"
            storageKey={`map-curriculum:${goalId}`}
            subtitle={`${data.curriculum.nodes.length} concepts, collapsed.`}
            aside={
              <span className="small muted">
                {overlay
                  ? 'every subárea of the exam, area by area, in the official order'
                  : 'every concept the plan found, chapter by chapter'}
              </span>
            }
          >
            <GraphCanvas
              nodes={data.curriculum.nodes}
              edges={data.curriculum.edges}
              states={data.states}
              selected={selected}
              onSelect={select}
              title="curriculum map"
              storageKey={`curriculum:${goalId}`}
              exam={exam}
            />
          </Collapsible>

          <Collapsible
            title="learner path · your route, in order"
            storageKey={`map-path:${goalId}`}
            subtitle={`${data.path.order.length} concepts on the route, collapsed.`}
            aside={
              <span className="small muted">
                {data.path.order.length} concepts, numbered in teaching order
              </span>
            }
          >
            <GraphCanvas
              nodes={data.curriculum.nodes}
              edges={data.curriculum.edges}
              states={data.states}
              pathOrder={data.path.order}
              pathOnly
              selected={selected}
              onSelect={select}
              title="learner path"
              storageKey={`path:${goalId}`}
              hint="Numbered in teaching order. Known concepts are not on the route - there is nothing to teach there."
              exam={exam}
            />
          </Collapsible>

          <Collapsible
            title="mermaid source"
            storageKey={`map-mermaid:${goalId}`}
            defaultOpen={false}
            subtitle="the same two graphs as the gateway emitted them, for export and POST /api/render."
          >
            <details>
              <summary className="small muted" style={{ cursor: 'pointer' }}>
                curriculum map
              </summary>
              <pre style={{ marginTop: 8 }}>{data.curriculum.mermaid}</pre>
            </details>
            <details>
              <summary className="small muted" style={{ cursor: 'pointer' }}>
                learner path
              </summary>
              <pre style={{ marginTop: 8 }}>{data.path.mermaid}</pre>
            </details>
            <p className="small muted">
              The graphs above are drawn from the node and edge lists so they can be coloured,
              clicked and collapsed. These are the same graphs as the gateway&rsquo;s mermaid.
            </p>
          </Collapsible>

          <Drawer open={!!selected} title={selectedTitle} onClose={() => select(null)}>
            {selectedInfo?.ghost ? (
              // No concept in the graph: numbers only, and no receipts call to 404 on.
              <aside className="receipts flat" id="receipts" aria-live="polite">
                <ExamNumbers info={selectedInfo} hasProgress={!!overlay?.hasProgress} />
              </aside>
            ) : (
              <ReceiptsPanel
                goalId={goalId}
                nodeId={selected}
                flat
                exam={
                  selectedInfo ? { info: selectedInfo, hasProgress: !!overlay?.hasProgress } : null
                }
              />
            )}
          </Drawer>
        </>
      ) : null}
    </main>
  );
}

export default function MapPage() {
  return (
    <Suspense
      fallback={
        <main className="main wide" id="main">
          <Loading what="the map" />
        </main>
      }
    >
      <MapScreen />
    </Suspense>
  );
}
