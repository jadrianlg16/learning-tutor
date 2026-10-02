'use client';

/**
 * The dependency graph, drawn as SVG from `nodes` + `edges` + per-node state, and made
 * navigable: wheel/pinch zoom around the cursor, drag to pan, fit-to-width, a full-viewport
 * expand, per-chapter collapse, and a search box that jumps to a concept.
 *
 * Why not the mermaid string the gateway returns: the map has to be *coloured by state* and
 * *clickable* (click a node -> receipts), and a rendered mermaid SVG is an opaque blob that
 * gives us neither. Why no d3/cytoscape/react-flow: the whole interaction is one SVG
 * transform plus pointer arithmetic - a few hundred lines against ~500 kB of dependency,
 * and the static export has to keep working.
 *
 * Layout and clustering live in `@/lib/graph` so this file is only interaction.
 *
 * With `exam` (a goal that has an exam blueprint) the chapters are the blueprint's areas in
 * official order, each subárea box is as wide as its exam weight and carries its counts
 * (weight, seen of bank, first tries), and `exam.colourBy` picks what the fill means. The
 * overlay itself is built in `@/lib/mapBlueprint`. Without `exam` nothing here changes.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent as ReactKeyboardEvent,
  type PointerEvent as ReactPointerEvent,
} from 'react';
import {
  layoutGraph,
  STATE_ORDER,
  type PlacedCluster,
  type PlacedNode,
  type GraphLayout,
} from '@/lib/graph';
import {
  describeNode,
  examBox,
  examGroups,
  firstTryText,
  paintFor,
  reactivos,
  seenText,
  sharePct,
  tallyOf,
  type ColourBy,
  type ExamNode,
  type ExamOverlay,
  type Paint,
} from '@/lib/mapBlueprint';
import type { GraphEdge, GraphNode, NodeState, NodeStateName } from '@/lib/types';

export const STATE_COLOUR: Record<NodeStateName, string> = {
  known: 'var(--known)',
  fragile: 'var(--fragile)',
  unknown: 'var(--unknown)',
  misconception: 'var(--miscon)',
};

const MIN_K = 0.25;
const MAX_K = 4;
const FIT_MAX_K = 1.6;

interface View {
  k: number;
  tx: number;
  ty: number;
}

const clampK = (k: number) => Math.min(MAX_K, Math.max(MIN_K, k));

function readCollapsed(key: string | undefined): string[] {
  if (!key || typeof window === 'undefined') return [];
  try {
    const raw = window.localStorage.getItem(`lt-collapsed:${key}`);
    const parsed = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x) => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function writeCollapsed(key: string | undefined, ids: Set<string>) {
  if (!key || typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(`lt-collapsed:${key}`, JSON.stringify(Array.from(ids)));
  } catch {
    /* private mode / storage disabled: collapsing still works, it just does not persist */
  }
}

export interface GraphCanvasProps {
  nodes: GraphNode[];
  edges: GraphEdge[];
  states?: Record<string, NodeState>;
  /** Node ids on the learner path, drawn with the accent edge colour and numbered. */
  pathOrder?: string[];
  selected?: string | null;
  onSelect?: (nodeId: string) => void;
  title?: string;
  hint?: string;
  /** Dim everything not on `pathOrder` (the learner-path pane). */
  pathOnly?: boolean;
  /** localStorage namespace for the collapsed set; omit to keep collapsing in memory. */
  storageKey?: string;
  /** Canvas height when not expanded. */
  height?: number;
  /** A goal with an exam blueprint: area chapters, weighted boxes, counts, colour mode. */
  exam?: { overlay: ExamOverlay; colourBy: ColourBy } | null;
}

