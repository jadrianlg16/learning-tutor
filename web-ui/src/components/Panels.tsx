'use client';

/**
 * The three containers every long screen needed and did not have: a panel that collapses
 * and remembers, a side drawer (right on desktop, bottom sheet on mobile) so opening a
 * concept does not scroll the graph off screen, and a list that truncates.
 *
 * Persistence is localStorage, per key, read after mount - the export is prerendered, so
 * reading storage during the first render would make the markup disagree with itself.
 */

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';

function readOpen(key: string | undefined, fallback: boolean): boolean {
  if (!key || typeof window === 'undefined') return fallback;
  try {
    const v = window.localStorage.getItem(`lt-panel:${key}`);
    return v === null ? fallback : v === '1';
  } catch {
    return fallback;
  }
}

export function usePersistentToggle(
  key: string | undefined,
  fallback: boolean,
): [boolean, (next?: boolean) => void] {
  const [open, setOpen] = useState(fallback);
  useEffect(() => {
    setOpen(readOpen(key, fallback));
    // `fallback` is the initial default only; re-reading on its change would fight the user.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const toggle = useCallback(
    (next?: boolean) => {
      setOpen((prev) => {
        const value = next ?? !prev;
        if (key && typeof window !== 'undefined') {
          try {
            window.localStorage.setItem(`lt-panel:${key}`, value ? '1' : '0');
          } catch {
            /* storage disabled: the toggle still works for this page view */
          }
        }
        return value;
      });
    },
    [key],
  );
  return [open, toggle];
}

export function Collapsible({
  title,
  subtitle,
  storageKey,
  defaultOpen = true,
  aside,
  id,
  children,
}: {
  title: ReactNode;
  subtitle?: ReactNode;
  /** localStorage namespace; omit and the panel forgets on reload. */
  storageKey?: string;
  defaultOpen?: boolean;
  /** Rendered on the right of the header, before the chevron. */
  aside?: ReactNode;
  id?: string;
  children: ReactNode;
}) {
  const [open, toggle] = usePersistentToggle(storageKey, defaultOpen);
  return (
    <section className={`panel${open ? ' open' : ''}`} id={id}>
      <div className="panel-head">
        <button
          type="button"
          className="panel-toggle"
          aria-expanded={open}
          onClick={() => toggle()}
        >
          <span className="chev" aria-hidden="true">
            {open ? '▾' : '▸'}
          </span>
          <span className="panel-title">{title}</span>
        </button>
        {aside ? <div className="panel-aside">{aside}</div> : null}
      </div>
      {subtitle && !open ? <p className="small muted panel-sub">{subtitle}</p> : null}
      {open ? <div className="panel-body">{children}</div> : null}
    </section>
  );
}

/**
 * Right-hand drawer on desktop, bottom sheet under 860px. Esc and the X close it; the
 * page underneath does not move, which is the whole point - the graph stays where it was.
 */
export function Drawer({
  open,
  title,
  onClose,
  children,
}: {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (ev: KeyboardEvent) => {
      if (ev.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  useEffect(() => {
    if (open) ref.current?.focus();
  }, [open]);

  if (!open) return null;
  return (
    <aside
      className="drawer"
      role="dialog"
      aria-label={typeof title === 'string' ? title : 'details'}
      tabIndex={-1}
      ref={ref}
    >
      <header className="drawer-head">
        <span className="eyebrow">{title}</span>
        <button type="button" className="btn sm" onClick={onClose} aria-label="Close (Esc)">
          &times;
        </button>
      </header>
      <div className="drawer-body">{children}</div>
    </aside>
  );
}

/** A long list that shows `limit` items and then says how many it is holding back. */
export function TruncatedList({
  items,
  limit = 6,
  label,
}: {
  items: ReactNode[];
  limit?: number;
  label?: string;
}) {
  const [all, setAll] = useState(false);
  if (!items.length) return null;
  const shown = all ? items : items.slice(0, limit);
  return (
    <>
      {shown.map((item, i) => (
        <span key={i}>
          {i > 0 ? ', ' : ''}
          {item}
        </span>
      ))}
      {items.length > limit ? (
        <>
          {' '}
          <button type="button" className="linkbtn" onClick={() => setAll((v) => !v)}>
            {all ? 'show fewer' : `show all ${items.length}${label ? ` ${label}` : ''}`}
          </button>
        </>
      ) : null}
    </>
  );
}
