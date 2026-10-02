'use client';

import Link from 'next/link';
import { useSearchParams } from 'next/navigation';
import { Suspense, useCallback, useEffect, useState } from 'react';
import { ApiError, api, errorMessage } from '@/lib/api';
import type { Blueprint, Goal, StudyResponse } from '@/lib/types';
import { ErrorNote, Loading } from '@/components/Bits';
import { CardsPanel } from '@/components/CardsPanel';
import { ImportPanel } from '@/components/ImportPanel';
import { MockExamPanel } from '@/components/MockExamPanel';
import { PracticePanel } from '@/components/PracticePanel';
import { ProgressPanel } from '@/components/ProgressPanel';
import { TablesPanel } from '@/components/TablesPanel';

type Tab = 'progress' | 'practice' | 'mock' | 'cards' | 'tables' | 'import';

const TABS: { id: Tab; label: string }[] = [
  { id: 'progress', label: 'Progress' },
  { id: 'practice', label: 'Practice' },
  { id: 'mock', label: 'Mock exam' },
  { id: 'cards', label: 'Cards' },
  { id: 'tables', label: 'Tables' },
  { id: 'import', label: 'Import' },
];

function isTab(v: string | null): v is Tab {
  return TABS.some((t) => t.id === v);
}

/**
 * Study tools: progress toward the exam, a question bank, sealed mock exams, flashcards and
 * tables, all run without a model. The tab you were on is remembered per goal in
 * localStorage (`lt-study-tab:<goal>`), read after mount like every other panel state,
 * because the export is prerendered. With nothing stored, a goal with an exam blueprint
 * opens on Progress and any other goal on Practice.
 */
