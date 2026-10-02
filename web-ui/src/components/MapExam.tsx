'use client';

/**
 * The exam-blueprint pieces around the concept graphs: the data hook (blueprint + progress),
 * the colour-by toggle, a legend per colour mode, the "wider = more reactivos" key, and the
 * subárea numbers shown at the top of the receipts drawer.
 *
 * Evidence, never decimals: every number here is a count, or a count ratio printed next to
 * its counts (a first-try range, a share of the exam). Nothing is a mastery estimate.
 */

import { useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { api, ApiError, errorMessage } from '@/lib/api';
import {
  ACCURACY_LEGEND,
  COLOUR_BY,
  COVERAGE_LEGEND,
  FEW_FIRST,
  isColourBy,
  paintFor,
  reactivos,
  sharePct,
  type BandTally,
  type ColourBy,
  type ExamNode,
} from '@/lib/mapBlueprint';
import type { Blueprint, Progress } from '@/lib/types';
import { StateLegend } from './GraphCanvas';

export interface ExamData {
  /** false until both calls have settled. */
  ready: boolean;
  blueprint: Blueprint | null;
  progress: Progress | null;
  /** Set when the goal has a blueprint but its progress could not be read. */
  progressError: string | null;
  /** Set when the blueprint call failed for a reason other than "this goal has none". */
  blueprintError: string | null;
}

/**
 * `GET /blueprint` and `GET /progress`, together. A 404 on the blueprint is the contract's
 * "this goal has none" and leaves every screen exactly as it was.
 */
export function useExamData(goalId: string | null): ExamData {
  const [data, setData] = useState<ExamData>({
    ready: false,
    blueprint: null,
    progress: null,
    progressError: null,
    blueprintError: null,
  });
  useEffect(() => {
    if (!goalId) return;
    let live = true;
    Promise.all([
      api.blueprint(goalId).then(
        (b) => ({ b, e: null as string | null }),
        (e: unknown) => ({
          b: null,
          e: e instanceof ApiError && e.status === 404 ? null : errorMessage(e),
        }),
      ),
      api.progress(goalId).then(
        (p) => ({ p, e: null as string | null }),
        (e: unknown) => ({ p: null, e: errorMessage(e) }),
      ),
    ]).then(([bp, pr]) => {
      if (!live) return;
      setData({
        ready: true,
        blueprint: bp.b,
        progress: bp.b ? pr.p : null,
        progressError: bp.b ? pr.e : null,
        blueprintError: bp.e,
      });
    });
    return () => {
      live = false;
    };
  }, [goalId]);
  return data;
}

const COLOUR_KEY = (goalId: string) => `lt-map-colour:${goalId}`;

/** The colour mode, remembered per goal in this browser (read after mount). */
export function useColourBy(goalId: string | null): [ColourBy, (c: ColourBy) => void] {
  const [mode, setMode] = useState<ColourBy>('state');
  useEffect(() => {
    if (!goalId) return;
    try {
      const v = window.localStorage.getItem(COLOUR_KEY(goalId));
      if (isColourBy(v)) setMode(v);
    } catch {
      /* storage disabled: default State */
    }
  }, [goalId]);
  const set = (c: ColourBy) => {
    setMode(c);
    if (!goalId) return;
    try {
      window.localStorage.setItem(COLOUR_KEY(goalId), c);
    } catch {
      /* the choice still holds for this page view */
    }
  };
  return [mode, set];
}

/** State | First-try accuracy | Coverage, as a radio group (arrow keys move and select). */
export function ColourByToggle({
  value,
  onChange,
}: {
  value: ColourBy;
  onChange: (c: ColourBy) => void;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const onKey = (ev: ReactKeyboardEvent<HTMLDivElement>) => {
    const i = COLOUR_BY.findIndex((o) => o.id === value);
    let next = -1;
    if (ev.key === 'ArrowRight' || ev.key === 'ArrowDown') next = (i + 1) % COLOUR_BY.length;
    else if (ev.key === 'ArrowLeft' || ev.key === 'ArrowUp')
      next = (i - 1 + COLOUR_BY.length) % COLOUR_BY.length;
    else if (ev.key === 'Home') next = 0;
    else if (ev.key === 'End') next = COLOUR_BY.length - 1;
    if (next < 0) return;
    ev.preventDefault();
    onChange(COLOUR_BY[next].id);
    refs.current[next]?.focus();
  };
  return (
    <div className="cbtoggle" role="radiogroup" aria-label="Colour the boxes by" onKeyDown={onKey}>
      <span className="cbtoggle-lbl" aria-hidden="true">
        colour by
      </span>
      {COLOUR_BY.map((o, i) => (
        <button
          key={o.id}
          ref={(el) => {
            refs.current[i] = el;
          }}
          type="button"
          role="radio"
          aria-checked={value === o.id}
          tabIndex={value === o.id ? 0 : -1}
          title={o.hint}
          className="cbtoggle-btn"
          onClick={() => onChange(o.id)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** A sample tally that lands in `band`, so a swatch is painted exactly like a box. */
function sampleTally(mode: 'accuracy' | 'coverage', band: string, few = false): BandTally | null {
  if (band === 'none') return null;
  if (mode === 'accuracy') {
    // 10/10, 7/10 and 0/10 sit squarely in their bands; 2/2 is a "few first tries" good.
    const n = few ? 2 : 10;
    const k = band === 'good' ? n : band === 'warn' ? 7 : 0;
    return { first_correct: k, first_attempts: n, seen: 0, bank: 0 };
  }
  const seen = band === 'all' ? 4 : band === 'half' ? 2 : band === 'some' ? 1 : 0;
  return { first_correct: 0, first_attempts: 0, seen, bank: 4 };
}

function Swatch({ mode, band, few = false }: { mode: 'accuracy' | 'coverage'; band: string; few?: boolean }) {
  // Same paint as the boxes, so the key can never drift from what is drawn.
  const p = paintFor(mode, 'unknown', sampleTally(mode, band, few));
  if (p.hatch) return <i className="msw hatch" aria-hidden="true" />;
  return (
    <i
      className="msw"
      aria-hidden="true"
      style={{
        background: `color-mix(in srgb, ${p.fill} ${Math.round(Number(p.fillOpacity) * 100)}%, var(--surface))`,
        borderColor: p.stroke,
      }}
    />
  );
}

/** The key for whichever colour mode is on. */
export function ColourLegend({ mode }: { mode: ColourBy }) {
  if (mode === 'state') return <StateLegend />;
  if (mode === 'accuracy') {
    return (
      <div className="legend" aria-label="First-try accuracy bands">
        {ACCURACY_LEGEND.map((l) => (
          <span className="k" key={l.band}>
            <Swatch mode="accuracy" band={l.band} />
            {l.label}
          </span>
        ))}
        <span className="k">
          <Swatch mode="accuracy" band="good" few />
          paler: under {FEW_FIRST} first tries, the band can still move
        </span>
      </div>
    );
  }
  return (
    <div className="legend" aria-label="Coverage bands: practice questions seen, of the bank">
      {COVERAGE_LEGEND.map((l) => (
        <span className="k" key={l.band}>
          <Swatch mode="coverage" band={l.band} />
          {l.label}
        </span>
      ))}
    </div>
  );
}

/** What box width and the thin bar mean. */
export function WeightLegend() {
  return (
    <div className="legend">
      <span className="k">
        <svg width="34" height="12" aria-hidden="true">
          <rect x="0.5" y="1.5" width="14" height="9" rx="2" className="lgbox" />
          <rect x="17.5" y="1.5" width="16" height="9" rx="2" className="lgbox" />
        </svg>
        wider box = more reactivos
      </span>
      <span className="k">
        <svg width="26" height="8" aria-hidden="true">
          <rect x="0" y="2" width="26" height="4" rx="2" className="lgtrack" />
          <rect x="0" y="2" width="16" height="4" rx="2" className="lgbar" />
        </svg>
        questions seen, of the bank
      </span>
    </div>
  );
}

/** One line under the map title: which exam, how many items, where the numbers come from. */
export function ExamSummary({ blueprint, ghosts }: { blueprint: Blueprint; ghosts: number }) {
  const subs = blueprint.areas.reduce((n, a) => n + a.subareas.length, 0);
  return (
    <p className="small muted examline">
      <strong>{blueprint.exam}</strong> &middot; {reactivos(blueprint.total_items)} in{' '}
      {blueprint.areas.length} areas, {subs} subáreas &middot; {blueprint.source}
      {ghosts ? (
        <>
          {' '}
          &middot;{' '}
          <span className="warnword">
            {ghosts} subárea{ghosts === 1 ? ' has' : 's have'} no concept in this graph (dashed)
          </span>
        </>
      ) : null}
    </p>
  );
}

/**
 * The subárea's numbers, at the top of the receipts drawer: weight, bank, seen, first try
 * with its 95% range, every attempt, due now.
 */
export function ExamNumbers({ info, hasProgress }: { info: ExamNode; hasProgress: boolean }) {
  const p = info.progress;
  const seenFrac = p && p.bank > 0 ? Math.min(1, p.seen / p.bank) : 0;
  return (
    <section className="examnums" aria-label={`Exam numbers for ${info.ref} ${info.title}`}>
      <div className="row between">
        <span className="eyebrow">
          exam blueprint &middot; {info.ref} &middot; área {info.areaCode}
        </span>
        <span className="pill accent">
          {reactivos(info.examItems)} &middot; {sharePct(info.share)} of the exam
        </span>
      </div>
      <p className="small">{info.title}</p>
      {p ? (
        <div className="table-wrap">
          <table>
            <tbody>
              <tr>
                <td className="mono muted">practice bank</td>
                <td className="mono">
                  {p.bank} question{p.bank === 1 ? '' : 's'}
                  {p.bank ? ` · ${p.checked} checked` : ''}
                  {p.sealed ? ` · ${p.sealed} sealed for mocks` : ''}
                </td>
              </tr>
              <tr>
                <td className="mono muted">seen</td>
                <td className="mono">
                  {p.bank ? `${p.seen} of ${p.bank}` : 'nothing to see yet'}
                  {p.bank ? (
                    <span className="numbar" aria-hidden="true">
                      <span style={{ width: `${seenFrac * 100}%` }} />
                    </span>
                  ) : null}
                </td>
              </tr>
              <tr>
                <td className="mono muted">first try</td>
                <td className="mono">
                  {p.first_attempts ? (
                    <>
                      {p.first_correct} of {p.first_attempts} right
                      {p.low !== null && p.high !== null ? (
                        <span className="muted">
                          {' '}
                          &middot; 95% range {p.low}&ndash;{p.high}%
                        </span>
                      ) : null}
                    </>
                  ) : (
                    <span className="muted">no first try yet &middot; no range until there is one</span>
                  )}
                </td>
              </tr>
              <tr>
                <td className="mono muted">all attempts</td>
                <td className="mono">
                  {p.attempts ? `${p.correct} of ${p.attempts} right` : 'none yet'}
                </td>
              </tr>
              <tr>
                <td className="mono muted">due now</td>
                <td className="mono">{p.due_now}</td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : (
        <p className="small muted">
          {hasProgress
            ? 'No practice numbers for this subárea yet.'
            : 'Progress numbers could not be read, so only the exam weight is shown.'}
        </p>
      )}
      {p && p.first_attempts > 0 && p.first_attempts < FEW_FIRST ? (
        <p className="small muted">
          Under {FEW_FIRST} first tries the range is wide: the counts say more than any
          percentage would.
        </p>
      ) : null}
      {info.ghost ? (
        <p className="note warn small">
          This subárea does not resolve to a concept in the goal&rsquo;s graph, so there are no
          receipts to open. Its weight still counts on the exam.
        </p>
      ) : null}
    </section>
  );
}
