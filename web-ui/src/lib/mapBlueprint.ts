/**
 * The exam blueprint laid over the concept graph. Pure functions, no React, no fetching:
 * `GET /blueprint` gives the structure (areas → subáreas, official order, item counts) and
 * `GET /progress` gives the numbers (bank, seen, first tries, due), and this file turns the
 * pair into what `lib/graph.ts` places and `GraphCanvas` paints.
 *
 * Why the blueprint and not graph shape: for a fixed-date exam the question the map has to
 * answer is "where are the points, and how am I doing there". The exam's areas are the
 * chapters, in the guide's order, and a subárea's box is as wide as its share of the exam.
 *
 * Evidence, never decimals: a box shows counts ("7/10 first try", "2/3 seen"), never a
 * percentage on a concept. The colour bands below are drawn from the same counts, the
 * legend says which band is which, and "no first attempt" is its own hatched style - no
 * data is not 0.
 */

import { FLOOR, TARGET, pct } from './progress';
import type { GroupSpec, NodeBox } from './graph';
import type {
  AreaProgress,
  Blueprint,
  GraphNode,
  NodeProgress,
  NodeStateName,
  Progress,
  Tally,
} from './types';

export type ColourBy = 'state' | 'accuracy' | 'coverage';

export const COLOUR_BY: { id: ColourBy; label: string; hint: string }[] = [
  { id: 'state', label: 'State', hint: 'the four evidence states' },
  { id: 'accuracy', label: 'First-try accuracy', hint: 'right on the first attempt, in bands' },
  { id: 'coverage', label: 'Coverage', hint: 'practice questions seen, of the bank' },
];

export function isColourBy(v: unknown): v is ColourBy {
  return v === 'state' || v === 'accuracy' || v === 'coverage';
}

/** One subárea of the blueprint, with its numbers. */
export interface ExamNode {
  /** The graph node id, or `bp:<ref>` when the ref resolves to no live concept. */
  id: string;
  ref: string;
  /** The official subárea title (the graph's own title may carry the ref as a prefix). */
  title: string;
  areaCode: string;
  areaTitle: string;
  examItems: number;
  /** Share of the exam, 0..1. */
  share: number;
  progress: NodeProgress | null;
  /** Not in the goal's graph: drawn dashed, and there are no receipts to open. */
  ghost: boolean;
}

export interface ExamArea {
  code: string;
  title: string;
  examItems: number;
  share: number;
  progress: AreaProgress | null;
  /** Member ids in official order (graph ids or `bp:<ref>`). */
  nodeIds: string[];
}

export interface ExamOverlay {
  exam: string;
  source: string;
  totalItems: number;
  /** The heaviest subárea's item count; box widths scale against it. */
  maxItems: number;
  areas: ExamArea[];
  nodes: Map<string, ExamNode>;
  /** Progress for concepts outside the blueprint, by node id. */
  outside: Map<string, NodeProgress>;
  /** Stand-in graph nodes for subáreas the graph does not have. */
  ghosts: GraphNode[];
  /** false when `/progress` failed: boxes show weights only. */
  hasProgress: boolean;
}

/**
 * Join the blueprint (structure) with progress (numbers) and the graph's node list.
 * Progress rows are matched by node id first, then by ref.
 */
export function buildOverlay(
  bp: Blueprint,
  progress: Progress | null,
  graphNodes: GraphNode[],
): ExamOverlay {
  const inGraph = new Set(graphNodes.map((n) => n.id));
  const subsById = new Map<string, NodeProgress>();
  const subsByRef = new Map<string, NodeProgress>();
  const areaByCode = new Map<string, AreaProgress>();
  for (const a of progress?.areas ?? []) {
    areaByCode.set(a.code, a);
    for (const s of a.subareas) {
      subsById.set(s.node_id, s);
      if (s.ref) subsByRef.set(s.ref, s);
    }
  }

  const nodes = new Map<string, ExamNode>();
  const ghosts: GraphNode[] = [];
  const areas: ExamArea[] = [];
  let maxItems = 1;
  for (const a of bp.areas) {
    const ids: string[] = [];
    for (const s of a.subareas) {
      const id = s.node_id ?? `bp:${s.ref}`;
      if (nodes.has(id)) continue; // two refs on one concept: the first row wins
      const ghost = !inGraph.has(id);
      nodes.set(id, {
        id,
        ref: s.ref,
        title: s.title,
        areaCode: a.code,
        areaTitle: a.title,
        examItems: s.exam_items,
        share: s.share,
        progress: (s.node_id ? subsById.get(s.node_id) : undefined) ?? subsByRef.get(s.ref) ?? null,
        ghost,
      });
      if (ghost) ghosts.push({ id, title: `${s.ref} ${s.title}`, aliases: [s.ref] });
      ids.push(id);
      maxItems = Math.max(maxItems, s.exam_items);
    }
    areas.push({
      code: a.code,
      title: a.title,
      examItems: a.exam_items,
      share: a.share,
      progress: areaByCode.get(a.code) ?? null,
      nodeIds: ids,
    });
  }

  return {
    exam: bp.exam,
    source: bp.source,
    totalItems: bp.total_items,
    maxItems,
    areas,
    nodes,
    outside: new Map((progress?.unassigned ?? []).map((u) => [u.node_id, u] as const)),
    ghosts,
    hasProgress: !!progress,
  };
}

