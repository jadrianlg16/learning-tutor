/**
 * Graph clustering + layout. Pure functions, no React, no DOM - so the interactive canvas
 * in `components/GraphCanvas.tsx` only has to worry about pan, zoom and hit-testing.
 *
 * Why clusters: a real goal graph is not one tree. A 40-concept CS-review goal has ten
 * chapter-sized weakly connected components (algorithms, graphs, automata, networking,
 * SQL, ...). Laying that out as one layered DAG gives a 7000px-wide strip nobody can read.
 * So: split into components, lay each one out on its own, and pack the blocks into rows
 * that wrap - a page of chapters rather than a banner.
 *
 * If the graph really is one giant component (a single course with one root), the
 * component split degenerates to one block, so we fall back to grouping by *root ancestor*
 * instead, which recovers chapter-sized groups for that shape too.
 *
 * Explicit groups (a goal with an exam blueprint): graph shape is the wrong question there.
 * An EGEL-style goal is 14 subáreas joined by cross-area edges - one component, and a
 * root-ancestor split of it cuts areas in half. The exam's own structure is the chapter: the
 * caller (`lib/mapBlueprint.ts`) passes `groups` (areas, in official order) and a `box` size
 * per node (width carries exam weight), and `layoutGraph` places them instead of clustering.
 * Without `groups` nothing below changes.
 */
import type { GraphEdge, GraphNode, NodeState, NodeStateName } from './types';

/** Edges that imply "comes before"; the rest are annotations, not sequence. */
export const STRUCTURAL: ReadonlySet<string> = new Set([
  'strict_prerequisite',
  'recommended_background',
  'course_sequence',
  'co_requisite',
]);

export type EdgeStyle = 'strict' | 'recommended' | 'soft';

/** strict = solid, recommended/sequence = dashed, transfer/supports/misconception = dotted. */
export function edgeStyle(type: string): EdgeStyle {
  if (type === 'strict_prerequisite' || type === 'co_requisite') return 'strict';
  if (type === 'recommended_background' || type === 'course_sequence') return 'recommended';
  return 'soft';
}

export const NODE_W = 186;
export const LINE_H = 15;
export const MAX_LINES = 3;
export const WRAP_CHARS = 25;
const GAP_X = 18;
const GAP_Y = 44;
const PAD = 12;
const HEAD_H = 34;
const BLOCK_GAP = 24;
const MAX_PER_ROW = 4;

export const STATE_ORDER: NodeStateName[] = ['known', 'fragile', 'unknown', 'misconception'];

export function wrapTitle(title: string, max = WRAP_CHARS, maxLines = MAX_LINES): string[] {
  const words = String(title ?? '').split(/\s+/).filter(Boolean);
  const lines: string[] = [];
  let cur = '';
  for (const w of words) {
    if (!cur) cur = w;
    else if (`${cur} ${w}`.length <= max) cur = `${cur} ${w}`;
    else {
      lines.push(cur);
      cur = w;
    }
  }
  if (cur) lines.push(cur);
  if (!lines.length) return [''];
  if (lines.length <= maxLines) return lines;
  const kept = lines.slice(0, maxLines);
  kept[maxLines - 1] = `${kept[maxLines - 1].slice(0, max - 1)}…`;
  return kept;
}

export function nodeHeight(lines: number): number {
  return Math.max(46, 22 + lines * LINE_H);
}

/* ------------------------------------------------------------------ clusters */

/**
 * A taller chapter header for explicit groups: a label row beside the chevron, the group's
 * title wrapped under it, an optional small meta row and an optional share bar.
 */
export interface ClusterHead {
  /** Row 1, e.g. "Área 3 · 49 reactivos (34%)". */
  label: string;
  /** Wrapped under the label to the block width. */
  title: string;
  /** One small mono row, e.g. the area's own counts. */
  meta?: string;
  /** 0..1: drawn as a thin bar (share of the exam). */
  share?: number;
}

export interface ClusterInfo {
  id: string;
  title: string;
  nodeIds: string[];
  counts: Record<NodeStateName, number>;
  head?: ClusterHead;
  arrange?: 'stack' | 'layers';
  minInnerW?: number;
}

