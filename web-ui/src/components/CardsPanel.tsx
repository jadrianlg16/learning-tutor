'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { allowShortcut, kb, when } from '@/lib/study';
import type {
  CardCounts,
  CardRating,
  CardRevealResponse,
  CardReviewResponse,
  StudyCard,
} from '@/lib/types';
import { CARD_RATINGS } from '@/lib/types';
import { ErrorNote, Loading } from './Bits';
import { Markdown } from './Markdown';

const RATING_LABEL: Record<CardRating, string> = {
  again: 'Again',
  hard: 'Hard',
  good: 'Good',
  easy: 'Easy',
};

/**
 * Flashcards. The page gets the front only; the back is a separate `reveal` call, and the
 * rating is self-report - it schedules the next review (FSRS) and is never evidence, which
 * the screen says in plain words rather than leaving to a tooltip.
 */
export function CardsPanel({ goalId, onChange }: { goalId: string; onChange: () => void }) {
  const [card, setCard] = useState<StudyCard | null>(null);
  const [counts, setCounts] = useState<CardCounts | null>(null);
  const [done, setDone] = useState(false);
  const [back, setBack] = useState<CardRevealResponse | null>(null);
  const [last, setLast] = useState<CardReviewResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [exported, setExported] = useState<string | null>(null);
  const backRef = useRef<HTMLDivElement | null>(null);

  const next = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await api.cardsNext(goalId, 1);
      setCard(r.cards[0] ?? null);
      setCounts(r.counts);
      setDone(r.done || !r.cards.length);
      setBack(null);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setLoading(false);
    }
  }, [goalId]);

  useEffect(() => {
    void next();
  }, [next]);

  const reveal = useCallback(async () => {
    if (!card || back || busy) return;
    setBusy(true);
    setError(null);
    try {
      setBack(await api.cardReveal(goalId, card.item_id));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }, [card, back, busy, goalId]);

  // The back is read next, so that is where focus goes (it is not a control: no Space trap).
  useEffect(() => {
    if (back) backRef.current?.focus();
  }, [back]);

  const rate = useCallback(
    async (rating: CardRating) => {
      if (!card || !back || busy) return;
      setBusy(true);
      setError(null);
      try {
        setLast(await api.cardReview(goalId, card.item_id, rating));
        onChange();
        await next();
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setBusy(false);
      }
    },
    [card, back, busy, goalId, onChange, next],
  );

  // Space shows the answer; 1-4 rate it.
  useEffect(() => {
    const onKey = (ev: KeyboardEvent) => {
      if (!card || loading || busy || !allowShortcut(ev)) return;
      if (ev.key === ' ' && !back) {
        ev.preventDefault();
        void reveal();
        return;
      }
      if (back && /^[1-4]$/.test(ev.key)) {
        ev.preventDefault();
        void rate(CARD_RATINGS[Number(ev.key) - 1]);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [card, loading, busy, back, reveal, rate]);

  const exportUrl = api.cardsExportUrl(goalId, 'tsv', 'cards');

  // Fetched rather than navigated, so a failure shows here instead of as a raw error page.
  // The href stays real for "copy link" and middle-click.
  const download = async (e: React.MouseEvent<HTMLAnchorElement>) => {
    e.preventDefault();
    setError(null);
    try {
      const { blob, filename } = await api.downloadCardsExport(goalId, 'tsv', 'cards');
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setExported(`${filename} (${kb(blob.size)})`);
    } catch (err) {
      setError(errorMessage(err));
    }
  };

  return (
    <div className="stack gap-lg">
      <div className="stack gap-sm">
        <p className="note small">
          <strong>Self-rated:</strong> this schedules your reviews and never counts as proof.
          Mastery only moves on a checked question you answer yourself.
        </p>
        {counts ? (
          <p className="small mono muted" aria-live="polite">
            due now {counts.due_now} &middot; new today {counts.new_today} of {counts.new_limit}
            {counts.new_available ? ` · ${counts.new_available} new waiting` : ''} &middot;{' '}
            {counts.total} in the deck
          </p>
        ) : null}
      </div>

      <ErrorNote message={error} />
      {loading && !card ? <Loading what="the next card" /> : null}

      {last ? (
        <p className="small muted mono" role="status">
          last card: {last.rating} &middot; next review {when(last.schedule.due)} ({last.schedule.state})
        </p>
      ) : null}

      {!loading && done ? (
        <div className="card">
          <span className="eyebrow">cards</span>
          <h2>No cards due</h2>
          <p className="small muted" style={{ maxWidth: '64ch' }}>
            Every card you have seen is scheduled for later
            {counts && counts.new_available && counts.new_today >= counts.new_limit
              ? `, and today's ${counts.new_limit} new cards are used up`
              : counts && !counts.new_available
                ? ', and there are no new cards left'
                : ''}
            . Nothing to do here until one falls due.
          </p>
        </div>
      ) : null}

      {card && !done ? (
        <section className="qcard" aria-label="Flashcard">
          <div className="qhead">
            <span className="eyebrow">card &middot; {card.node_title}</span>
            <span className="pill">{card.reason === 'due' ? 'due review' : 'new'}</span>
          </div>

          <div className="qstem">
            <Markdown className="prose">{card.front}</Markdown>
          </div>

          {back ? (
            <div className="flashback" ref={backRef} tabIndex={-1} aria-label="Answer">
              <span className="eyebrow">answer</span>
              <Markdown className="prose">{back.back}</Markdown>
              {back.source ? <p className="small muted mono">from {back.source}</p> : null}
            </div>
          ) : null}

          {!back ? (
            <div className="row between">
              <span className="small muted mono">Space shows the answer</span>
              <button type="button" className="btn primary" disabled={busy} onClick={reveal}>
                {busy ? 'revealing...' : 'Show answer'}
              </button>
            </div>
          ) : (
            <div className="stack gap-sm">
              <span className="eyebrow" id="card-rate">
                How well did you recall it? &middot; keys 1-4 &middot; self-rated
              </span>
              <div className="ratings" role="group" aria-labelledby="card-rate">
                {CARD_RATINGS.map((r, i) => (
                  <button
                    key={r}
                    type="button"
                    className={`btn rate-${r}`}
                    disabled={busy}
                    onClick={() => void rate(r)}
                  >
                    <span className="kbd" aria-hidden="true">
                      {i + 1}
                    </span>
                    {RATING_LABEL[r]}
                  </button>
                ))}
              </div>
            </div>
          )}
        </section>
      ) : null}

      <div className="row">
        <a className="btn sm" href={exportUrl} download onClick={download}>
          Export for Anki (TSV)
        </a>
        <span className="small muted">
          {exported
            ? `Downloaded ${exported}.`
            : 'Front, back and tags, one card per line. Anki: File → Import.'}
        </span>
      </div>
    </div>
  );
}