/** Whole percent of the exam, e.g. 0.3427 → "34%". */
export function sharePct(share: number): string {
  return `${Math.round(share * 100)}%`;
}

export function reactivos(n: number): string {
  return `${n} reactivo${n === 1 ? '' : 's'}`;
}

/** "Área 3 · Desarrollo de Sistemas de Software · 49 reactivos (34%)" */
export function areaHeader(a: Pick<ExamArea, 'code' | 'title' | 'examItems' | 'share'>): string {
  return `Área ${a.code} · ${a.title} · ${reactivos(a.examItems)} (${sharePct(a.share)})`;
}

/** "7/10 first try", or "no first try yet". Counts only. */
export function firstTryText(t: Pick<Tally, 'first_correct' | 'first_attempts'> | null): string {
  if (!t || t.first_attempts === 0) return 'no first try yet';
  return `${t.first_correct}/${t.first_attempts} first try`;
}

/** "2/3 seen", or "no questions yet" for an empty bank. */
export function seenText(t: Pick<Tally, 'seen' | 'bank'> | null): string {
  if (!t || t.bank === 0) return 'no questions yet';
  return `${t.seen}/${t.bank} seen`;
}

function areaMeta(p: AreaProgress | null): string | undefined {
  if (!p) return undefined;
  const due = p.due_now ? ` · ${p.due_now} due` : '';
  return `${firstTryText(p)} · ${seenText(p)}${due}`;
}

/** The chapter of concepts the blueprint does not name. */
export const OUTSIDE_GROUP = 'outside';

/**
 * Blueprint chapters' minimum inner width: room for "Área 3 · 49 reactivos (34%)". With
 * BOX_MAX_W this keeps the four ISOFT areas at ~1220 px, so they sit in one row at >= 0.8x
 * in a ~1000 px plan panel instead of wrapping the fourth area below the canvas.
 */
const AREA_MIN_W = 250;

/**
 * Chapters for `layoutGraph`: one per area (official order, subáreas stacked in official
 * order), then the concepts outside the blueprint in the graph's own order.
 */
export function examGroups(
  o: ExamOverlay,
  visible: GraphNode[],
): GroupSpec[] {
  const ids = new Set(visible.map((n) => n.id));
  const groups: GroupSpec[] = o.areas.map((a) => ({
    id: `area:${a.code}`,
    title: areaHeader(a),
    nodeIds: a.nodeIds.filter((id) => ids.has(id)),
    arrange: 'stack' as const,
    minInnerW: AREA_MIN_W,
    head: {
      label: `Área ${a.code} · ${reactivos(a.examItems)} (${sharePct(a.share)})`,
      title: a.title,
      meta: areaMeta(a.progress),
      share: a.share,
    },
  }));
  const outside = visible.filter((n) => !o.nodes.has(n.id)).map((n) => n.id);
  if (outside.length) {
    groups.push({
      id: OUTSIDE_GROUP,
      title: 'Not in the blueprint',
      nodeIds: outside,
      arrange: 'layers',
    });
  }
  return groups;
}

/* ------------------------------------------------------------------ box geometry */

export const BOX_MIN_W = 180;
const BOX_MAX_W = 300;
const BOX_PAD_X = 10;
/** Average px per character of the 12px title, a little generous so lines never overrun. */
const TITLE_CHAR_PX = 6.8;
/** The ref + weight row. */
export const BOX_TOP_H = 26;
/** The coverage bar and its caption row. */
export const BOX_EXTRA_H = 34;

/**
 * Width carries weight: a box is as wide as its share of the heaviest subárea, with a floor
 * so the lightest one still holds its numbers. 16 items → 300 px, 12 → 233, 7 → 180 (floor).
 */
export function boxWidth(items: number, maxItems: number): number {
  const w = 30 + ((BOX_MAX_W - 30) * items) / Math.max(1, maxItems);
  return Math.max(BOX_MIN_W, Math.round(w));
}

export function examBox(o: ExamOverlay, id: string): NodeBox | undefined {
  const info = o.nodes.get(id);
  if (!info) return undefined;
  const w = boxWidth(info.examItems, o.maxItems);
  return {
    w,
    label: info.title,
    wrap: Math.max(14, Math.floor((w - BOX_PAD_X * 2) / TITLE_CHAR_PX)),
    maxLines: 4,
    topH: BOX_TOP_H,
    extraH: BOX_EXTRA_H,
  };
}

/* ------------------------------------------------------------------ colour bands */

export type AccuracyBand = 'good' | 'warn' | 'bad' | 'none';
export type CoverageBand = 'all' | 'half' | 'some' | 'zero' | 'none';

/** Under this many first attempts the band can still move a lot; drawn paler. */
export const FEW_FIRST = 5;

/**
 * First-try accuracy in bands against the plan's target lines (TARGET/FLOOR, the same
 * constants the Progress tab draws). `none` = no first attempt: not 0.
 */