/** A chapter the caller decides, instead of one derived from graph shape. */
export interface GroupSpec {
  id: string;
  /** Full name for tooltips and screen readers. */
  title: string;
  /** Members, in display order. Ids missing from the node list are skipped. */
  nodeIds: string[];
  /** `stack`: one box per row, in the given order. `layers`: the dependency layering. */
  arrange: 'stack' | 'layers';
  head?: ClusterHead;
  /** Minimum inner width, so the header has room. */
  minInnerW?: number;
}

/** A per-node box size. Nodes without one use the fixed NODE_W box. */
export interface NodeBox {
  w: number;
  /** Text wrapped inside the box; defaults to the node title. */
  label?: string;
  /** Characters per wrapped line. */
  wrap: number;
  maxLines: number;
  /** Height above the title lines (a ref/weight row). */
  topH: number;
  /** Height under the title lines (metric rows). */
  extraH: number;
}

function emptyCounts(): Record<NodeStateName, number> {
  return { known: 0, fragile: 0, unknown: 0, misconception: 0 };
}

function stateOf(states: Record<string, NodeState> | undefined, id: string): NodeStateName {
  return states?.[id]?.state ?? 'unknown';
}

/** Longest-path layering over structural edges, restricted to `ids`. */
function layerMap(ids: string[], edges: GraphEdge[]): Map<string, number> {
  const inSet = new Set(ids);
  const parents = new Map<string, string[]>();
  for (const id of ids) parents.set(id, []);
  for (const e of edges) {
    if (!STRUCTURAL.has(e.type)) continue;
    if (!inSet.has(e.from) || !inSet.has(e.to)) continue;
    parents.get(e.to)!.push(e.from);
  }
  const layers = new Map<string, number>();
  const visiting = new Set<string>();
  const walk = (id: string, guard: number): number => {
    const seen = layers.get(id);
    if (seen !== undefined) return seen;
    if (guard > ids.length || visiting.has(id)) return 0; // cycle guard
    visiting.add(id);
    let best = 0;
    for (const p of parents.get(id) ?? []) best = Math.max(best, walk(p, guard + 1) + 1);
    visiting.delete(id);
    layers.set(id, best);
    return best;
  };
  for (const id of ids) walk(id, 0);
  return layers;
}

/**
 * Weakly connected components over structural edges, with a root-ancestor fallback when
 * that leaves one giant blob.
 */
export function clustersOf(
  nodes: GraphNode[],
  edges: GraphEdge[],
  states?: Record<string, NodeState>,
): ClusterInfo[] {
  const ids = nodes.map((n) => n.id);
  const title = new Map(nodes.map((n) => [n.id, n.title] as const));
  const parent = new Map(ids.map((i) => [i, i] as const));
  const find = (x: string): string => {
    let r = x;
    while (parent.get(r) !== r) r = parent.get(r)!;
    let c = x;
    while (parent.get(c) !== c) {
      const nx = parent.get(c)!;
      parent.set(c, r);
      c = nx;
    }
    return r;
  };
  for (const e of edges) {
    if (!STRUCTURAL.has(e.type)) continue;
    if (!parent.has(e.from) || !parent.has(e.to)) continue;
    const a = find(e.from);
    const b = find(e.to);
    if (a !== b) parent.set(a, b);
  }

  let groups = new Map<string, string[]>();
  for (const id of ids) {
    const r = find(id);
    if (!groups.has(r)) groups.set(r, []);
    groups.get(r)!.push(id);
  }

  // One giant component: regroup by root ancestor so we still get chapter-sized blocks.
  const biggest = Math.max(0, ...Array.from(groups.values(), (g) => g.length));
  if (ids.length > 12 && biggest >= ids.length * 0.6) {
    const firstParent = new Map<string, string>();
    for (const e of edges) {
      if (!STRUCTURAL.has(e.type)) continue;
      if (!firstParent.has(e.to) && parent.has(e.from)) firstParent.set(e.to, e.from);
    }
    const rootOf = (id: string): string => {
      let cur = id;
      for (let i = 0; i < ids.length; i += 1) {
        const p = firstParent.get(cur);
        if (!p || p === cur) break;
        cur = p;
      }
      return cur;
    };
    groups = new Map();
    for (const id of ids) {
      const r = rootOf(id);
      if (!groups.has(r)) groups.set(r, []);
      groups.get(r)!.push(id);
    }
  }

  const out: ClusterInfo[] = [];
  for (const [root, members] of groups) {
    const layers = layerMap(members, edges);
    // The cluster is named after its entry point: the lowest-layer member, ties broken by
    // the order the gateway listed them (which follows the plan's own order).
    let head = members[0];
    let bestLayer = Number.POSITIVE_INFINITY;
    for (const m of members) {
      const l = layers.get(m) ?? 0;
      if (l < bestLayer) {
        bestLayer = l;
        head = m;
      }
    }
    const counts = emptyCounts();
    for (const m of members) counts[stateOf(states, m)] += 1;
    out.push({ id: root, title: title.get(head) ?? head, nodeIds: members, counts });
  }
  // Biggest chapters first, stable for equal sizes.
  return out.sort((a, b) => b.nodeIds.length - a.nodeIds.length);
}

