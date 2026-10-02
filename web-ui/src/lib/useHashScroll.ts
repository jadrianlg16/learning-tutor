'use client';

import { useEffect } from 'react';

/**
 * Re-applies `location.hash` once the content a panel was waiting on is on screen.
 *
 * The browser resolves a fragment at load time, when the panels are still fetching, so a
 * deep link like `/goal/?g=g_forms#checkpoint` would otherwise land at the top of an empty
 * page. Call it from every panel that owns an anchor, with that panel's own "loaded" flag:
 * it is a single deferred scroll, so calls that fire before their element exists simply do
 * nothing and the later one wins.
 */
export function useHashScroll(ready: boolean): void {
  useEffect(() => {
    if (!ready || typeof window === 'undefined') return;
    const id = window.location.hash.slice(1);
    if (!id) return;
    const t = window.setTimeout(() => {
      document.getElementById(id)?.scrollIntoView({ block: 'start' });
    }, 80);
    return () => window.clearTimeout(t);
  }, [ready]);
}
