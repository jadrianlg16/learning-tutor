'use client';

import { useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { ErrorNote } from '@/components/Bits';

/**
 * The learner passport: the model-independence promise made concrete. Everything the system
 * believes about you, in a file you keep, whether or not you keep using this tool.
 */
export default function PassportPage() {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<string | null>(null);

  const download = async () => {
    setBusy(true);
    setError(null);
    try {
      const blob = await api.downloadPassport();
      const isZip = blob.type.includes('zip');
      const name = `learner-passport-${new Date().toISOString().slice(0, 10)}.${isZip ? 'zip' : 'json'}`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = name;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      setDone(`${name} (${Math.max(1, Math.round(blob.size / 1024))} KB)`);
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <main className="main" id="main">
      <div className="stack gap-sm">
        <span className="eyebrow">passport</span>
        <h1>Take your learner model with you</h1>
        <p className="muted" style={{ maxWidth: '64ch' }}>
          One archive with everything the system believes about you and the evidence behind it.
          If you stop using this tool tomorrow, the model does not stay behind.
        </p>
      </div>

      <div className="card">
        <span className="eyebrow">what is in the archive</span>
        <ul className="small" style={{ margin: 0, paddingLeft: 18 }}>
          <li>
            <code>events.jsonl</code> - every question, answer, confidence and assistance level,
            append-only
          </li>
          <li>
            <code>state.json</code> - the derived graph and per-node state
          </li>
          <li>
            <code>learner.md</code> and <code>notes.md</code> - the readable projections
          </li>
          <li>
            <code>goals.json</code>, <code>graph.json</code>, <code>disputes.json</code>
          </li>
          <li>
            <code>artifacts/</code> - the diagrams and session logs generated along the way
          </li>
        </ul>

        <ErrorNote message={error} />
        {done ? <p className="note good small">Downloaded {done}.</p> : null}

        <div className="row">
          <button type="button" className="btn primary" disabled={busy} onClick={download}>
            {busy ? 'building...' : 'Download passport'}
          </button>
          <span className="small muted mono">GET {api.passportUrl()}</span>
        </div>
      </div>
    </main>
  );
}