export function accuracyBand(t: Pick<Tally, 'first_correct' | 'first_attempts'> | null): {
  band: AccuracyBand;
  few: boolean;
} {
  const p = t ? pct(t.first_correct, t.first_attempts) : null;
  if (p === null || !t) return { band: 'none', few: false };
  const few = t.first_attempts < FEW_FIRST;
  if (p >= TARGET) return { band: 'good', few };
  if (p >= FLOOR) return { band: 'warn', few };
  return { band: 'bad', few };
}

/** Seen of the practice bank. `none` = no questions in the bank (nothing to cover). */
export function coverageBand(t: Pick<Tally, 'seen' | 'bank'> | null): CoverageBand {
  if (!t || t.bank === 0) return 'none';
  if (t.seen <= 0) return 'zero';
  if (t.seen >= t.bank) return 'all';
  return t.seen * 2 >= t.bank ? 'half' : 'some';
}

export const ACCURACY_LEGEND: { band: AccuracyBand; label: string }[] = [
  { band: 'good', label: `${TARGET}%+ right first try` },
  { band: 'warn', label: `${FLOOR}–${TARGET - 1}%` },
  { band: 'bad', label: `under ${FLOOR}%` },
  { band: 'none', label: 'no first try yet' },
];

export const COVERAGE_LEGEND: { band: CoverageBand; label: string }[] = [
  { band: 'all', label: 'all seen' },
  { band: 'half', label: 'half or more' },
  { band: 'some', label: 'under half' },
  { band: 'zero', label: 'none seen yet' },
  { band: 'none', label: 'no questions' },
];

const STATE_VAR: Record<NodeStateName, string> = {
  known: 'var(--known)',
  fragile: 'var(--fragile)',
  unknown: 'var(--unknown)',
  misconception: 'var(--miscon)',
};

const ACC_VAR: Record<Exclude<AccuracyBand, 'none'>, string> = {
  good: 'var(--known)',
  warn: 'var(--fragile)',
  bad: 'var(--miscon)',
};

const COV_ALPHA: Record<Exclude<CoverageBand, 'none'>, number> = {
  zero: 0.05,
  some: 0.17,
  half: 0.31,
  all: 0.48,
};

export interface Paint {
  fill: string;
  fillOpacity: number | string;
  stroke: string;
  /** Hatched "no data" fill; the caller supplies the pattern id. */
  hatch: boolean;
  /** Short band name, for aria text and tooltips. */
  band: string;
}

export type BandTally = Pick<Tally, 'first_correct' | 'first_attempts' | 'seen' | 'bank'>;

/** How to fill a box under the active colour mode. `tally` null = no numbers for it. */
export function paintFor(mode: ColourBy, state: NodeStateName, tally: BandTally | null): Paint {
  if (mode === 'accuracy') {
    const { band, few } = accuracyBand(tally);
    if (band === 'none') {
      return { fill: 'none', fillOpacity: 1, stroke: 'var(--unknown)', hatch: true, band: 'no first try yet' };
    }
    const label = ACCURACY_LEGEND.find((l) => l.band === band)?.label ?? band;
    return {
      fill: ACC_VAR[band],
      fillOpacity: few ? 0.1 : 0.3,
      stroke: ACC_VAR[band],
      hatch: false,
      band: few ? `${label} (under ${FEW_FIRST} first tries)` : label,
    };
  }
  if (mode === 'coverage') {
    const band = coverageBand(tally);
    if (band === 'none') {
      return { fill: 'none', fillOpacity: 1, stroke: 'var(--unknown)', hatch: true, band: 'no questions' };
    }
    return {
      fill: 'var(--accent)',
      fillOpacity: COV_ALPHA[band],
      stroke: 'var(--accent)',
      hatch: false,
      band: COVERAGE_LEGEND.find((l) => l.band === band)?.label ?? band,
    };
  }
  return {
    fill: STATE_VAR[state],
    fillOpacity: 'var(--fill-alpha)',
    stroke: STATE_VAR[state],
    hatch: false,
    band: state,
  };
}

/** The numbers behind a box: its subárea's, or a concept's outside the blueprint. */
export function tallyOf(o: ExamOverlay, id: string): Tally | null {
  return o.nodes.get(id)?.progress ?? o.outside.get(id) ?? null;
}

/** One sentence per subárea for screen readers; counts only. */
export function describeNode(info: ExamNode, state: NodeStateName): string {
  const p = info.progress;
  const parts = [
    `${info.ref} ${info.title}`,
    reactivos(info.examItems),
    `${sharePct(info.share)} of the exam`,
    state,
  ];
  if (info.ghost) parts.push('not in the concept graph');
  if (p) {
    parts.push(
      p.bank ? `${p.seen} of ${p.bank} practice questions seen` : 'no practice questions yet',
      p.first_attempts
        ? `${p.first_correct} of ${p.first_attempts} right on the first try`
        : 'no first try yet',
    );
    if (p.due_now) parts.push(`${p.due_now} due now`);
  }
  return parts.join(', ');
}
