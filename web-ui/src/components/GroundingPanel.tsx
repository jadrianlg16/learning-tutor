'use client';

import { useEffect, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import type { ProposedSource, Source, SourceRole } from '@/lib/types';
import { ErrorNote, Loading } from './Bits';

const ROLE_MEANING: Record<SourceRole, string> = {
  alignment: 'Slides, syllabus, past exams. Constrains scope and notation. Citing one proves alignment, not truth.',
  authority: 'Textbook, docs, standards. Supports correctness.',
  learner: 'Your notes, solutions, code. Evidence about you, not about the subject.',
};

export function GroundingPanel({ goalId, onPlan }: { goalId: string; onPlan: () => void }) {
  const [sources, setSources] = useState<Source[] | null>(null);
  const [role, setRole] = useState<SourceRole>('alignment');
  const [files, setFiles] = useState<FileList | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [topic, setTopic] = useState('');
  const [guidelines, setGuidelines] = useState('');
  const [proposalId, setProposalId] = useState<string | null>(null);
  const [proposed, setProposed] = useState<ProposedSource[]>([]);
  const [accepted, setAccepted] = useState<Set<string>>(new Set());
  const [approvedCount, setApprovedCount] = useState<number | null>(null);

  useEffect(() => {
    api
      .listSources(goalId)
      .then((d) => setSources(d.sources))
      .catch((e) => setError(errorMessage(e)));
  }, [goalId]);

  const upload = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!files?.length) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.uploadSources(goalId, Array.from(files), role);
      setSources(res.sources);
      setFiles(null);
      (e.target as HTMLFormElement).reset();
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const research = async () => {
    setBusy(true);
    setError(null);
    setApprovedCount(null);
    try {
      const res = await api.research(goalId, { topic, guidelines });
      setProposalId(res.proposal_id);
      setProposed(res.sources);
      setAccepted(new Set());
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const approve = async () => {
    if (!proposalId) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.approveResearch(goalId, proposalId, Array.from(accepted));
      setApprovedCount(res.approved);
      setProposed([]);
      setProposalId(null);
      const s = await api.listSources(goalId);
      setSources(s.sources);
    } catch (err) {
      setError(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack gap-lg">
      <div className="card">
        <div className="stack gap-sm">
          <span className="eyebrow">grounding &middot; once per goal</span>
          <h2>Where does the material come from?</h2>
          <p className="muted small" style={{ maxWidth: '64ch' }}>
            Optional. Without it the tutor teaches from what the model already knows - fine for
            canonical topics, wrong for your professor&rsquo;s notation. Corpus text is data, never
            instructions.
          </p>
        </div>

        <ErrorNote message={error} />

        <form className="stack" onSubmit={upload}>
          <div className="grid-2">
            <label className="field">
              <span className="lbl">files</span>
              <input
                type="file"
                multiple
                onChange={(e) => setFiles(e.target.files)}
                aria-describedby="role-meaning"
              />
            </label>
            <label className="field">
              <span className="lbl">role</span>
              <select value={role} onChange={(e) => setRole(e.target.value as SourceRole)}>
                <option value="alignment">alignment</option>
                <option value="authority">authority</option>
                <option value="learner">learner</option>
              </select>
              <span className="hint" id="role-meaning">
                {ROLE_MEANING[role]}
              </span>
            </label>
          </div>
          <div>
            <button type="submit" className="btn" disabled={busy || !files?.length}>
              {busy ? 'uploading...' : 'Upload sources'}
            </button>
          </div>
        </form>

        {sources === null ? <Loading what="sources" /> : null}
        {sources?.length ? (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>file</th>
                  <th>role</th>
                  <th>pages</th>
                  <th>ingested</th>
                </tr>
              </thead>
              <tbody>
                {sources.map((s) => (
                  <tr key={s.source_id}>
                    <td>{s.filename}</td>
                    <td>
                      <span className="pill accent">{s.role}</span>
                    </td>
                    <td className="mono">{s.pages ?? '-'}</td>
                    <td className="mono">{s.ingested_at ?? '-'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : sources ? (
          <p className="muted small">No sources yet.</p>
        ) : null}
      </div>

      <div className="card">
        <div className="stack gap-sm">
          <span className="eyebrow">research pass &middot; you approve the list</span>
          <h2>Proposed sources</h2>
          <p className="muted small">
            Nothing is ingested until you tick it. The proposal says why each one is there and
            which role it would take.
          </p>
        </div>

        <div className="grid-2">
          <label className="field">
            <span className="lbl">topic</span>
            <input
              type="text"
              value={topic}
              onChange={(e) => setTopic(e.target.value)}
              placeholder="differential forms, Stokes, Maxwell"
            />
          </label>
          <label className="field">
            <span className="lbl">guidelines</span>
            <input
              type="text"
              value={guidelines}
              onChange={(e) => setGuidelines(e.target.value)}
              placeholder="prefer sources that match my course notation"
            />
          </label>
        </div>

        <div className="row">
          <button type="button" className="btn" disabled={busy} onClick={research}>
            {busy ? 'searching...' : 'Propose sources'}
          </button>
          {approvedCount !== null ? (
            <span className="pill known">{approvedCount} approved</span>
          ) : null}
        </div>

        {proposed.length ? (
          <div className="stack">
            {proposed.map((p) => (
              <label key={p.url} className="card tight" style={{ cursor: 'pointer' }}>
                <div className="row" style={{ alignItems: 'flex-start', gap: 10 }}>
                  <input
                    type="checkbox"
                    style={{ width: 'auto', marginTop: 4 }}
                    checked={accepted.has(p.url)}
                    onChange={(e) => {
                      const next = new Set(accepted);
                      if (e.target.checked) next.add(p.url);
                      else next.delete(p.url);
                      setAccepted(next);
                    }}
                  />
                  <div className="stack gap-sm">
                    <div className="row">
                      <strong>{p.title}</strong>
                      <span className="pill accent">{p.role}</span>
                    </div>
                    <span className="small muted">{p.why}</span>
                    <span className="small mono muted">{p.url}</span>
                  </div>
                </div>
              </label>
            ))}
            <div>
              <button
                type="button"
                className="btn primary"
                disabled={busy || !accepted.size}
                onClick={approve}
              >
                Approve {accepted.size} source{accepted.size === 1 ? '' : 's'}
              </button>
            </div>
          </div>
        ) : null}
      </div>

      <div className="banner">
        <span>Grounding is optional. When you are ready, reason the arc out.</span>
        <button type="button" className="btn primary" onClick={onPlan}>
          Build the plan
        </button>
      </div>
    </div>
  );
}