/**
 * Chapters from explicit groups, in the order given (the exam's order - never re-sorted by
 * size). Members not in `nodes` are dropped, and a group left empty is dropped with them.
 */
export function clustersFromGroups(
  groups: GroupSpec[],
  nodes: GraphNode[],
  states?: Record<string, NodeState>,
): ClusterInfo[] {
  const present = new Set(nodes.map((n) => n.id));
  const out: ClusterInfo[] = [];
  for (const g of groups) {
    const members = g.nodeIds.filter((id) => present.has(id));
    if (!members.length) continue;
    const counts = emptyCounts();
    for (const m of members) counts[stateOf(states, m)] += 1;
    out.push({
      id: g.id,
      title: g.title,
      nodeIds: members,
      counts,
      head: g.head,
      arrange: g.arrange,
      minInnerW: g.minInnerW,
    });
  }
  return out;
}

/* ------------------------------------------------------------------ layout */

export interface PlacedNode {
  id: string;
  title: string;
  lines: string[];
  x: number;
  y: number;
  w: number;
  h: number;
  state: NodeStateName;
  clusterId: string;
  /** Set on the single box that stands in for a collapsed cluster. */
  summary?: { total: number; counts: Record<NodeStateName, number> };
  order?: number;
  /** Sized by a NodeBox: title lines start under `topH` and are left-aligned. */
  box?: { topH: number; extraH: number };
}

export interface PlacedCluster extends ClusterInfo {
  x: number;
  y: number;
  w: number;
  h: number;
  collapsed: boolean;
  total: number;
  /** Header height (HEAD_H for a plain chapter). */
  headH: number;
  /** `head.title` wrapped to the block width. */
  headLines: string[];
}

export interface PlacedEdge {
  key: string;
  from: string;
  to: string;
  type: string;
  provenance: string;
  style: EdgeStyle;
  onPath: boolean;
  d: string;
}

export interface GraphLayout {
  nodes: PlacedNode[];
  clusters: PlacedCluster[];
  edges: PlacedEdge[];
  width: number;
  height: number;
  byId: Map<string, PlacedNode>;
}

export interface LayoutOptions {
  states?: Record<string, NodeState>;
  collapsed?: ReadonlySet<string>;
  pathOrder?: string[];
  /** Aspect ratio the block packing aims for when the viewport width is unknown. */
  aspect?: number;
  /** CSS pixels of canvas width. The packing uses it to keep fit-to-width legible. */
  viewportW?: number;
  /** Explicit chapters (exam areas) instead of the shape-derived ones. */
  groups?: GroupSpec[];
  /** Per-node box size; nodes it returns nothing for keep the fixed box. */
  box?: (id: string) => NodeBox | undefined;
}

interface Block {
  cluster: ClusterInfo;
  collapsed: boolean;
  w: number;
  h: number;
  headH: number;
  headLines: string[];
  nodes: PlacedNode[]; // positions relative to the block origin
}