export function GraphCanvas({
  nodes: graphNodes,
  edges,
  states,
  pathOrder,
  selected,
  onSelect,
  title,
  hint,
  pathOnly = false,
  storageKey,
  height,
  exam,
}: GraphCanvasProps) {
  const overlay = exam?.overlay ?? null;
  const colourBy: ColourBy = exam?.colourBy ?? 'state';
  /* Subáreas the graph has no concept for still get a (dashed) box: the points are on the
     exam whether or not the plan drew them. */
  const nodes = useMemo(() => {
    if (!overlay?.ghosts.length) return graphNodes;
    const have = new Set(graphNodes.map((n) => n.id));
    return [...graphNodes, ...overlay.ghosts.filter((g) => !have.has(g.id))];
  }, [graphNodes, overlay]);
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, '');
  const hatchId = `lt-hatch-${uid}`;
  const descId = `lt-desc-${uid}`;
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [view, setView] = useState<View>({ k: 1, tx: 0, ty: 0 });
  const [collapsed, setCollapsed] = useState<Set<string>>(() => new Set());
  const [expanded, setExpanded] = useState(false);
  const [query, setQuery] = useState('');
  const [hover, setHover] = useState<{ id: string; x: number; y: number } | null>(null);
  const [focusWanted, setFocusWanted] = useState<string | null>(null);
  /** Which layout the current view was fitted to; a new signature triggers a re-fit. */
  const fittedRef = useRef<string>('');

  /* localStorage is read after mount: the export is prerendered, so reading it during the
     first render would make server and client markup disagree. */
  useEffect(() => {
    const stored = readCollapsed(storageKey);
    if (stored.length) setCollapsed(new Set(stored));
  }, [storageKey]);

  const visible = useMemo(
    () =>
      pathOnly && pathOrder?.length
        ? nodes.filter((n) => pathOrder.includes(n.id))
        : nodes,
    [nodes, pathOnly, pathOrder],
  );

  /* A ghost's state comes from progress (the map's `states` only knows graph nodes). */
  const allStates = useMemo(() => {
    if (!overlay?.ghosts.length) return states;
    const s: Record<string, NodeState> = { ...(states ?? {}) };
    for (const g of overlay.ghosts) {
      const p = overlay.nodes.get(g.id)?.progress;
      if (p && !s[g.id]) {
        s[g.id] = {
          state: p.state,
          independent_passes: 0,
          assisted_passes: 0,
          self_graded_passes: 0,
          fails: 0,
          transfer_passes: 0,
          uncertainty: 'high',
        };
      }
    }
    return s;
  }, [overlay, states]);

  const groups = useMemo(() => (overlay ? examGroups(overlay, visible) : undefined), [overlay, visible]);
  const titleOf = useMemo(() => new Map(nodes.map((n) => [n.id, n.title] as const)), [nodes]);
  const box = useMemo(
    () => (overlay ? (id: string) => examBox(overlay, id) : undefined),
    [overlay],
  );

  const layout: GraphLayout = useMemo(
    () =>
      layoutGraph(visible, edges, {
        states: allStates,
        collapsed,
        pathOrder,
        aspect: size.w && size.h ? size.w / size.h : 1.6,
        viewportW: size.w || undefined,
        groups,
        box,
      }),
    [visible, edges, allStates, collapsed, pathOrder, size.w, size.h, groups, box],
  );

  /** How a box is filled under the active colour mode. */
  const paintOf = useCallback(
    (n: PlacedNode): Paint => {
      if (!overlay || colourBy === 'state') return paintFor('state', n.state, null);
      if (n.summary) {
        const a = overlay.areas.find((x) => `area:${x.code}` === n.clusterId);
        return paintFor(colourBy, n.state, a?.progress ?? null);
      }
      return paintFor(colourBy, n.state, tallyOf(overlay, n.id));
    },
    [overlay, colourBy],
  );

  /* ------------------------------------------------------------ measuring */

  useLayoutEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const apply = () => setSize({ w: el.clientWidth, h: el.clientHeight });
    apply();
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(apply);
    ro.observe(el);
    return () => ro.disconnect();
  }, [expanded]);

  /* ------------------------------------------------------------ fit / zoom */

  const fitWidth = useCallback(() => {
    if (!size.w) return;
    const k = clampK(Math.min((size.w - 24) / layout.width, FIT_MAX_K));
    setView({ k, tx: Math.max(12, (size.w - layout.width * k) / 2), ty: 12 });
  }, [layout.width, size.w]);

  const fitAll = useCallback(() => {
    if (!size.w || !size.h) return;
    const k = clampK(
      Math.min((size.w - 24) / layout.width, (size.h - 24) / layout.height, FIT_MAX_K),
    );
    setView({
      k,
      tx: (size.w - layout.width * k) / 2,
      ty: Math.max(8, (size.h - layout.height * k) / 2),
    });
  }, [layout.width, layout.height, size.w, size.h]);

  /* First paint, an expand, or a re-flow after collapsing a chapter: put the whole width on
     screen, so 40 concepts are legible instead of a squashed strip. Keyed by a signature
     rather than a boolean flag, because two effects racing over one flag is how "it did not
     re-fit after collapse" happens. */
  const layoutSig = `${expanded}|${Math.round(layout.width)}x${Math.round(layout.height)}`;
  useEffect(() => {
    if (!size.w || fittedRef.current === layoutSig) return;
    fittedRef.current = layoutSig;
    fitWidth();
  }, [fitWidth, layoutSig, size.w]);

  const zoomBy = useCallback(
    (factor: number, cx?: number, cy?: number) => {
      setView((v) => {
        const k = clampK(v.k * factor);
        const px = cx ?? size.w / 2;
        const py = cy ?? size.h / 2;
        return { k, tx: px - ((px - v.tx) * k) / v.k, ty: py - ((py - v.ty) * k) / v.k };
      });
    },
    [size.w, size.h],
  );

  const centreOn = useCallback(
    (n: PlacedNode, minK = 0.85) => {
      setView((v) => {
        const k = clampK(Math.max(v.k, minK));
        return {
          k,
          tx: size.w / 2 - (n.x + n.w / 2) * k,
          ty: size.h / 2 - (n.y + n.h / 2) * k,
        };
      });
    },
    [size.w, size.h],
  );

  /* Wheel has to be a native non-passive listener: React routes onWheel through a passive
     root listener, where preventDefault does nothing and the page scrolls instead. */
  useEffect(() => {
    const el = wrapRef.current;
    if (!el) return;
    const onWheel = (ev: WheelEvent) => {
      ev.preventDefault();
      const r = el.getBoundingClientRect();
      const factor = Math.exp(-ev.deltaY * (ev.deltaMode === 1 ? 0.05 : 0.0015));
      zoomBy(factor, ev.clientX - r.left, ev.clientY - r.top);
    };
    el.addEventListener('wheel', onWheel, { passive: false });
    return () => el.removeEventListener('wheel', onWheel);
  }, [zoomBy]);

  /* ------------------------------------------------------------ pan / pinch */

  const pointers = useRef(new Map<number, { x: number; y: number }>());
  const drag = useRef<{ x: number; y: number; tx: number; ty: number } | null>(null);
  const pinch = useRef<{ dist: number; k: number; cx: number; cy: number } | null>(null);
  const moved = useRef(false);
  const captured = useRef(false);
  const [panning, setPanning] = useState(false);

  const localPoint = (ev: { clientX: number; clientY: number }) => {
    const r = wrapRef.current?.getBoundingClientRect();
    return { x: ev.clientX - (r?.left ?? 0), y: ev.clientY - (r?.top ?? 0) };
  };

  const onPointerDown = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if (ev.button !== 0 && ev.pointerType === 'mouse') return;
    const p = localPoint(ev);
    pointers.current.set(ev.pointerId, p);
    moved.current = false;
    if (pointers.current.size === 1) {
      drag.current = { x: p.x, y: p.y, tx: view.tx, ty: view.ty };
      captured.current = false;
      setPanning(true);
      /* Deliberately no setPointerCapture here. Capturing on pointerdown retargets the
         compatibility mouse events to this div, so the `click` a node needs never reaches
         it and nothing on the graph is clickable. Capture is taken below, once the pointer
         has actually moved and the gesture is definitely a pan. */
    } else if (pointers.current.size === 2) {
      const [a, b] = Array.from(pointers.current.values());
      pinch.current = {
        dist: Math.hypot(a.x - b.x, a.y - b.y) || 1,
        k: view.k,
        cx: (a.x + b.x) / 2,
        cy: (a.y + b.y) / 2,
      };
      drag.current = null;
    }
  };

  const onPointerMove = (ev: ReactPointerEvent<HTMLDivElement>) => {
    if (!pointers.current.has(ev.pointerId)) return;
    const p = localPoint(ev);
    pointers.current.set(ev.pointerId, p);

    if (pointers.current.size >= 2 && pinch.current) {
      const [a, b] = Array.from(pointers.current.values());
      const dist = Math.hypot(a.x - b.x, a.y - b.y) || 1;
      const start = pinch.current;
      const k = clampK(start.k * (dist / start.dist));
      moved.current = true;
      setView((v) => ({
        k,
        tx: start.cx - ((start.cx - v.tx) * k) / v.k,
        ty: start.cy - ((start.cy - v.ty) * k) / v.k,
      }));
      return;
    }

    const d = drag.current;
    if (!d) return;
    const dx = p.x - d.x;
    const dy = p.y - d.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) {
      moved.current = true;
      if (!captured.current) {
        captured.current = true;
        (ev.currentTarget as HTMLElement).setPointerCapture?.(ev.pointerId);
      }
    }
    setView((v) => ({ ...v, tx: d.tx + dx, ty: d.ty + dy }));
  };

  const endPointer = (ev: ReactPointerEvent<HTMLDivElement>) => {
    pointers.current.delete(ev.pointerId);
    if (pointers.current.size < 2) pinch.current = null;
    if (pointers.current.size === 0) {
      drag.current = null;
      captured.current = false;
      setPanning(false);
    }
  };

  /* ------------------------------------------------------------ collapse */

  const toggleCluster = useCallback(
    (id: string) => {
      setCollapsed((prev) => {
        const next = new Set(prev);
        if (next.has(id)) next.delete(id);
        else next.add(id);
        writeCollapsed(storageKey, next);
        return next;
      });
    },
    [storageKey],
  );

  const collapseAll = useCallback(() => {
    setCollapsed(() => {
      const next = new Set(layout.clusters.map((c) => c.id));
      writeCollapsed(storageKey, next);
      return next;
    });
  }, [layout.clusters, storageKey]);

  const expandAll = useCallback(() => {
    setCollapsed(() => {
      const next = new Set<string>();
      writeCollapsed(storageKey, next);
      return next;
    });
  }, [storageKey]);

  /* ------------------------------------------------------------ search */

  const q = query.trim().toLowerCase();
  /* A subárea is also found by its ref ("3.2") and its official title. */
  const matches = useCallback(
    (n: GraphNode) => {
      if (n.title.toLowerCase().includes(q)) return true;
      const info = overlay?.nodes.get(n.id);
      return !!info && `${info.ref} ${info.title}`.toLowerCase().includes(q);
    },
    [overlay, q],
  );
  const hits = useMemo(() => {
    if (!q) return new Set<string>();
    const s = new Set<string>();
    for (const n of visible) if (matches(n)) s.add(n.id);
    return s;
  }, [q, visible, matches]);

  const clusterOfNode = useMemo(() => {
    const m = new Map<string, string>();
    for (const c of layout.clusters) for (const id of c.nodeIds) m.set(id, c.id);
    return m;
  }, [layout.clusters]);

  const goToFirstHit = useCallback(() => {
    const first = visible.find((n) => q && matches(n));
    if (!first) return;
    const cid = clusterOfNode.get(first.id);
    // A match inside a collapsed chapter opens that chapter rather than pretending it is
    // not there; the centring then happens on the next layout, via `focusWanted`.
    if (cid && collapsed.has(cid)) {
      setCollapsed((prev) => {
        const next = new Set(prev);
        next.delete(cid);
        writeCollapsed(storageKey, next);
        return next;
      });
    }
    setFocusWanted(first.id);
  }, [clusterOfNode, collapsed, q, storageKey, visible, matches]);

  useEffect(() => {
    if (!focusWanted || !size.w) return;
    const n = layout.byId.get(focusWanted);
    if (!n) return;
    fittedRef.current = layoutSig; // an explicit jump wins over the pending auto-fit
    centreOn(n);
    setFocusWanted(null);
  }, [centreOn, focusWanted, layout, layoutSig, size.w]);

  // A deep link (?node=) or a click on the other pane should not leave the concept off
  // screen; centre it only when it actually is.
  const lastSelected = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (lastSelected.current === selected) return;
    lastSelected.current = selected;
    if (!selected || !size.w) return;
    const n = layout.byId.get(selected);
    if (!n) return;
    const sx = n.x * view.k + view.tx;
    const sy = n.y * view.k + view.ty;
    if (sx < 0 || sy < 0 || sx > size.w - 40 || sy > size.h - 40) centreOn(n);
  }, [selected, layout, size.w, size.h, view.k, view.tx, view.ty, centreOn]);

  /* ------------------------------------------------------------ keyboard */

  const onKeyDown = (ev: ReactKeyboardEvent<HTMLDivElement>) => {
    if (ev.key === '+' || ev.key === '=') {
      ev.preventDefault();
      zoomBy(1.25);
    } else if (ev.key === '-' || ev.key === '_') {
      ev.preventDefault();
      zoomBy(0.8);
    } else if (ev.key === '0') {
      ev.preventDefault();
      fitWidth();
    } else if (ev.key === 'f') {
      ev.preventDefault();
      fitAll();
    } else if (ev.key.startsWith('Arrow')) {
      ev.preventDefault();
      const step = ev.shiftKey ? 120 : 40;
      setView((v) => ({
        ...v,
        tx: v.tx + (ev.key === 'ArrowLeft' ? step : ev.key === 'ArrowRight' ? -step : 0),
        ty: v.ty + (ev.key === 'ArrowUp' ? step : ev.key === 'ArrowDown' ? -step : 0),
      }));
    }
  };

  useEffect(() => {
    if (!expanded) return;
    const onEsc = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') {
        ev.stopPropagation();
        setExpanded(false);
      }
    };
    window.addEventListener('keydown', onEsc);
    return () => window.removeEventListener('keydown', onEsc);
  }, [expanded]);

  /* ------------------------------------------------------------ render */

  const hovered = hover ? layout.byId.get(hover.id) : null;
  const hoveredState = hovered && !hovered.summary ? allStates?.[hovered.id] : undefined;
  const hoveredInfo = hovered && !hovered.summary ? overlay?.nodes.get(hovered.id) : undefined;
  /* Count only chapters that exist now: a stored set can hold ids from an older clustering
     (a goal that gained a blueprint), which must not flip the button to "expand all". */
  const nCollapsed = layout.clusters.filter((c) => c.collapsed).length;
  const allCollapsed = layout.clusters.length > 0 && nCollapsed >= layout.clusters.length;

  const edgeEls = layout.edges.map((e) => {
    // In exam mode an edge between two areas is marked `cross` and drawn fainter, so the
    // area structure reads first and the dependency is still there on a closer look.
    const cross =
      !!overlay && layout.byId.get(e.from)?.clusterId !== layout.byId.get(e.to)?.clusterId;
    return (
      <path
        key={e.key}
        className={`gedge ${e.style}${e.onPath ? ' path' : ''}${cross ? ' cross' : ''}`}
        d={e.d}
        markerEnd={`url(#${e.onPath ? 'lt-arrow-path' : 'lt-arrow'})`}
      >
        <title>{`${e.type} (${e.provenance})${cross ? ' - between two areas' : ''}`}</title>
      </path>
    );
  });

  const canvas = (
    <div
      className={`gcanvas${panning ? ' panning' : ''}`}
      ref={wrapRef}
      tabIndex={0}
      role="application"
      aria-label={`${title ?? 'Concept graph'}: ${layout.nodes.length} boxes, ${layout.clusters.length} chapters. Drag to pan, wheel to zoom.`}
      aria-describedby={overlay ? descId : undefined}
      style={!expanded && height ? { height } : undefined}
      onKeyDown={onKeyDown}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endPointer}
      onPointerCancel={endPointer}
      onPointerLeave={(e) => {
        endPointer(e);
        setHover(null);
      }}
    >
      <svg width="100%" height="100%" role="img" aria-hidden="true">
        <defs>
          <marker
            id="lt-arrow"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="var(--muted)" />
          </marker>
          <marker
            id="lt-arrow-path"
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto-start-reverse"
          >
            <path d="M0,0 L8,4 L0,8 z" fill="var(--accent)" />
          </marker>
          {overlay ? (
            /* "No data" is hatched, never an empty or a red box: no data is not 0. */
            <pattern
              id={hatchId}
              width="7"
              height="7"
              patternUnits="userSpaceOnUse"
              patternTransform="rotate(45)"
            >
              <rect width="7" height="7" fill="var(--surface)" />
              <line x1="0" y1="0" x2="0" y2="7" className="ghatch" />
            </pattern>
          ) : null}
        </defs>

        <g transform={`translate(${view.tx},${view.ty}) scale(${view.k})`}>
          {/* Exam mode draws edges under the chapters: the cross-area ones are long, and
              over an area header or through a box they strike out its text. */}
          {overlay ? edgeEls : null}
          {layout.clusters.map((c) => (
            <g key={c.id} className={`gcluster${c.head ? ' exam' : ''}`}>
              <rect
                className="cbox"
                x={c.x}
                y={c.y}
                width={c.w}
                height={c.h}
                rx="12"
              />
              <g
                className="chead"
                role="button"
                tabIndex={0}
                aria-label={`${c.title} chapter, ${c.total} concepts. ${c.collapsed ? 'Expand' : 'Collapse'}.`}
                aria-expanded={!c.collapsed}
                onClick={(ev) => {
                  ev.stopPropagation();
                  if (!moved.current) toggleCluster(c.id);
                }}
                onKeyDown={(ev) => {
                  if (ev.key === 'Enter' || ev.key === ' ') {
                    ev.preventDefault();
                    ev.stopPropagation();
                    toggleCluster(c.id);
                  }
                }}
              >
                {c.head ? (
                  <ExamHead c={c} />
                ) : (
                  <>
                    <rect x={c.x + 6} y={c.y + 5} width={c.w - 12} height="24" rx="7" />
                    <title>
                      {`${c.title} - ${c.total} concepts: ${STATE_ORDER.filter((s) => c.counts[s] > 0)
                        .map((s) => `${c.counts[s]} ${s}`)
                        .join(', ')}. Click to ${c.collapsed ? 'expand' : 'collapse'}.`}
                    </title>
                    <text className="tri" x={c.x + 18} y={c.y + 17} dominantBaseline="central">
                      {c.collapsed ? '▸' : '▾'}
                    </text>
                    <text className="ctitle" x={c.x + 30} y={c.y + 17} dominantBaseline="central">
                      {c.title.length > 30 ? `${c.title.slice(0, 29)}…` : c.title}
                    </text>
                    {STATE_ORDER.filter((s) => c.counts[s] > 0).map((s, i, arr) => (
                      <text
                        key={s}
                        className="ccount"
                        x={c.x + c.w - 12 - (arr.length - 1 - i) * 34}
                        y={c.y + 17}
                        textAnchor="end"
                        dominantBaseline="central"
                        fill={STATE_COLOUR[s]}
                      >
                        {c.counts[s]} {s.slice(0, 1)}
                      </text>
                    ))}
                  </>
                )}
              </g>
            </g>
          ))}

          {overlay ? null : edgeEls}

          {layout.nodes.map((n) => {
            const isSel = selected === n.id;
            const isHit = hits.has(n.id);
            const dim = !!q && !isHit;
            const paint = paintOf(n);
            const colour = paint.stroke;
            const info = !n.summary ? overlay?.nodes.get(n.id) : undefined;
            const outsideBand =
              overlay && colourBy !== 'state' && !n.summary && !info ? `, ${paint.band}` : '';
            return (
              <g
                key={n.id}
                className={`gnode${isSel ? ' sel' : ''}${isHit ? ' hit' : ''}${dim ? ' dim' : ''}${info ? ' exam' : ''}${info?.ghost ? ' ghost' : ''}`}
                transform={`translate(${n.x},${n.y})`}
                role="button"
                tabIndex={0}
                aria-label={
                  n.summary
                    ? `${n.title} chapter, collapsed, ${n.summary.total} concepts`
                    : info
                      ? describeNode(info, n.state)
                      : `${n.title}: ${n.state}${outsideBand}`
                }
                onClick={(ev) => {
                  ev.stopPropagation();
                  if (moved.current) return;
                  if (n.summary) toggleCluster(n.clusterId);
                  else onSelect?.(n.id);
                }}
                onKeyDown={(ev) => {
                  if (ev.key !== 'Enter' && ev.key !== ' ') return;
                  ev.preventDefault();
                  if (n.summary) toggleCluster(n.clusterId);
                  else onSelect?.(n.id);
                }}
                onPointerEnter={(ev) =>
                  setHover({ id: n.id, ...localPoint(ev) })
                }
                onPointerLeave={() => setHover((h) => (h?.id === n.id ? null : h))}
              >
                {info ? (
                  /* Opaque backing: the tint over it stays readable wherever an edge runs. */
                  <rect className="gback" width={n.w} height={n.h} rx="9" />
                ) : null}
                <rect
                  width={n.w}
                  height={n.h}
                  rx="9"
                  fill={paint.hatch ? `url(#${hatchId})` : paint.fill}
                  fillOpacity={paint.fillOpacity}
                  stroke={isSel ? 'var(--accent)' : colour}
                  strokeDasharray={paint.hatch || info?.ghost ? '5 4' : undefined}
                />
                {info && n.box ? (
                  <ExamBoxBody
                    n={n}
                    info={info}
                    showState={colourBy !== 'state'}
                    hasProgress={overlay?.hasProgress ?? false}
                  />
                ) : null}
                {n.order && !info ? (
                  <>
                    <circle cx="15" cy="14" r="11" fill="var(--accent)" />
                    <text className="gorder" x="15" y="14" textAnchor="middle" dominantBaseline="central">
                      {n.order}
                    </text>
                  </>
                ) : null}
                {info && n.box ? null : n.lines.map((line, i) => (
                  <text
                    key={i}
                    x={n.w / 2}
                    y={
                      (n.summary ? n.h / 2 - 8 : n.h / 2) +
                      (i - (n.lines.length - 1) / 2) * 15
                    }
                    textAnchor="middle"
                    dominantBaseline="central"
                  >
                    {line}
                  </text>
                ))}
                {n.summary ? (
                  <text
                    className="gsum"
                    x={n.w / 2}
                    y={n.h - 14}
                    textAnchor="middle"
                    dominantBaseline="central"
                  >
                    {n.summary.total} concepts &#183; click to expand
                  </text>
                ) : null}
              </g>
            );
          })}
        </g>
      </svg>

      {hovered ? (
        <div
          className="gtip"
          style={{
            left: Math.min(Math.max(8, (hover?.x ?? 0) + 14), Math.max(8, size.w - 268)),
            top: Math.min(Math.max(8, (hover?.y ?? 0) + 14), Math.max(8, size.h - 150)),
          }}
          role="tooltip"
        >
          <strong>{hoveredInfo ? `${hoveredInfo.ref} ${hoveredInfo.title}` : hovered.title}</strong>
          {hoveredInfo ? (
            <p className="small mono muted">
              {reactivos(hoveredInfo.examItems)} &middot; {sharePct(hoveredInfo.share)} of the exam
              &middot; área {hoveredInfo.areaCode}
              {hoveredInfo.progress ? (
                <>
                  <br />
                  {seenText(hoveredInfo.progress)} &middot; {firstTryText(hoveredInfo.progress)}
                  {hoveredInfo.progress.due_now ? ` · ${hoveredInfo.progress.due_now} due` : ''}
                </>
              ) : null}
              {hoveredInfo.ghost ? (
                <>
                  <br />
                  not in the concept graph: no receipts yet
                </>
              ) : null}
            </p>
          ) : null}
          {hovered.summary ? (
            <p className="small muted">
              {hovered.summary.total} concepts collapsed &middot;{' '}
              {STATE_ORDER.filter((s) => hovered.summary!.counts[s] > 0)
                .map((s) => `${hovered.summary!.counts[s]} ${s}`)
                .join(' · ')}
            </p>
          ) : (
            <>
              <p className="small">
                <span className={`pill ${hovered.state}`}>
                  <i className="dot" aria-hidden="true" />
                  {hovered.state}
                </span>{' '}
                <span className="mono muted">
                  {hoveredState?.independent_passes ?? 0} unaided &middot;{' '}
                  {hoveredState?.assisted_passes ?? 0} assisted &middot;{' '}
                  {hoveredState?.fails ?? 0} failed &middot;{' '}
                  {hoveredState?.transfer_passes ?? 0} transfer
                </span>
              </p>
              {hoveredState?.reasons?.length ? (
                <ul className="small muted">
                  {hoveredState.reasons.slice(0, 3).map((r, i) => (
                    <li key={i}>{r}</li>
                  ))}
                </ul>
              ) : null}
              <p className="small muted">click for the full receipts</p>
            </>
          )}
        </div>
      ) : null}
    </div>
  );

  const toolbar = (
    <div className="gtools">
      <div className="gtools-left">
        {title ? <span className="eyebrow">{title}</span> : null}
        {overlay ? (
          <span className="small muted mono">
            {`${layout.clusters.filter((c) => c.head).length} areas · ${visible.filter((n) => overlay.nodes.has(n.id)).length} subáreas${
              visible.some((n) => !overlay.nodes.has(n.id))
                ? ` · ${visible.filter((n) => !overlay.nodes.has(n.id)).length} outside`
                : ''
            }`}
          </span>
        ) : (
          <span className="small muted mono">
            {layout.clusters.length} chapters &middot; {visible.length} concepts
          </span>
        )}
      </div>
      <div className="gtools-right">
        <label className="gsearch">
          <span className="sr-only">Search concepts</span>
          <input
            type="text"
            value={query}
            placeholder="search concepts"
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                goToFirstHit();
              }
            }}
          />
          {q ? (
            <span className="ghits mono small">
              {hits.size} hit{hits.size === 1 ? '' : 's'}
            </span>
          ) : null}
        </label>
        <button type="button" className="btn sm" onClick={goToFirstHit} disabled={!hits.size}>
          go
        </button>
        <span className="gsep" aria-hidden="true" />
        <button type="button" className="btn sm" onClick={() => zoomBy(0.8)} aria-label="Zoom out">
          &minus;
        </button>
        <span className="mono small muted gzoom">{Math.round(view.k * 100)}%</span>
        <button type="button" className="btn sm" onClick={() => zoomBy(1.25)} aria-label="Zoom in">
          +
        </button>
        <button type="button" className="btn sm" onClick={fitAll}>
          fit
        </button>
        <button type="button" className="btn sm" onClick={fitWidth}>
          reset
        </button>
        <span className="gsep" aria-hidden="true" />
        <button
          type="button"
          className="btn sm"
          onClick={allCollapsed ? expandAll : collapseAll}
        >
          {allCollapsed ? 'expand all' : 'collapse all'}
        </button>
        <button type="button" className="btn sm" onClick={() => setExpanded((v) => !v)}>
          {expanded ? 'close (esc)' : 'expand'}
        </button>
      </div>
    </div>
  );

  const body = (
    <figure className="graphfig" style={{ margin: 0 }}>
      {toolbar}
      {canvas}
      <figcaption className="small muted graphhint">
        {hint ??
          (overlay
            ? 'One chapter per exam area, subáreas in the official order; a wider box is worth more reactivos. Drag to pan, wheel or pinch to zoom, +/-/0 on the keyboard. Click an area header to collapse it.'
            : 'Drag to pan, wheel or pinch to zoom, +/-/0 on the keyboard. Click a chapter header to collapse it.')}
      </figcaption>
      {overlay ? (
        <div className="sr-only" id={descId}>
          {layout.clusters.map((c) => (
            <p key={c.id}>
              {c.title}
              {c.head?.meta ? ` (${c.head.meta})` : ''}:{' '}
              {c.nodeIds
                .map((id) => {
                  const info = overlay.nodes.get(id);
                  const st = allStates?.[id]?.state ?? 'unknown';
                  return info ? describeNode(info, st) : `${titleOf.get(id) ?? id}, ${st}`;
                })
                .join('; ')}
              .
            </p>
          ))}
        </div>
      ) : null}
    </figure>
  );

  if (!expanded) return body;

  return (
    <>
      <figure className="graphfig placeholder" style={{ margin: 0 }}>
        <p className="small muted">
          Open full screen. <button type="button" className="btn sm" onClick={() => setExpanded(false)}>bring it back</button>
        </p>
      </figure>
      <div className="graphfs" role="dialog" aria-modal="true" aria-label={title ?? 'Concept graph'}>
        {body}
      </div>
    </>
  );
}

