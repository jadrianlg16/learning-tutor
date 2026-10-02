'use client';

import { useState, type ReactNode } from 'react';
import { api, errorMessage } from '@/lib/api';
import { kb } from '@/lib/study';
import type { ImportableFile, ImportKind, ImportReport } from '@/lib/types';
import { ErrorNote } from './Bits';

const KINDS: { id: ImportKind; label: string; hint: ReactNode }[] = [
  {
    id: 'questions',
    label: 'questions',
    hint: (
      <>
        <code>N. stem [tag]</code> with <code>A) ...</code> options; keys from{' '}
        <code>| N | tag | LETTER | explanation |</code>
      </>
    ),
  },
  {
    id: 'cards',
    label: 'flashcards',
    hint: (
      <>
        paragraphs that open with a bold term: <code>**Term.** definition</code>
      </>
    ),
  },
  { id: 'tables', label: 'tables', hint: 'GFM pipe tables, titled by the nearest heading' },
];

/**
 * Markdown in, bank/deck/tables out, with no model anywhere: the importer reads the format
 * spelled out in CONTRACTS.md. "Preview" is a dry run - the same report, nothing written -
 * so a file can be checked before it lands. Imported questions start unchecked.
 */
export function ImportPanel({
  goalId,
  importable,
  onChange,
}: {
  goalId: string;
  importable: ImportableFile[];
  onChange: () => void;
}) {
  const [mode, setMode] = useState<'folder' | 'upload'>(importable.length ? 'folder' : 'upload');
  const [path, setPath] = useState(importable[0]?.path ?? '');
  const [keyPath, setKeyPath] = useState('');
  const [file, setFile] = useState<File | null>(null);
  const [keyFile, setKeyFile] = useState<File | null>(null);
  const [what, setWhat] = useState<Set<ImportKind>>(new Set(KINDS.map((k) => k.id)));
  const [busy, setBusy] = useState<'preview' | 'import' | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const kinds = KINDS.map((k) => k.id).filter((k) => what.has(k));
  const ready = kinds.length > 0 && (mode === 'folder' ? !!path : !!file);

  const toggle = (k: ImportKind) =>
    setWhat((s) => {
      const n = new Set(s);
      if (n.has(k)) n.delete(k);
      else n.add(k);
      return n;
    });

  const run = async (dryRun: boolean) => {
    if (!ready) return;
    setBusy(dryRun ? 'preview' : 'import');
    setError(null);
    try {
      const res =
        mode === 'folder'
          ? await api.importStudyPath(goalId, {
              path,
              key_path: keyPath || undefined,
              what: kinds,
              dry_run: dryRun,
            })
          : await api.importStudyUpload(goalId, {
              file: file as File,
              keyFile,
              what: kinds,
              dryRun,
            });
      setReport(res);
      if (!dryRun) onChange();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="stack gap-lg">
      <p className="small muted" style={{ maxWidth: '68ch' }}>
        Pull questions, flashcards and tables out of markdown you already have. Nothing here calls
        a model. Imported questions start <strong>unchecked</strong>: they are practice until a
        blind solver that is not their author agrees with the key.
      </p>

      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          void run(false);
        }}
      >
        <fieldset className="plainset">
          <legend className="lbl mono">SOURCE</legend>
          <div className="row">
            <label className="row" style={{ gap: 6 }}>
              <input
                type="radio"
                name="import-mode"
                checked={mode === 'folder'}
                onChange={() => setMode('folder')}
                disabled={!importable.length}
              />
              <span className="small">
                a file in this goal&rsquo;s sources folder
                {importable.length ? ` (${importable.length})` : ' (none found)'}
              </span>
            </label>
            <label className="row" style={{ gap: 6 }}>
              <input
                type="radio"
                name="import-mode"
                checked={mode === 'upload'}
                onChange={() => setMode('upload')}
              />
              <span className="small">upload files</span>
            </label>
          </div>
        </fieldset>

        {mode === 'folder' ? (
          <div className="grid-2">
            <label className="field">
              <span className="lbl">file</span>
              <select value={path} onChange={(e) => setPath(e.target.value)}>
                {importable.map((f) => (
                  <option key={f.path} value={f.path}>
                    {f.path} ({kb(f.bytes)})
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span className="lbl">answer key (optional)</span>
              <select value={keyPath} onChange={(e) => setKeyPath(e.target.value)}>
                <option value="">none - keys are in the file itself</option>
                {importable
                  .filter((f) => f.path !== path)
                  .map((f) => (
                    <option key={f.path} value={f.path}>
                      {f.path}
                    </option>
                  ))}
              </select>
            </label>
          </div>
        ) : (
          <div className="grid-2">
            <label className="field">
              <span className="lbl">markdown file</span>
              <input
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              />
            </label>
            <label className="field">
              <span className="lbl">answer key file (optional)</span>
              <input
                type="file"
                accept=".md,.markdown,.txt,text/markdown,text/plain"
                onChange={(e) => setKeyFile(e.target.files?.[0] ?? null)}
              />
            </label>
          </div>
        )}

        <fieldset className="plainset">
          <legend className="lbl mono">EXTRACT</legend>
          <div className="stack gap-sm">
            {KINDS.map((k) => (
              <label key={k.id} className="row" style={{ gap: 8, alignItems: 'baseline' }}>
                <input type="checkbox" checked={what.has(k.id)} onChange={() => toggle(k.id)} />
                <span className="small">
                  <strong>{k.label}</strong> <span className="muted">&middot; {k.hint}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>

        <ErrorNote message={error} />

        <div className="row">
          <button
            type="button"
            className="btn"
            disabled={!ready || !!busy}
            onClick={() => void run(true)}
          >
            {busy === 'preview' ? 'reading...' : 'Preview'}
          </button>
          <button type="submit" className="btn primary" disabled={!ready || !!busy}>
            {busy === 'import' ? 'importing...' : 'Import'}
          </button>
          <span className="small muted">
            {!kinds.length
              ? 'Tick at least one thing to extract.'
              : 'Preview writes nothing. Import skips what is already there.'}
          </span>
        </div>
      </form>

      {report ? <ImportReportView r={report} /> : null}
    </div>
  );
}

function ImportReportView({ r }: { r: ImportReport }) {
  const rows = [
    ['questions', r.questions],
    ['flashcards', r.cards],
    ['tables', r.tables],
  ] as const;
  const problems = r.questions?.problems ?? [];
  return (
    <section className="card" aria-label="Import report" role="status">
      <span className="eyebrow">
        {r.dry_run ? 'preview · nothing was written' : 'imported'} &middot; {r.source}
      </span>
      <div className="table-wrap">
        <table className="compact">
          <thead>
            <tr>
              <th scope="col">kind</th>
              <th scope="col">parsed</th>
              <th scope="col">{r.dry_run ? 'would import' : 'imported'}</th>
              <th scope="col">already there</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, c]) => (
              <tr key={label}>
                <td>{label}</td>
                {c ? (
                  <>
                    <td className="mono">{c.parsed}</td>
                    <td className="mono">{c.imported}</td>
                    <td className="mono">{c.skipped_existing}</td>
                  </>
                ) : (
                  <td className="small muted" colSpan={3}>
                    not extracted
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {problems.length ? (
        <div className="note warn small">
          <strong>
            {problems.length} problem{problems.length === 1 ? '' : 's'} in the questions:
          </strong>
          <ul className="plainlist">
            {problems.map((p, i) => (
              <li key={i}>{p}</li>
            ))}
          </ul>
        </div>
      ) : r.questions ? (
        <p className="small muted">No problems with the questions.</p>
      ) : null}

      {r.nodes_created.length ? (
        <p className="small">
          {r.dry_run ? 'Would create' : 'Created'} {r.nodes_created.length} concept
          {r.nodes_created.length === 1 ? '' : 's'} on the map:{' '}
          {r.nodes_created.map((n) => n.title).join(', ')}.
        </p>
      ) : null}

      {!r.dry_run && r.questions?.imported ? (
        <p className="small muted">
          The new questions stay practice-only until they pass a blind check (
          <code>learner bank pending</code> then <code>learner item blind-check</code>).
        </p>
      ) : null}
    </section>
  );
}
