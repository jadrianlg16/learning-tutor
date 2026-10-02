'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import {
  FLOOR,
  TARGET,
  dayLabel,
  firstTry,
  pct,
  seenPct,
  tallyVerdict,
  todayPlan,
  type Tone,
} from '@/lib/progress';
import type { AreaProgress, NodeProgress, Progress, StudyResponse, Tally } from '@/lib/types';
import { ErrorNote, Loading, StatePill } from './Bits';
import {
  AccuracyStrip,
  ChartFrame,
  ColumnChart,
  Key,
  Meter,
  StripAxis,
  type Column,
  type ColumnMarker,
} from './Charts';

/**
 * Progress toward a fixed-date exam, from `GET /progress` only.
 *
 * Evidence, never decimals: the one percentage per concept is first-try accuracy - a count
 * ratio, always printed with its counts ("7 of 10 first try") and its 95% range. It is never
 * called mastery, and with no attempts the screen says "no data yet", not 0%. The concept's
 * STATE (the map colour) is shown as a word and never gets a number.
 */
export function ProgressPanel({
  goalId,
  study,
  onGo,
}: {
  goalId: string;
  study: StudyResponse | null;
  /** Switch tabs: "practice" (optionally focused) or "mock". */
  onGo: (tab: 'practice' | 'mock', focus?: string) => void;
}) {
  const [p, setP] = useState<Progress | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setError(null);
    api
      .progress(goalId)
      .then(setP)
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  if (error) return <ErrorNote message={error} />;
  if (!p) return <Loading what="your progress" />;

  const plan = study ? todayPlan(study.bank, study.cards, p, p.mocks) : null;
  const submitted = p.mocks.filter((m) => m.submitted_at);

  return (
    <div className="stack gap-lg">
      <Header p={p} plan={plan} onGo={onGo} mocksTaken={submitted.length} />

      {p.has_blueprint ? (
        <>
          <AreasChart areas={p.areas} onGo={onGo} />
          <SubareaGrid goalId={goalId} areas={p.areas} onGo={onGo} />
        </>
      ) : (
        <p className="note">
          This goal has no exam blueprint, so there are no areas to weigh. Concepts are listed
          below by first-try accuracy; set a blueprint with <code>learner goal blueprint</code>{' '}
          to see exam weight.
        </p>
      )}

      {p.unassigned.length ? (
        <NodeGrid
          goalId={goalId}
          title={p.has_blueprint ? 'Concepts outside the blueprint' : 'By concept'}
          sub={
            p.has_blueprint
              ? 'Practised here but not part of any subárea, so they carry no exam weight.'
              : 'First-try accuracy per concept, with its 95% range.'
          }
          nodes={p.unassigned}
        />
      ) : null}

      <div className="vizpair">
        <ActivityChart p={p} />
        <ForecastChart p={p} />
      </div>

      <MockHistory p={p} onGo={onGo} />
    </div>
  );
}

/* ------------------------------------------------------------------ header */