/**
 * An exam area's header: "▾ Área 3 · 49 reactivos (34%)", the area title under it, the
 * area's own counts, and a thin bar for its share of the exam. Geometry matches
 * `headerFor()` in lib/graph.ts.
 */
function ExamHead({ c }: { c: PlacedCluster }) {
  const head = c.head!;
  const L = c.headLines.length;
  const M = head.meta ? 16 : 0;
  const trackW = Math.max(0, c.w - 36);
  const barY = c.y + 30 + 16 * L + M + 6;
  return (
    <>
      <rect x={c.x + 6} y={c.y + 5} width={c.w - 12} height={Math.max(24, c.headH - 10)} rx="7" />
      <title>
        {`${c.title}${head.meta ? ` - ${head.meta}` : ''}. Click to ${c.collapsed ? 'expand' : 'collapse'}.`}
      </title>
      <text className="tri" x={c.x + 18} y={c.y + 17} dominantBaseline="central">
        {c.collapsed ? '▸' : '▾'}
      </text>
      <text className="ctitle" x={c.x + 30} y={c.y + 17} dominantBaseline="central">
        {head.label}
      </text>
      {c.headLines.map((line, i) => (
        <text key={i} className="csub" x={c.x + 18} y={c.y + 38 + 16 * i} dominantBaseline="central">
          {line}
        </text>
      ))}
      {head.meta ? (
        <text className="cmeta" x={c.x + 18} y={c.y + 38 + 16 * L} dominantBaseline="central">
          {head.meta}
        </text>
      ) : null}
      {head.share !== undefined ? (
        <>
          <rect className="cshare-track" x={c.x + 18} y={barY} width={trackW} height="4" rx="2" />
          <rect
            className="cshare"
            x={c.x + 18}
            y={barY}
            width={Math.max(2, trackW * Math.min(1, head.share))}
            height="4"
            rx="2"
          />
        </>
      ) : null}
    </>
  );
}