/** Vertical gap between boxes in a `stack` chapter: an arrow's worth, no more. */
const STACK_GAP = 22;
/** Pixels per character of the 13px header title, for wrapping it to the block width. */
const HEAD_CHAR_PX = 7.4;

/** Header height and wrapped title for a chapter whose inner width is `innerW`. */
function headerFor(cluster: ClusterInfo, innerW: number): { headH: number; headLines: string[] } {
  const head = cluster.head;
  if (!head) return { headH: HEAD_H, headLines: [] };
  const chars = Math.max(12, Math.floor((innerW - 12) / HEAD_CHAR_PX));
  const headLines = head.title ? wrapTitle(head.title, chars, 2) : [];
  const headH =
    34 + headLines.length * 16 + (head.meta ? 16 : 0) + (head.share !== undefined ? 10 : 0) + 6;
  return { headH, headLines };
}

function buildBlock(
  cluster: ClusterInfo,
  nodes: Map<string, GraphNode>,
  edges: GraphEdge[],
  states: Record<string, NodeState> | undefined,
  collapsed: boolean,
  pathIndex: Map<string, number>,
  box?: (id: string) => NodeBox | undefined,
): Block {
  if (collapsed) {
    const dominant = STATE_ORDER.reduce(
      (best, s) => (cluster.counts[s] > cluster.counts[best] ? s : best),
      'unknown' as NodeStateName,
    );
    const innerW = Math.max(NODE_W, cluster.minInnerW ?? 0);
    const { headH, headLines } = headerFor(cluster, innerW);
    const label = cluster.head?.title || cluster.title;
    const lines = wrapTitle(label, WRAP_CHARS, 2);
    const h = nodeHeight(lines.length + 1);
    const node: PlacedNode = {
      id: `cluster:${cluster.id}`,
      title: label,
      lines,
      x: PAD,
      y: headH,
      w: NODE_W,
      h,
      state: dominant,
      clusterId: cluster.id,
      summary: { total: cluster.nodeIds.length, counts: cluster.counts },
    };
    return {
      cluster,
      collapsed,
      w: innerW + PAD * 2,
      h: headH + h + PAD,
      headH,
      headLines,
      nodes: [node],
    };
  }

  if (cluster.arrange === 'stack') {
    // One box per row in the order given, left-aligned, so boxes whose width carries a
    // weight line up like the bars of a chart.
    const sized = cluster.nodeIds.map((id) => {
      const n = nodes.get(id)!;
      const b = box?.(id);
      const lines = b
        ? wrapTitle(b.label ?? n.title, b.wrap, b.maxLines)
        : wrapTitle(n.title);
      const w = b?.w ?? NODE_W;
      const h = b ? b.topH + lines.length * LINE_H + b.extraH : nodeHeight(lines.length);
      return { id, n, b, lines, w, h };
    });
    const innerW = Math.max(cluster.minInnerW ?? 0, ...sized.map((s) => s.w));
    const { headH, headLines } = headerFor(cluster, innerW);
    const placed: PlacedNode[] = [];
    let y = headH;
    for (const s of sized) {
      placed.push({
        id: s.id,
        title: s.n.title,
        lines: s.lines,
        x: PAD,
        y,
        w: s.w,
        h: s.h,
        state: stateOf(states, s.id),
        clusterId: cluster.id,
        order: pathIndex.get(s.id),
        box: s.b ? { topH: s.b.topH, extraH: s.b.extraH } : undefined,
      });
      y += s.h + STACK_GAP;
    }
    return {
      cluster,
      collapsed,
      w: innerW + PAD * 2,
      h: y - STACK_GAP + PAD,
      headH,
      headLines,
      nodes: placed,
    };
  }

  const layers = layerMap(cluster.nodeIds, edges);
  const byLayer = new Map<number, string[]>();
  for (const id of cluster.nodeIds) {
    const l = layers.get(id) ?? 0;
    if (!byLayer.has(l)) byLayer.set(l, []);
    byLayer.get(l)!.push(id);
  }
  // Each layer becomes one or more rows; a layer wider than MAX_PER_ROW wraps so a single
  // fan-out cannot blow the block's width out.
  const rows: string[][] = [];
  for (const l of Array.from(byLayer.keys()).sort((a, b) => a - b)) {
    const group = byLayer.get(l)!;
    for (let i = 0; i < group.length; i += MAX_PER_ROW) rows.push(group.slice(i, i + MAX_PER_ROW));
  }

  const widest = Math.max(1, ...rows.map((r) => r.length));
  const innerW = Math.max(widest * NODE_W + (widest - 1) * GAP_X, cluster.minInnerW ?? 0);
  const { headH, headLines } = headerFor(cluster, innerW);
  const placed: PlacedNode[] = [];
  let y = headH;
  for (const row of rows) {
    const rowW = row.length * NODE_W + (row.length - 1) * GAP_X;
    const startX = PAD + (innerW - rowW) / 2;
    let rowH = 0;
    row.forEach((id, i) => {
      const n = nodes.get(id)!;
      const lines = wrapTitle(n.title);
      const h = nodeHeight(lines.length);
      rowH = Math.max(rowH, h);
      placed.push({
        id,
        title: n.title,
        lines,
        x: startX + i * (NODE_W + GAP_X),
        y,
        w: NODE_W,
        h,
        state: stateOf(states, id),
        clusterId: cluster.id,
        order: pathIndex.get(id),
      });
    });
    y += rowH + GAP_Y;
  }

  return {
    cluster,
    collapsed,
    w: innerW + PAD * 2,
    h: y - GAP_Y + PAD,
    headH,
    headLines,
    nodes: placed,
  };
}