function StudyScreen() {
  const params = useSearchParams();
  const goalId = params.get('g');

  const [goal, setGoal] = useState<Goal | null>(null);
  const [study, setStudy] = useState<StudyResponse | null>(null);
  const [blueprint, setBlueprint] = useState<Blueprint | null>(null);
  const [blueprintFailed, setBlueprintFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [stored, setStored] = useState<Tab | null | undefined>(undefined);
  const [tab, setTab] = useState<Tab | null>(null);
  const [focusHandoff, setFocusHandoff] = useState<string | null>(null);

  useEffect(() => {
    if (!goalId) return;
    try {
      const v = window.localStorage.getItem(`lt-study-tab:${goalId}`);
      setStored(isTab(v) ? v : null);
    } catch {
      setStored(null); /* storage disabled: fall back to the default tab */
    }
  }, [goalId]);

  // The default waits for both the stored tab and GET /study (it needs `blueprint`).
  useEffect(() => {
    if (tab !== null || stored === undefined) return;
    if (stored) setTab(stored);
    else if (study) setTab(study.blueprint ? 'progress' : 'practice');
  }, [tab, stored, study]);

  const choose = useCallback(
    (t: Tab) => {
      setTab(t);
      if (t !== 'practice') setFocusHandoff(null);
      try {
        window.localStorage.setItem(`lt-study-tab:${goalId}`, t);
      } catch {
        /* storage disabled: the tab still switches for this page view */
      }
    },
    [goalId],
  );

  const loadStudy = useCallback(() => {
    if (!goalId) return;
    api
      .study(goalId)
      .then(setStudy)
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  useEffect(() => {
    if (!goalId) return;
    api
      .getGoal(goalId)
      .then((d) => setGoal(d.goal))
      .catch((e) => setError(errorMessage(e)));
    loadStudy();
  }, [goalId, loadStudy]);

  // The blueprint only when GET /study says there is one (a 404 is "no blueprint", not an error).
  const hasBlueprint = !!study?.blueprint;
  useEffect(() => {
    if (!goalId || !hasBlueprint) {
      setBlueprint(null);
      return;
    }
    api
      .blueprint(goalId)
      .then(setBlueprint)
      .catch((e) => {
        setBlueprintFailed(true); // practice then runs unfocused rather than waiting forever
        if (!(e instanceof ApiError && e.status === 404)) setError(errorMessage(e));
      });
  }, [goalId, hasBlueprint]);
  const blueprintReady = study !== null && (!study.blueprint || blueprint !== null || blueprintFailed);

  const go = useCallback(
    (t: 'practice' | 'mock', focus?: string) => {
      if (t === 'practice' && focus !== undefined) setFocusHandoff(focus);
      choose(t);
      window.scrollTo({ top: 0 });
    },
    [choose],
  );

  // Arrow keys move along the tab strip (the WAI-ARIA tabs pattern); Tab leaves it.
  const onTabKey = (ev: React.KeyboardEvent<HTMLDivElement>) => {
    const i = TABS.findIndex((t) => t.id === tab);
    const to =
      ev.key === 'ArrowRight'
        ? (i + 1) % TABS.length
        : ev.key === 'ArrowLeft'
          ? (i - 1 + TABS.length) % TABS.length
          : ev.key === 'Home'
            ? 0
            : ev.key === 'End'
              ? TABS.length - 1
              : -1;
    if (to < 0) return;
    ev.preventDefault();
    choose(TABS[to].id);
    document.getElementById(`study-tab-${TABS[to].id}`)?.focus();
  };

  if (!goalId) {
    return (
      <main className="main" id="main">
        <p className="note warn">
          No goal selected. <Link href="/">Pick one.</Link>
        </p>
      </main>
    );
  }

  const due: Partial<Record<Tab, number>> = study
    ? { practice: study.bank.due_now, cards: study.cards.due_now }
    : {};
  const openMock = study?.mock?.open ?? null;

  return (
    <main className="main" id="main">
      <div className="stack gap-sm">
        <span className="eyebrow">study tools &middot; no model involved</span>
        <h1>{goal?.title ?? goalId}</h1>
        <p className="muted small" style={{ maxWidth: '68ch' }}>
          A question bank, sealed mock exams, flashcards and tables, graded or scheduled by code.
          Only a question that passed an independent blind check counts toward mastery; cards are
          self-rated and never do.
        </p>
        {study ? (
          <div className="row small mono muted" aria-label="What is in this goal's study set">
            <span className="pill">{study.bank.total} questions</span>
            <span className="pill known">{study.bank.checked} checked</span>
            <span className="pill fragile">{study.bank.unchecked} unchecked</span>
            {study.bank.rejected ? (
              <span className="pill misconception">{study.bank.rejected} disputed by the checker</span>
            ) : null}
            {study.bank.sealed ? (
              <span className="pill accent" title="Sealed for mock exams: never served by practice until a mock uses them">
                {study.bank.sealed} sealed for mocks
              </span>
            ) : null}
            <span className="pill">
              {study.cards.total} cards &middot; {study.cards.due_now} due
            </span>
            <span className="pill">
              {study.tables.length} table{study.tables.length === 1 ? '' : 's'}
            </span>
          </div>
        ) : null}
        <div className="row">
          <Link className="btn sm" href={`/goal/?g=${encodeURIComponent(goalId)}`}>
            Back to the session
          </Link>
        </div>
      </div>

      <ErrorNote message={error} />

      {openMock && tab !== 'mock' ? (
        <div className="banner" role="status">
          <span>
            <strong>A mock exam is open.</strong> Its clock keeps running while you are elsewhere.
          </span>
          <button type="button" className="btn primary sm" onClick={() => choose('mock')}>
            Resume the mock
          </button>
        </div>
      ) : null}

      <div className="tabs" role="tablist" aria-label="Study tools" onKeyDown={onTabKey}>
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            role="tab"
            id={`study-tab-${t.id}`}
            aria-selected={tab === t.id}
            aria-controls={`study-panel-${t.id}`}
            tabIndex={tab === t.id || (tab === null && t.id === 'progress') ? 0 : -1}
            className="tab"
            onClick={() => choose(t.id)}
          >
            {t.label}
            {due[t.id] ? (
              <span className="tabcount">
                {due[t.id]}
                <span className="sr-only"> due</span>
              </span>
            ) : null}
            {t.id === 'mock' && openMock ? (
              <span className="tabcount live">
                open<span className="sr-only"> mock in progress</span>
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {tab === null ? (
        <Loading what="the study tools" />
      ) : (
        <div
          role="tabpanel"
          id={`study-panel-${tab}`}
          aria-labelledby={`study-tab-${tab}`}
          className="tabpanel"
        >
          {tab === 'progress' ? (
            <ProgressPanel goalId={goalId} study={study} onGo={go} />
          ) : tab === 'practice' ? (
            <PracticePanel
              goalId={goalId}
              rejected={study?.bank.rejected ?? 0}
              blueprint={blueprint}
              blueprintReady={blueprintReady}
              initialFocus={focusHandoff}
              onChange={loadStudy}
            />
          ) : tab === 'mock' ? (
            <MockExamPanel goalId={goalId} onChange={loadStudy} />
          ) : tab === 'cards' ? (
            <CardsPanel goalId={goalId} onChange={loadStudy} />
          ) : tab === 'tables' ? (
            <TablesPanel goalId={goalId} onChange={loadStudy} />
          ) : study ? (
            <ImportPanel goalId={goalId} importable={study.importable} onChange={loadStudy} />
          ) : (
            <Loading what="the sources folder" />
          )}
        </div>
      )}
    </main>
  );
}

export default function StudyPage() {
  return (
    <Suspense
      fallback={
        <main className="main" id="main">
          <Loading what="the study tools" />
        </main>
      }
    >
      <StudyScreen />
    </Suspense>
  );
}