/** Approximate width of a 10.5px mono label, for sizing the weight pill. */
const MONO_CHAR_PX = 6.4;

/**
 * Inside a subárea box: ref and weight on top, the official title, a thin coverage bar
 * (seen of bank) and the counts under it. Counts only - no percentage on a concept.
 */
function ExamBoxBody({
  n,
  info,
  showState,
  hasProgress,
}: {
  n: PlacedNode;
  info: ExamNode;
  /** Colour modes other than State hide the state in the fill, so it gets a dot. */
  showState: boolean;
  hasProgress: boolean;
}) {
  const p = info.progress;
  const topH = n.box?.topH ?? 26;
  const pad = 10;
  let x = pad;
  const order = n.order ? (
    <>
      <circle cx={x + 10} cy="14" r="10" fill="var(--accent)" />
      <text className="gorder" x={x + 10} y="14" textAnchor="middle" dominantBaseline="central">
        {n.order}
      </text>
    </>
  ) : null;
  if (n.order) x += 25;
  const dotX = x + 4;
  if (showState) x += 13;
  const weight = reactivos(info.examItems);
  const pw = weight.length * MONO_CHAR_PX + 14;
  const trackW = Math.max(0, n.w - pad * 2);
  const barY = topH + n.lines.length * 15 + 6;
  const seenFrac = p && p.bank > 0 ? Math.min(1, p.seen / p.bank) : 0;
  return (
    <>
      {order}
      {showState ? (
        <circle cx={dotX} cy="14" r="4.5" fill={STATE_COLOUR[n.state]}>
          <title>{n.state}</title>
        </circle>
      ) : null}
      <text className="gref" x={x} y="14" dominantBaseline="central">
        {info.ref}
      </text>
      <rect className="gwpill" x={n.w - 7 - pw} y="5" width={pw} height="18" rx="9" />
      <text
        className="gweight"
        x={n.w - 7 - pw / 2}
        y="14"
        textAnchor="middle"
        dominantBaseline="central"
      >
        {weight}
      </text>
      {n.lines.map((line, i) => (
        <text key={i} className="gtitle" x={pad} y={topH + 7.5 + i * 15} dominantBaseline="central">
          {line}
        </text>
      ))}
      <rect className="gbar-track" x={pad} y={barY} width={trackW} height="4" rx="2" />
      {seenFrac > 0 ? (
        <rect className="gbar" x={pad} y={barY} width={Math.max(3, trackW * seenFrac)} height="4" rx="2" />
      ) : null}
      <text className="gcap" x={pad} y={barY + 15} dominantBaseline="central">
        {hasProgress ? seenText(p) : 'no numbers'}
      </text>
      {hasProgress ? (
        <text
          className={`gcap${p && p.first_attempts ? ' strong' : ''}`}
          x={n.w - pad}
          y={barY + 15}
          textAnchor="end"
          dominantBaseline="central"
        >
          {firstTryText(p)}
        </text>
      ) : null}
    </>
  );
}

export function StateLegend() {
  return (
    <div className="legend">
      {(['known', 'fragile', 'unknown', 'misconception'] as NodeStateName[]).map((s) => (
        <span className="k" key={s}>
          <i style={{ background: STATE_COLOUR[s] }} />
          {s}
        </span>
      ))}
    </div>
  );
}

/** What the three line styles mean; shown next to the state legend on the map. */
export function EdgeLegend() {
  return (
    <div className="legend">
      <span className="k">
        <svg width="26" height="8" aria-hidden="true">
          <line x1="0" y1="4" x2="26" y2="4" className="lg strict" />
        </svg>
        strict prerequisite
      </span>
      <span className="k">
        <svg width="26" height="8" aria-hidden="true">
          <line x1="0" y1="4" x2="26" y2="4" className="lg recommended" />
        </svg>
        recommended / sequence
      </span>
      <span className="k">
        <svg width="26" height="8" aria-hidden="true">
          <line x1="0" y1="4" x2="26" y2="4" className="lg soft" />
        </svg>
        transfer / supports
      </span>
    </div>
  );
}