/** Greedy row packing at a target width; returns the resulting bounding box. */
function pack(blocks: Block[], target: number) {
  const rows: Block[][] = [];
  let cur: Block[] = [];
  let curW = 0;
  for (const b of blocks) {
    const add = cur.length ? BLOCK_GAP + b.w : b.w;
    if (cur.length && curW + add > target) {
      rows.push(cur);
      cur = [b];
      curW = b.w;
    } else {
      cur.push(b);
      curW += add;
    }
  }
  if (cur.length) rows.push(cur);

  let width = 0;
  let y = 0;
  const origins = new Map<Block, { x: number; y: number }>();
  for (const row of rows) {
    let x = 0;
    let rowH = 0;
    for (const b of row) {
      origins.set(b, { x, y });
      x += b.w + BLOCK_GAP;
      rowH = Math.max(rowH, b.h);
    }
    width = Math.max(width, x - BLOCK_GAP);
    y += rowH + BLOCK_GAP;
  }
  return { origins, width, height: Math.max(0, y - BLOCK_GAP) };
}

function edgePath(a: PlacedNode, b: PlacedNode): string {
  const ax = a.x + a.w / 2;
  const bx = b.x + b.w / 2;
  const aBottom = a.y + a.h;
  // Straightforward top-to-bottom flow when b sits below a.
  if (b.y > aBottom - 4) {
    const my = (aBottom + b.y) / 2;
    return `M${ax},${aBottom} C${ax},${my} ${bx},${my} ${bx},${b.y}`;
  }
  // Otherwise leave from the side nearest the target and arrive on its opposite side.
  const fromRight = bx >= ax;
  const x1 = fromRight ? a.x + a.w : a.x;
  const y1 = a.y + a.h / 2;
  const x2 = fromRight ? b.x : b.x + b.w;
  const y2 = b.y + b.h / 2;
  const dx = Math.max(40, Math.abs(x2 - x1) / 2);
  const c1 = fromRight ? x1 + dx : x1 - dx;
  const c2 = fromRight ? x2 - dx : x2 + dx;
  return `M${x1},${y1} C${c1},${y1} ${c2},${y2} ${x2},${y2}`;
}

