'use client';

import { useEffect, useRef, useState } from 'react';
import { api, errorMessage } from '@/lib/api';
import { DISPUTE_TYPES, type DisputeType, type Question } from '@/lib/types';
import { ErrorNote } from './Bits';

/**
 * "skip / dispute" - the six typed disputes from IDEA.md. None of them silently becomes
 * mastery: "I already know this" and "test me instead" come back with a check the learner
 * has to pass, and the UI says so before they pick.
 */
export function DisputeMenu({
  goalId,
  nodeId,
  itemId,
  onCheckItems,
}: {
  goalId: string;
  nodeId: string;
  itemId?: string;
  onCheckItems?: (items: Question[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const [type, setType] = useState<DisputeType | null>(null);
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const wrap = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    };
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onEsc);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onEsc);
    };
  }, [open]);

  const submit = async () => {
    if (!type) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.dispute(goalId, { type, node_id: nodeId, item_id: itemId, note });
      const served = !!(res.check?.items?.length && onCheckItems);
      setResult(
        served
          ? `Dispute ${res.dispute_id} opened. It is settled by evidence: the check is now on screen.`
          : res.check?.items?.length
            ? `Dispute ${res.dispute_id} opened. Its check will be served the next time this concept comes up.`
            : `Dispute ${res.dispute_id} opened. It will be settled by evidence, not by asserting it.`,
      );
      if (served) onCheckItems!(res.check!.items);
      setType(null);
      setNote('');
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="stack gap-sm">
      <div className="menu" ref={wrap}>
        <button
          type="button"
          className="btn sm"
          aria-haspopup="menu"
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          skip / dispute
        </button>
        {open ? (
          <div className="items" role="menu">
            {DISPUTE_TYPES.map((t) => (
              <button
                key={t}
                type="button"
                role="menuitem"
                onClick={() => {
                  setType(t);
                  setOpen(false);
                  setResult(null);
                }}
              >
                {t}
              </button>
            ))}
          </div>
        ) : null}
      </div>

      {type ? (
        <div className="card tight">
          <span className="eyebrow">dispute &middot; {type}</span>
          <p className="small muted">
            {type === 'I already know this' || type === 'test me instead'
              ? 'This opens a check, not a shortcut: a self-report is a claim, and only a passed item moves the map.'
              : 'This is recorded as a transparent dispute against the item or edge and settled by evidence.'}
          </p>
          <label className="field">
            <span className="lbl">note</span>
            <textarea
              value={note}
              onChange={(e) => setNote(e.target.value)}
              placeholder="What is wrong, in your words."
            />
          </label>
          <div className="row">
            <button type="button" className="btn primary sm" disabled={busy} onClick={submit}>
              {busy ? 'opening...' : 'Open dispute'}
            </button>
            <button type="button" className="btn sm" onClick={() => setType(null)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      <ErrorNote message={error} />
      {result ? <p className="note good small">{result}</p> : null}
    </div>
  );
}