function Header({
  p,
  plan,
  onGo,
  mocksTaken,
}: {
  p: Progress;
  plan: ReturnType<typeof todayPlan> | null;
  onGo: (tab: 'practice' | 'mock', focus?: string) => void;
  mocksTaken: number;
}) {
  const days = p.days_left;
  const covered = Math.round(p.disciplinar.coverage * 100);
  return (
    <section className="card progresshead" aria-label="Where you stand today">
      <div className="kpis">
        <div className="kpi hero">
          <span className="eyebrow">{p.has_blueprint ? 'exam' : 'deadline'}</span>
          {days === null ? (
            <>
              <span className="kpi-value">no date</span>
              <span className="small muted">Set a deadline with edit dates on the session screen.</span>
            </>
          ) : (
            <>
              <span className="kpi-value">
                {days > 0 ? days : days === 0 ? 'today' : 'past'}
                {days > 0 ? <span className="kpi-unit"> day{days === 1 ? '' : 's'} left</span> : null}
              </span>
              <span className="small muted">{p.deadline ? dayLabel(p.deadline) : ''}</span>
            </>
          )}
        </div>
        <div className="kpi">
          <span className="eyebrow">today</span>
          <span className="kpi-value sm">
            {plan ? `${plan.newQuestions} new · ${plan.dueQuestions} due` : '...'}
          </span>
          <span className="small muted">
            {plan ? `questions, plus ${plan.cards} card${plan.cards === 1 ? '' : 's'}` : ''}
          </span>
        </div>
        {p.has_blueprint ? (
          <div className="kpi">
            <span className="eyebrow">exam weight touched</span>
            <span className="kpi-value sm">{covered}%</span>
            <span className="small muted">of the blueprint has at least one first attempt</span>
          </div>
        ) : null}
        <div className="kpi">
          <span className="eyebrow">first try, whole bank</span>
          <span className="kpi-value sm">
            {p.totals.first_attempts ? `${firstTry(p.totals)}` : 'no data yet'}
          </span>
          <span className="small muted">
            {p.totals.first_attempts && p.totals.low !== null
              ? `${pct(p.totals.first_correct, p.totals.first_attempts)}%, range ${p.totals.low}–${p.totals.high}%`
              : 'answer a question to start the count'}
          </span>
        </div>
        {p.has_blueprint ? (
          <div className="kpi">
            <span className="eyebrow">weighted by exam share</span>
            <span className="kpi-value sm">
              {p.disciplinar.weighted_accuracy !== null ? `${p.disciplinar.weighted_accuracy}%` : 'not yet'}
            </span>
            <span className="small muted">
              {p.disciplinar.weighted_accuracy !== null
                ? `from ${p.areas.reduce((n, a) => n + a.first_attempts, 0)} first attempts, each area weighted by its exam items${
                    p.disciplinar.note ? ` (${p.disciplinar.note})` : ''
                  }`
                : (p.disciplinar.note ?? 'needs first attempts in every area')}
            </span>
          </div>
        ) : null}
      </div>
      {plan ? (
        <div className="plan">
          <p className="plan-line">
            <strong>{plan.sentence}</strong>
          </p>
          {plan.focus ? <p className="small muted">{plan.focus}</p> : null}
          <div className="row">
            <button type="button" className="btn primary sm" onClick={() => onGo('practice')}>
              Practise now
            </button>
            <button type="button" className="btn sm" onClick={() => onGo('mock')}>
              {mocksTaken ? 'Mock exam' : 'Take a first mock'}
            </button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

/* ------------------------------------------------------------------ areas */

function toneClass(t: Tone): string {
  return `vtag ${t}`;
}

const TONE_ICON: Record<Tone, string> = { good: '✓', warn: '▲', bad: '✕', none: '○' };

function VerdictTag({ t }: { t: Pick<Tally, 'first_correct' | 'first_attempts' | 'low' | 'high'> }) {
  const v = tallyVerdict(t);
  return (
    <span className={toneClass(v.tone)}>
      <b aria-hidden="true">{TONE_ICON[v.tone]}</b> {v.label}
    </span>
  );
}

function accuracyText(t: Tally): string {
  if (!t.first_attempts) return 'no data yet';
  return `${firstTry(t)} · ${pct(t.first_correct, t.first_attempts)}% (range ${t.low}–${t.high}%)`;
}

function accuracyAria(t: Tally, what: string): string {
  if (!t.first_attempts) return `${what}: no first attempts yet.`;
  return `${what}: ${t.first_correct} of ${t.first_attempts} correct on the first try, ${pct(
    t.first_correct,
    t.first_attempts,
  )} percent, 95 percent range ${t.low} to ${t.high} percent. Reference lines: ${TARGET} percent target, ${FLOOR} percent floor.`;
}

function AreasChart({
  areas,
  onGo,
}: {
  areas: AreaProgress[];
  onGo: (tab: 'practice' | 'mock', focus?: string) => void;
}) {
  const summary = areas
    .map(
      (a) =>
        `Area ${a.code}: ${Math.round(a.share * 100)}% of the exam, ${a.seen} of ${a.bank} bank questions seen, ${
          a.first_attempts ? `${a.first_correct} of ${a.first_attempts} first try` : 'no first attempts'
        }.`,
    )
    .join(' ');
  return (
    <ChartFrame
      title="By area: exam weight, bank seen, first-try accuracy"
      sub={`Each row on its own 0–100% track. First try is the share of questions you got right the first time you saw them; the whisker is its 95% range, which narrows as you answer more. Reference lines: ${TARGET}% target, ${FLOOR}% floor.`}
      legend={
        <>
          <Key swatch="muted">exam weight</Key>
          <Key swatch="solid">bank seen</Key>
          <Key swatch="dot">first try</Key>
          <Key swatch="range">95% range</Key>
          <Key swatch="target">{TARGET}% target</Key>
          <Key swatch="floor">{FLOOR}% floor</Key>
        </>
      }
      table={
        <table className="compact">
          <thead>
            <tr>
              <th scope="col">Area</th>
              <th scope="col">Exam items</th>
              <th scope="col">Seen</th>
              <th scope="col">Sealed</th>
              <th scope="col">First try</th>
              <th scope="col">95% range</th>
              <th scope="col">Due now</th>
            </tr>
          </thead>
          <tbody>
            {areas.map((a) => (
              <tr key={a.code}>
                <th scope="row">
                  {a.code} {a.title}
                </th>
                <td className="mono">
                  {a.exam_items} ({Math.round(a.share * 100)}%)
                </td>
                <td className="mono">
                  {a.seen} of {a.bank}
                </td>
                <td className="mono">{a.sealed}</td>
                <td className="mono">{a.first_attempts ? firstTry(a) : 'no data yet'}</td>
                <td className="mono">{a.first_attempts ? `${a.low}–${a.high}%` : '—'}</td>
                <td className="mono">{a.due_now}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="areas" role="list" aria-label={summary}>
        {areas.map((a) => {
          const share = Math.round(a.share * 100);
          const seen = seenPct(a);
          return (
            <div className="arearow" role="listitem" key={a.code}>
              <div className="arealabel">
                <span className="areacode">{a.code}</span>
                <span>
                  <strong>{a.title}</strong>
                  <span className="small muted block">
                    {a.exam_items} exam items · {a.due_now} due now
                  </span>
                </span>
              </div>
              <div className="measures">
                <div className="measure axisrow" aria-hidden="true">
                  <span />
                  <StripAxis />
                  <span />
                </div>
                <div className="measure">
                  <span className="mlabel">exam weight</span>
                  <Meter value={share} tone="muted" label={`Area ${a.code} is ${share}% of the exam`} />
                  <span className="mval">
                    {share}% · {a.exam_items} items
                  </span>
                </div>
                <div className="measure">
                  <span className="mlabel">bank seen</span>
                  <Meter
                    value={seen}
                    tone="solid"
                    label={`Area ${a.code}: ${a.seen} of ${a.bank} bank questions seen`}
                  />
                  <span className="mval">
                    {a.seen} of {a.bank} seen{a.sealed ? ` · ${a.sealed} sealed` : ''}
                  </span>
                </div>
                <div className="measure">
                  <span className="mlabel">first try</span>
                  <AccuracyStrip
                    point={pct(a.first_correct, a.first_attempts)}
                    low={a.low}
                    high={a.high}
                    label={accuracyAria(a, `Area ${a.code}`)}
                  />
                  <span className="mval">{accuracyText(a)}</span>
                </div>
              </div>
              <div className="areafoot">
                <VerdictTag t={a} />
                {a.first_attempts > 0 && a.first_attempts < 5 ? (
                  <span className="small muted">few attempts: read the range, not the dot</span>
                ) : null}
                <button type="button" className="linkbtn small" onClick={() => onGo('practice', a.code)}>
                  practise area {a.code}
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ subáreas */

function SubareaGrid({
  goalId,
  areas,
  onGo,
}: {
  goalId: string;
  areas: AreaProgress[];
  onGo: (tab: 'practice' | 'mock', focus?: string) => void;
}) {
  const subs = areas.flatMap((a) => a.subareas);
  const noData = subs.filter((s) => !s.first_attempts).length;
  return (
    <ChartFrame
      title={`The ${subs.length} subáreas`}
      sub={`First-try accuracy per subárea on the same 0–100% track (dot, 95% range, ${FLOOR}/${TARGET} lines). ${noData} of ${subs.length} have no first attempt yet — shown empty, not as 0%.`}
      legend={
        <>
          <Key swatch="dot">first try</Key>
          <Key swatch="range">95% range</Key>
          <Key swatch="target">{TARGET}% target</Key>
          <Key swatch="floor">{FLOOR}% floor</Key>
        </>
      }
      table={<NodeTable nodes={subs} />}
    >
      <div className="stack">
        {areas.map((a) => (
          <div key={a.code} className="stack gap-sm">
            <span className="eyebrow">
              {a.code} {a.title}
            </span>
            <ul className="subgrid">
              {a.subareas.map((s) => (
                <NodeCell key={s.node_id} goalId={goalId} n={s} onPractise={() => onGo('practice', s.ref ?? s.node_id)} />
              ))}
            </ul>
          </div>
        ))}
      </div>
    </ChartFrame>
  );
}

function NodeGrid({ goalId, title, sub, nodes }: { goalId: string; title: string; sub: string; nodes: NodeProgress[] }) {
  return (
    <ChartFrame title={title} sub={sub} table={<NodeTable nodes={nodes} />}>
      <ul className="subgrid">
        {nodes.map((n) => (
          <NodeCell key={n.node_id} goalId={goalId} n={n} />
        ))}
      </ul>
    </ChartFrame>
  );
}

function NodeCell({ goalId, n, onPractise }: { goalId: string; n: NodeProgress; onPractise?: () => void }) {
  const none = !n.first_attempts;
  const name = `${n.ref ? `${n.ref} ` : ''}${n.title}`;
  return (
    <li className={`subcell${none ? ' nodata' : ''}`}>
      <div className="subtop">
        {n.ref ? <span className="subref mono">{n.ref}</span> : null}
        <Link
          className="subtitle"
          href={`/goal/map/?g=${encodeURIComponent(goalId)}&node=${encodeURIComponent(n.node_id)}`}
          title={`${n.title} - open its receipts on the map`}
        >
          {n.title}
        </Link>
      </div>
      <AccuracyStrip
        compact
        point={pct(n.first_correct, n.first_attempts)}
        low={n.low}
        high={n.high}
        label={accuracyAria(n, name)}
      />
      <p className="small subline">
        {none ? (
          <span className="muted">
            no data yet · {n.seen} of {n.bank} seen
          </span>
        ) : (
          <>
            <strong>{firstTry(n)}</strong>{' '}
            <span className="muted">
              · {n.low}–{n.high}%
            </span>
          </>
        )}
      </p>
      <div className="row subfoot">
        {n.exam_items !== null ? <span className="small muted mono">{n.exam_items} items</span> : null}
        <StatePill state={n.state} />
        {onPractise ? (
          <button type="button" className="linkbtn small" onClick={onPractise} aria-label={`Practise ${name}`}>
            practise
          </button>
        ) : null}
      </div>
    </li>
  );
}

function NodeTable({ nodes }: { nodes: NodeProgress[] }) {
  return (
    <table className="compact">
      <thead>
        <tr>
          <th scope="col">Concept</th>
          <th scope="col">Exam items</th>
          <th scope="col">Seen</th>
          <th scope="col">First try</th>
          <th scope="col">95% range</th>
          <th scope="col">State</th>
        </tr>
      </thead>
      <tbody>
        {nodes.map((n) => (
          <tr key={n.node_id}>
            <th scope="row">
              {n.ref ? `${n.ref} ` : ''}
              {n.title}
            </th>
            <td className="mono">{n.exam_items ?? '—'}</td>
            <td className="mono">
              {n.seen} of {n.bank}
            </td>
            <td className="mono">{n.first_attempts ? firstTry(n) : 'no data yet'}</td>
            <td className="mono">{n.first_attempts ? `${n.low}–${n.high}%` : '—'}</td>
            <td>{n.state}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/* ------------------------------------------------------------------ time */

function ActivityChart({ p }: { p: Progress }) {
  const days = p.activity;
  const totalAnswers = days.reduce((a, d) => a + d.answers, 0);
  const totalCorrect = days.reduce((a, d) => a + d.correct, 0);
  const totalCards = days.reduce((a, d) => a + d.cards, 0);
  const active = days.filter((d) => d.answers || d.cards).length;
  const answerCols: Column[] = days.map((d) => ({
    key: d.date,
    x: dayLabel(d.date, false),
    parts: [d.correct, d.answers - d.correct],
    tip: (
      <>
        <strong>{d.answers} answers</strong>
        <span>{d.correct} correct</span>
        <span className="muted">{dayLabel(d.date)}</span>
      </>
    ),
    text: `${dayLabel(d.date)}: ${d.answers} answers, ${d.correct} correct.`,
  }));
  const cardCols: Column[] = days.map((d) => ({
    key: d.date,
    x: dayLabel(d.date, false),
    parts: [d.cards],
    tip: (
      <>
        <strong>{d.cards} cards</strong>
        <span className="muted">{dayLabel(d.date)}</span>
      </>
    ),
    text: `${dayLabel(d.date)}: ${d.cards} card reviews.`,
  }));
  const yMax = Math.max(1, ...days.map((d) => Math.max(d.answers, d.cards)));
  return (
    <ChartFrame
      title="Last 28 days"
      sub={`${totalAnswers} answers (${totalCorrect} correct) and ${totalCards} card reviews on ${active} of 28 days. Card flips are self-rated and never count as evidence.`}
      legend={
        <>
          <Key swatch="solid">correct</Key>
          <Key swatch="soft">not correct</Key>
          <Key swatch="muted">card reviews</Key>
        </>
      }
      table={
        <table className="compact">
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Answers</th>
              <th scope="col">Correct</th>
              <th scope="col">Cards</th>
            </tr>
          </thead>
          <tbody>
            {days.map((d) => (
              <tr key={d.date}>
                <th scope="row">{dayLabel(d.date)}</th>
                <td className="mono">{d.answers}</td>
                <td className="mono">{d.correct}</td>
                <td className="mono">{d.cards}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="stack gap-sm">
        <span className="eyebrow">answers per day</span>
        <ColumnChart
          cols={answerCols}
          seriesClass={['solid', 'soft']}
          height={150}
          yMax={yMax}
          xEvery={7}
          markers={[{ index: days.length - 1, label: 'today', kind: 'today' }]}
          ariaLabel={`Answers per day over the last 28 days: ${totalAnswers} in total, ${totalCorrect} correct.`}
        />
        <span className="eyebrow">card reviews per day</span>
        <ColumnChart
          cols={cardCols}
          seriesClass={['muted']}
          height={96}
          yMax={yMax}
          xEvery={7}
          ariaLabel={`Card reviews per day over the last 28 days: ${totalCards} in total.`}
        />
      </div>
    </ChartFrame>
  );
}

function ForecastChart({ p }: { p: Progress }) {
  const f = p.forecast;
  const total = f.reduce((a, d) => a + d.due, 0);
  const examIdx = p.deadline ? f.findIndex((d) => d.date === p.deadline) : -1;
  const peak = f.reduce((best, d) => (d.due > best.due ? d : best), f[0] ?? { date: '', due: 0 });
  const cols: Column[] = f.map((d, i) => ({
    key: d.date,
    x: dayLabel(d.date, false),
    parts: [d.due],
    tip: (
      <>
        <strong>{d.due} due</strong>
        <span className="muted">
          {dayLabel(d.date)}
          {i === 0 ? ' (today, overdue included)' : ''}
        </span>
      </>
    ),
    text: `${dayLabel(d.date)}: ${d.due} coming due.`,
  }));
  const markers: ColumnMarker[] = [{ index: 0, label: 'today', kind: 'today' }];
  if (examIdx >= 0) markers.push({ index: examIdx, label: `exam ${dayLabel(p.deadline!, false)}`, kind: 'exam' });
  return (
    <ChartFrame
      title={p.deadline ? 'Reviews coming due until the exam' : 'Reviews coming due'}
      sub={
        f.length
          ? `${total} question and card reviews scheduled over ${f.length} days${
              peak && peak.due ? `; the busiest day is ${dayLabel(peak.date)} with ${peak.due}` : ''
            }. New questions are not in this count.`
          : 'Nothing scheduled.'
      }
      legend={p.deadline ? <Key swatch="exam">exam day</Key> : null}
      table={
        <table className="compact">
          <thead>
            <tr>
              <th scope="col">Day</th>
              <th scope="col">Due</th>
            </tr>
          </thead>
          <tbody>
            {f.map((d) => (
              <tr key={d.date}>
                <th scope="row">
                  {dayLabel(d.date)}
                  {d.date === p.deadline ? ' (exam)' : ''}
                </th>
                <td className="mono">{d.due}</td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      {f.length ? (
        <ColumnChart
          cols={cols}
          seriesClass={['solid']}
          height={170}
          xEvery={7}
          markers={markers}
          ariaLabel={`Reviews coming due per day from today${p.deadline ? ` to the exam on ${dayLabel(p.deadline)}` : ''}: ${total} in total.`}
        />
      ) : (
        <p className="small muted">No reviews scheduled yet.</p>
      )}
    </ChartFrame>
  );
}

/* ------------------------------------------------------------------ mocks */

function MockHistory({ p, onGo }: { p: Progress; onGo: (tab: 'practice' | 'mock') => void }) {
  const done = p.mocks
    .filter((m) => m.submitted_at && m.correct !== null)
    .sort((a, b) => a.started_at.localeCompare(b.started_at));
  if (!done.length) {
    return (
      <ChartFrame title="Mock exams" sub="Score per mock, overall and by area.">
        <div className="empty">
          <p>No mock exam yet.</p>
          <p className="small muted">
            A sealed mock is the only unbiased read on exam day: questions you have never practised,
            no feedback until you submit, timed at the exam&apos;s pace.
          </p>
          <div>
            <button type="button" className="btn sm" onClick={() => onGo('mock')}>
              Go to mock exams
            </button>
          </div>
        </div>
      </ChartFrame>
    );
  }
  const areaCodes = p.areas.length
    ? p.areas.map((a) => ({ code: a.code, title: a.title }))
    : Array.from(new Set(done.flatMap((m) => (m.areas ?? []).map((a) => a.code)))).map((code) => ({ code, title: '' }));
  const panels = [
    { code: null as string | null, title: 'Overall' },
    ...areaCodes.map((a) => ({ code: a.code, title: `Area ${a.code}` })),
  ];
  const label = (i: number) => `#${i + 1}`;
  return (
    <ChartFrame
      title="Mock exams"
      sub={`Score per mock (oldest first), overall and by area, on the same 0–100% scale with the ${TARGET}% target and ${FLOOR}% floor lines. Counts over each column.`}
      legend={
        <>
          <Key swatch="target">{TARGET}% target</Key>
          <Key swatch="floor">{FLOOR}% floor</Key>
        </>
      }
      table={
        <table className="compact">
          <thead>
            <tr>
              <th scope="col">Mock</th>
              <th scope="col">Date</th>
              <th scope="col">Overall</th>
              {areaCodes.map((a) => (
                <th scope="col" key={a.code}>
                  Area {a.code}
                </th>
              ))}
              <th scope="col">Time</th>
            </tr>
          </thead>
          <tbody>
            {done.map((m, i) => (
              <tr key={m.session_id}>
                <th scope="row">{label(i)}</th>
                <td className="mono">{dayLabel(m.started_at.slice(0, 10))}</td>
                <td className="mono">
                  {m.correct} of {m.n} ({pct(m.correct ?? 0, m.n)}%)
                </td>
                {areaCodes.map((a) => {
                  const s = m.areas?.find((x) => x.code === a.code);
                  return (
                    <td className="mono" key={a.code}>
                      {s ? `${s.correct} of ${s.n}` : '—'}
                    </td>
                  );
                })}
                <td className="mono">
                  {m.minutes_used ?? '?'} of {m.minutes} min
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      }
    >
      <div className="multiples">
        {panels.map((panel) => {
          const cols: Column[] = done.map((m, i) => {
            const s = panel.code === null ? { n: m.n, correct: m.correct ?? 0 } : m.areas?.find((x) => x.code === panel.code);
            const n = s?.n ?? 0;
            const k = s?.correct ?? 0;
            const v = pct(k, n);
            return {
              key: m.session_id,
              x: label(i),
              parts: [v ?? 0],
              top: n ? `${k}/${n}` : '—',
              tip: (
                <>
                  <strong>{n ? `${k} of ${n} · ${v}%` : 'not in this mock'}</strong>
                  <span className="muted">
                    {label(i)} · {dayLabel(m.started_at.slice(0, 10))}
                  </span>
                </>
              ),
              text: `${panel.title}, mock ${i + 1}: ${n ? `${k} of ${n} correct, ${v} percent` : 'no questions from this area'}.`,
            };
          });
          return (
            <div key={panel.title} className="multiple">
              <span className="eyebrow">{panel.title}</span>
              <ColumnChart
                cols={cols}
                seriesClass={['solid']}
                height={150}
                yMax={100}
                percent
                refLines={[
                  { value: TARGET, label: `${TARGET} target`, kind: 'target' },
                  { value: FLOOR, label: `${FLOOR} floor`, kind: 'floor' },
                ]}
                ariaLabel={`${panel.title}: score per mock exam, ${done.length} mock${done.length === 1 ? '' : 's'}.`}
              />
            </div>
          );
        })}
      </div>
    </ChartFrame>
  );
}