export function layoutGraph(
  nodes: GraphNode[],
  edges: GraphEdge[],
  opts: LayoutOptions = {},
): GraphLayout {
  const { states, collapsed, pathOrder, aspect = 1.6, viewportW, groups, box } = opts;
  const nodeMap = new Map(nodes.map((n) => [n.id, n] as const));
  const pathIndex = new Map<string, number>();
  (pathOrder ?? []).forEach((id, i) => pathIndex.set(id, i + 1));

  const clusters = groups
    ? clustersFromGroups(groups, nodes, states)
    : clustersOf(nodes, edges, states);
  const blocks = clusters.map((c) =>
    buildBlock(c, nodeMap, edges, states, !!collapsed?.has(c.id), pathIndex, box),
  );

  /* Choosing the number of columns is really choosing the zoom you land on: a wider
     packing fits to a smaller scale. So when we know the viewport we take the *widest*
     packing that still fits to width at MIN_FIT_K or better - the most chapters per screen
     that stays readable - and let the rest be reached by panning. With no viewport (server
     render, first paint) fall back to the target aspect. */
  const MIN_FIT_K = 0.8;
  const maxBlockW = Math.max(1, ...blocks.map((b) => b.w));
  const maxCols = Math.min(6, Math.max(1, blocks.length));
  let best: ReturnType<typeof pack> | null = null;
  let bestScore = Number.POSITIVE_INFINITY;
  for (let cols = 1; cols <= maxCols; cols += 1) {
    const target = cols * maxBlockW + (cols - 1) * BLOCK_GAP;
    const p = pack(blocks, target);
    if (!p.height) continue;
    if (viewportW) {
      // widest packing whose fit-to-width scale stays >= MIN_FIT_K; one column always wins
      // by default, so a very narrow viewport still gets a layout.
      if (cols > 1 && p.width * MIN_FIT_K > viewportW) continue;
      if (!best || p.width > best.width) best = p;
    } else {
      const score = Math.abs(p.width / p.height - aspect);
      if (score < bestScore) {
        bestScore = score;
        best = p;
      }
    }
  }
  const packed = best ?? pack(blocks, maxBlockW);

  const placedNodes: PlacedNode[] = [];
  const placedClusters: PlacedCluster[] = [];
  for (const b of blocks) {
    const o = packed.origins.get(b) ?? { x: 0, y: 0 };
    placedClusters.push({
      ...b.cluster,
      x: o.x,
      y: o.y,
      w: b.w,
      h: b.h,
      collapsed: b.collapsed,
      total: b.cluster.nodeIds.length,
      headH: b.headH,
      headLines: b.headLines,
    });
    for (const n of b.nodes) placedNodes.push({ ...n, x: n.x + o.x, y: n.y + o.y });
  }

  const byId = new Map(placedNodes.map((n) => [n.id, n] as const));
  // A collapsed cluster's nodes all resolve to its summary box, so edges in and out of the
  // chapter survive the collapse instead of vanishing.
  const proxy = new Map<string, PlacedNode>();
  for (const c of placedClusters) {
    if (!c.collapsed) continue;
    const s = byId.get(`cluster:${c.id}`);
    if (s) for (const id of c.nodeIds) proxy.set(id, s);
  }
  const resolve = (id: string) => byId.get(id) ?? proxy.get(id);

  const seen = new Set<string>();
  const placedEdges: PlacedEdge[] = [];
  edges.forEach((e, i) => {
    const a = resolve(e.from);
    const b = resolve(e.to);
    if (!a || !b || a.id === b.id) return;
    const key = `${a.id}->${b.id}:${e.type}`;
    if (seen.has(key)) return;
    seen.add(key);
    placedEdges.push({
      key: `${key}:${i}`,
      from: a.id,
      to: b.id,
      type: e.type,
      provenance: e.provenance,
      style: edgeStyle(e.type),
      onPath:
        pathIndex.size > 0 &&
        pathIndex.has(e.from) &&
        pathIndex.has(e.to) &&
        STRUCTURAL.has(e.type),
      d: edgePath(a, b),
    });
  });

  return {
    nodes: placedNodes,
    clusters: placedClusters,
    edges: placedEdges,
    width: Math.max(1, packed.width),
    height: Math.max(1, packed.height),
    byId,
  };
}
