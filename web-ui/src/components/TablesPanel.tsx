'use client';

import { useEffect, useMemo, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { shuffled } from '@/lib/study';
import type { ImportCount, StudyTable, TableSummary } from '@/lib/types';
import { ErrorNote, Loading } from './Bits';
import { Markdown } from './Markdown';

/**
 * Comparison and definition tables: read them, or hide a column and fill it back in from a
 * shuffled list of that column's own values. Fill-in is practice and is not recorded - the
 * contract says so and so does the screen. "Make flashcards" turns the rows into cards,
 * which is where spaced review of a table actually happens.
 */
export function TablesPanel({ goalId, onChange }: { goalId: string; onChange: () => void }) {
  const [tables, setTables] = useState<TableSummary[] | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [table, setTable] = useState<StudyTable | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .listTables(goalId)
      .then((r) => {
        setTables(r.tables);
        setSelected((s) => s ?? r.tables[0]?.table_id ?? null);
      })
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  useEffect(() => {
    if (!selected) return;
    let live = true;
    setTable(null);
    api
      .getTable(goalId, selected)
      .then((t) => live && setTable(t))
      .catch((e) => live && setError(errorMessage(e)));
    return () => {
      live = false;
    };
  }, [goalId, selected]);

  if (error && !tables) return <ErrorNote message={error} />;
  if (!tables) return <Loading what="the tables" />;
  if (!tables.length) {
    return (
      <p className="small muted" style={{ maxWidth: '64ch' }}>
        No tables for this goal yet. Import a markdown file with GFM pipe tables (Import tab), or
        have the harness write one with <code>learner table save</code>.
      </p>
    );
  }

  return (
    <div className="stack gap-lg">
      <ul className="tablelist" aria-label="Tables">
        {tables.map((t) => (
          <li key={t.table_id}>
            <button
              type="button"
              className={`tablepick${selected === t.table_id ? ' on' : ''}`}
              aria-pressed={selected === t.table_id}
              onClick={() => setSelected(t.table_id)}
            >
              <strong>{t.title}</strong>
              <span className="small muted">
                {t.node_title ?? 'no concept'} &middot; {t.columns.join(' / ')} &middot;{' '}
                {t.row_count} row{t.row_count === 1 ? '' : 's'}
                {t.source ? ` · from ${t.source}` : ''}
              </span>
            </button>
          </li>
        ))}
      </ul>

      <ErrorNote message={error} />
      {selected && !table ? <Loading what="the table" /> : null}
      {table ? (
        <TableView key={table.table_id} goalId={goalId} table={table} onChange={onChange} />
      ) : null}
    </div>
  );
}

function TableView({
  goalId,
  table,
  onChange,
}: {
  goalId: string;
  table: StudyTable;
  onChange: () => void;
}) {
  /** Index of the hidden column; null = read the whole table. Column 0 labels the rows. */
  const [hide, setHide] = useState<number | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [checked, setChecked] = useState(false);
  const [seed, setSeed] = useState(() => Math.floor(Math.random() * 2 ** 31));
  const [cards, setCards] = useState<ImportCount | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const choices = useMemo(() => {
    if (hide === null) return [];
    const values = Array.from(new Set(table.rows.map((r) => r[hide] ?? '')));
    return shuffled(values, seed);
  }, [table, hide, seed]);

  const score = useMemo(() => {
    if (hide === null) return 0;
    return table.rows.filter((r, i) => (answers[i] ?? '') === (r[hide] ?? '')).length;
  }, [table, hide, answers]);

  const reset = () => {
    setAnswers({});
    setChecked(false);
    setSeed((s) => s + 1);
  };

  const makeCards = async () => {
    setBusy(true);
    setError(null);
    try {
      setCards(await api.tableCards(goalId, table.table_id));
      onChange();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const colName = hide === null ? '' : table.columns[hide];

  return (
    <section className="card" aria-label={table.title}>
      <div className="row between">
        <div className="stack gap-sm">
          <span className="eyebrow">
            table &middot; {table.node_title ?? 'no concept'} &middot; by {table.author}
          </span>
          <h2>{table.title}</h2>
        </div>
        {table.columns.length > 1 ? (
          <label className="field fillpick">
            <span className="lbl">fill-in: hide a column</span>
            <select
              value={hide ?? ''}
              onChange={(e) => {
                setHide(e.target.value === '' ? null : Number(e.target.value));
                setAnswers({});
                setChecked(false);
              }}
            >
              <option value="">off - read the whole table</option>
              {table.columns.slice(1).map((c, i) => (
                <option key={c} value={i + 1}>
                  {c}
                </option>
              ))}
            </select>
          </label>
        ) : null}
      </div>

      {hide !== null ? (
        <p className="note warn small">
          Fill in <strong>{colName}</strong> from the list. Practice only &mdash; not recorded.
        </p>
      ) : null}

      <div className="table-wrap">
        <table className="studytable">
          <thead>
            <tr>
              {table.columns.map((c, i) => (
                <th key={i} scope="col">
                  {c}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {table.rows.map((row, r) => (
              <tr key={r}>
                {table.columns.map((_, c) => {
                  const cell = row[c] ?? '';
                  if (c === 0) {
                    return (
                      <th key={c} scope="row">
                        <Markdown inline>{cell}</Markdown>
                      </th>
                    );
                  }
                  if (c !== hide) {
                    return (
                      <td key={c}>
                        <Markdown inline>{cell}</Markdown>
                      </td>
                    );
                  }
                  const ok = (answers[r] ?? '') === cell;
                  return (
                    <td key={c} className={`fill${checked ? (ok ? ' right' : ' wrong') : ''}`}>
                      <select
                        aria-label={`${row[0] ?? `row ${r + 1}`}: ${colName}`}
                        value={answers[r] ?? ''}
                        onChange={(e) => {
                          setAnswers((a) => ({ ...a, [r]: e.target.value }));
                          setChecked(false);
                        }}
                      >
                        <option value="">choose...</option>
                        {choices.map((v) => (
                          <option key={v} value={v}>
                            {v}
                          </option>
                        ))}
                      </select>
                      {checked ? (
                        <span className={`verdict ${ok ? 'right' : 'wrong'}`}>
                          {ok ? 'right' : <>wrong &mdash; it is {cell}</>}
                        </span>
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {hide !== null ? (
        <div className="row">
          <button type="button" className="btn primary sm" onClick={() => setChecked(true)}>
            Check
          </button>
          <button type="button" className="btn sm" onClick={reset}>
            Reset
          </button>
          {checked ? (
            <span className="small mono" role="status">
              {score} of {table.rows.length} right &middot; practice only, not recorded
            </span>
          ) : null}
        </div>
      ) : null}

      <ErrorNote message={error} />
      <div className="row">
        <button type="button" className="btn sm" disabled={busy} onClick={makeCards}>
          {busy ? 'making cards...' : 'Make flashcards from this table'}
        </button>
        {cards ? (
          <span className="small mono" role="status">
            {cards.parsed} rows &middot; {cards.imported} new card{cards.imported === 1 ? '' : 's'}{' '}
            &middot; {cards.skipped_existing} already in the deck
          </span>
        ) : (
          <span className="small muted">
            {table.source ? `From ${table.source}. ` : ''}The rows become cards in the Cards tab;
            re-running skips the ones already there.
          </span>
        )}
      </div>
    </section>
  );
}
