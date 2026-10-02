'use client';

/**
 * Chart primitives for the exam-prep screens. Inline SVG and plain HTML, no chart library.
 *
 * Design rules:
 * - one hue: `--viz-solid` (the accent) for the series that matters, `--viz-soft` (a lighter
 *   step of the same hue, at least 2.7:1 against the surface in both themes) for its remainder, and
 *   `--viz-muted` gray only for context that is not evidence (card flips, exam weight);
 * - thin marks: columns <= 24px with a 4px rounded top and a 2px surface gap between stacked
 *   segments; dots >= 8px with a 2px surface ring; hairline solid gridlines;
 * - every chart has a text summary (`aria-label`) and a table twin, so no value is reachable
 *   only by hovering; hover and keyboard focus show the same readout.
 */

import {
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import { FLOOR, TARGET, ticks } from '@/lib/progress';

/* ------------------------------------------------------------------ sizing */

const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect;

/** The rendered width of an element, tracked with ResizeObserver (text never scales). */
export function useWidth<T extends HTMLElement>(fallback = 640): [React.RefObject<T | null>, number] {
  const ref = useRef<T | null>(null);
  const [w, setW] = useState(fallback);
  useIsoLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.clientWidth || fallback);
    if (typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver((entries) => {
      const next = Math.round(entries[0].contentRect.width);
      if (next > 0) setW(next);
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [fallback]);
  return [ref, w];
}

/* ------------------------------------------------------------------ frame */

/**
 * A chart container: title, one-line reading guide, legend, the chart, and its table twin
 * behind "Show as a table".
 */
export function ChartFrame({
  title,
  sub,
  legend,
  table,
  children,
  id,
}: {
  title: ReactNode;
  sub?: ReactNode;
  legend?: ReactNode;
  table?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <figure className="vizcard" id={id}>
      <figcaption className="vizhead">
        <h3>{title}</h3>
        {sub ? <p className="small muted">{sub}</p> : null}
      </figcaption>
      {legend ? <div className="vizlegend">{legend}</div> : null}
      {children}
      {table ? (
        <details className="viztable">
          <summary>Show as a table</summary>
          <div className="table-wrap">{table}</div>
        </details>
      ) : null}
    </figure>
  );
}

/** A legend key: a swatch that mirrors the mark (rect for columns, dot for points). */
export function Key({ swatch, children }: { swatch: 'solid' | 'soft' | 'muted' | 'dot' | 'range' | 'target' | 'floor' | 'exam'; children: ReactNode }) {
  return (
    <span className="vizkey">
      <i className={`sw sw-${swatch}`} aria-hidden="true" />
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ strips and meters */

/**
 * First-try accuracy on a 0-100% track: the 95% range as a whisker and the observed ratio as
 * a dot, with the plan's 70% floor and 80% target drawn as reference lines. With no attempts
 * the track is empty and says "no data yet" - never a dot at 0.
 */
export function AccuracyStrip({
  point,
  low,
  high,
  label,
  compact = false,
}: {
  point: number | null;
  low: number | null;
  high: number | null;
  /** The text summary for assistive tech, e.g. "7 of 10 first try, 70%, range 40 to 89%". */
  label: string;
  compact?: boolean;
}) {
  const none = point === null;
  return (
    <div className={`strip${compact ? ' compact' : ''}${none ? ' empty' : ''}`} role="img" aria-label={label}>
      <span className="strip-ref floor" style={{ left: `${FLOOR}%` }} />
      <span className="strip-ref target" style={{ left: `${TARGET}%` }} />
      {none ? (
        <span className="strip-none">no data yet</span>
      ) : (
        <>
          {low !== null && high !== null ? (
            <span className="strip-range" style={{ left: `${low}%`, width: `${Math.max(0.6, high - low)}%` }} />
          ) : null}
          <span className="strip-dot" style={{ left: `${point}%` }} />
        </>
      )}
    </div>
  );
}

/** The 0 / 70 / 80 / 100 labels that sit above a column of strips. */
export function StripAxis() {
  return (
    <div className="strip-axis" aria-hidden="true">
      <span style={{ left: '0%' }}>0</span>
      <span style={{ left: '50%' }}>50</span>
      {/* numbers only: "70 floor" and "80 target" would collide 10% apart; the legend names them */}
      <span className="floor" style={{ left: `${FLOOR}%` }}>
        {FLOOR}
      </span>
      <span className="target" style={{ left: `${TARGET}%` }}>
        {TARGET}
      </span>
      <span style={{ left: '100%' }}>100%</span>
    </div>
  );
}

/** A 0-100 meter: a same-ramp track with a fill. `tone` muted = context, solid = yours. */
export function Meter({ value, tone, label }: { value: number | null; tone: 'solid' | 'muted'; label: string }) {
  return (
    <div className={`meter ${tone}`} role="img" aria-label={label}>
      {value ? <i style={{ width: `${Math.min(100, Math.max(0, value))}%` }} /> : null}
    </div>
  );
}

/* ------------------------------------------------------------------ columns */

export interface Column {
  key: string;
  /** Short x label ("12 Sep"), shown for every `xEvery`-th column. */
  x: string;
  /** Stacked values, bottom first; index = series index. */
  parts: number[];
  /** The readout for hover and keyboard focus. */
  tip: ReactNode;
  /** The same readout in words, for the live region. */
  text: string;
  /** Optional label over the column (used sparingly: mock scores). */
  top?: string | null;
}

export interface ColumnMarker {
  index: number;
  label: string;
  kind: 'today' | 'exam';
}

const PAD = { left: 34, right: 10, top: 20, bottom: 24 };

function roundedTop(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + h}V${y + rr}Q${x},${y} ${x + rr},${y}H${x + w - rr}Q${x + w},${y} ${x + w},${y + rr}V${y + h}Z`;
}

/**
 * Columns over a categorical or daily x axis, optionally stacked (series share one hue in
 * ordinal steps). Hover or arrow keys move a readout; the table twin carries every value.
 */
export function ColumnChart({
  cols,
  seriesClass,
  height = 170,
  yMax,
  percent = false,
  refLines = [],
  markers = [],
  xEvery = 1,
  ariaLabel,
  minWidth = 0,
}: {
  cols: Column[];
  /** CSS class per series index: 'solid' | 'soft' | 'muted'. */
  seriesClass: string[];
  height?: number;
  yMax?: number;
  percent?: boolean;
  refLines?: { value: number; label: string; kind: 'target' | 'floor' }[];
  markers?: ColumnMarker[];
  xEvery?: number;
  ariaLabel: string;
  minWidth?: number;
}) {
  const [wrapRef, width] = useWidth<HTMLDivElement>(560);
  const [active, setActive] = useState<number | null>(null);
  const liveId = useId();

  const w = Math.max(minWidth, width);
  const plotW = Math.max(40, w - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;
  const n = Math.max(1, cols.length);
  const band = plotW / n;
  const barW = Math.max(2, Math.min(24, band * 0.7));
  const dataMax = Math.max(0, ...cols.map((c) => c.parts.reduce((a, b) => a + b, 0)));
  const yTicks = percent ? [0, 50, 100] : ticks(yMax ?? Math.max(1, dataMax), 3);
  const top = yMax ?? yTicks[yTicks.length - 1];
  const y = (v: number) => PAD.top + plotH - (v / top) * plotH;
  const cx = (i: number) => PAD.left + band * (i + 0.5);

  const onMove = useCallback(
    (ev: React.PointerEvent<SVGRectElement>) => {
      const box = (ev.currentTarget.ownerSVGElement as SVGSVGElement).getBoundingClientRect();
      const x = ((ev.clientX - box.left) / box.width) * w - PAD.left;
      setActive(Math.min(n - 1, Math.max(0, Math.floor(x / band))));
    },
    [band, n, w],
  );

  const onKey = (ev: React.KeyboardEvent<HTMLDivElement>) => {
    const cur = active ?? n - 1;
    let next: number | null = null;
    if (ev.key === 'ArrowRight') next = Math.min(n - 1, active === null ? n - 1 : cur + 1);
    else if (ev.key === 'ArrowLeft') next = Math.max(0, active === null ? n - 1 : cur - 1);
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = n - 1;
    else if (ev.key === 'Escape') {
      setActive(null);
      return;
    }
    if (next === null) return;
    ev.preventDefault();
    setActive(next);
  };

  const tipLeft = active === null ? 0 : Math.min(w - 90, Math.max(90, cx(active)));

  return (
    <div
      ref={wrapRef}
      className="colchart"
      tabIndex={0}
      role="group"
      aria-label={`${ariaLabel} Use the left and right arrow keys to read each column.`}
      aria-describedby={liveId}
      onKeyDown={onKey}
      onBlur={() => setActive(null)}
    >
      <svg width={w} height={height} viewBox={`0 0 ${w} ${height}`} aria-hidden="true" focusable="false">
        {/* gridlines + y ticks */}
        {yTicks.map((t) => (
          <g key={`t${t}`}>
            <line className="viz-grid" x1={PAD.left} x2={PAD.left + plotW} y1={y(t)} y2={y(t)} />
            <text className="viz-tick" x={PAD.left - 6} y={y(t) + 3.5} textAnchor="end">
              {percent ? `${t}%` : t}
            </text>
          </g>
        ))}
        {active !== null ? (
          <rect className="viz-hover" x={PAD.left + band * active} y={PAD.top} width={band} height={plotH} />
        ) : null}
        {/* columns */}
        {cols.map((c, i) => {
          let acc = 0;
          const total = c.parts.reduce((a, b) => a + b, 0);
          const lastIdx = c.parts.reduce((last, v, k) => (v > 0 ? k : last), -1);
          const x = cx(i) - barW / 2;
          return (
            <g key={c.key} className={active === i ? 'viz-col on' : 'viz-col'}>
              {c.parts.map((v, k) => {
                if (v <= 0) return null;
                const y0 = y(acc);
                acc += v;
                const y1 = y(acc);
                // 2px surface gap between stacked segments, never a stroke around them
                const gap = acc - v > 0 ? 2 : 0;
                const h = Math.max(0, y0 - y1 - gap);
                if (h <= 0) return null;
                return k === lastIdx ? (
                  <path key={k} className={`viz-bar ${seriesClass[k] ?? 'solid'}`} d={roundedTop(x, y1, barW, h, 4)} />
                ) : (
                  <rect key={k} className={`viz-bar ${seriesClass[k] ?? 'solid'}`} x={x} y={y1} width={barW} height={h} />
                );
              })}
              {c.top && total >= 0 ? (
                <text className="viz-top" x={cx(i)} y={y(total) - 5} textAnchor="middle">
                  {c.top}
                </text>
              ) : null}
            </g>
          );
        })}
        {/* reference lines (plan thresholds) */}
        {refLines.map((r) => (
          <g key={r.label}>
            <line className={`viz-ref ${r.kind}`} x1={PAD.left} x2={PAD.left + plotW} y1={y(r.value)} y2={y(r.value)} />
          </g>
        ))}
        {/* baseline */}
        <line className="viz-axis" x1={PAD.left} x2={PAD.left + plotW} y1={y(0)} y2={y(0)} />
        {/* x labels */}
        {cols.map((c, i) =>
          i % xEvery === 0 || i === n - 1 ? (
            <text
              key={`x${c.key}`}
              className="viz-tick"
              x={cx(i)}
              y={height - 7}
              textAnchor={i === 0 && n > 3 ? 'start' : i === n - 1 && n > 3 ? 'end' : 'middle'}
              dx={i === 0 && n > 3 ? -barW / 2 : i === n - 1 && n > 3 ? barW / 2 : 0}
            >
              {c.x}
            </text>
          ) : null,
        )}
        {/* today / exam markers */}
        {markers.map((m) =>
          m.index >= 0 && m.index < n ? (
            <g key={`${m.kind}${m.index}`} className={`viz-marker ${m.kind}`}>
              <line x1={cx(m.index)} x2={cx(m.index)} y1={PAD.top - 4} y2={PAD.top + plotH} />
              <text
                x={cx(m.index)}
                y={PAD.top - 8}
                textAnchor={m.index > n * 0.8 ? 'end' : m.index < n * 0.2 ? 'start' : 'middle'}
              >
                {m.label}
              </text>
            </g>
          ) : null,
        )}
        {/* one hit layer for the whole plot: the pointer aims at a date, not a 2px bar */}
        <rect
          className="viz-hit"
          x={PAD.left}
          y={PAD.top}
          width={plotW}
          height={plotH}
          onPointerMove={onMove}
          onPointerLeave={() => setActive(null)}
        />
      </svg>
      {active !== null ? (
        <div className="viz-tip" style={{ left: tipLeft }} aria-hidden="true">
          {cols[active].tip}
        </div>
      ) : null}
      <p id={liveId} className="sr-only" aria-live="polite">
        {active !== null ? cols[active].text : ''}
      </p>
    </div>
  );
}
