'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { Suspense, useEffect, useState } from 'react';
import { MOCK_ENABLED } from '@/lib/api';

type Theme = 'system' | 'light' | 'dark';

function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>('system');

  useEffect(() => {
    const stored = window.localStorage.getItem('lt-theme') as Theme | null;
    if (stored === 'light' || stored === 'dark') setTheme(stored);
  }, []);

  useEffect(() => {
    const root = document.documentElement;
    if (theme === 'system') {
      root.removeAttribute('data-theme');
      window.localStorage.removeItem('lt-theme');
    } else {
      root.setAttribute('data-theme', theme);
      window.localStorage.setItem('lt-theme', theme);
    }
  }, [theme]);

  const next: Theme = theme === 'system' ? 'light' : theme === 'light' ? 'dark' : 'system';

  return (
    <button
      type="button"
      className="linkish"
      onClick={() => setTheme(next)}
      title="Light / dark / follow the system"
      aria-label={`Theme: ${theme}. Switch to ${next}.`}
    >
      theme: {theme}
    </button>
  );
}

function Nav() {
  const pathname = usePathname();
  const params = useSearchParams();
  const g = params.get('g');
  const here = (p: string) => (pathname === p || pathname === `${p}/` ? 'page' : undefined);

  return (
    <nav className="topnav" aria-label="Main">
      <Link href="/" aria-current={here('/')}>
        goals
      </Link>
      {g ? (
        <>
          <Link href={`/goal/?g=${encodeURIComponent(g)}`} aria-current={here('/goal')}>
            session
          </Link>
          <Link href={`/goal/map/?g=${encodeURIComponent(g)}`} aria-current={here('/goal/map')}>
            map
          </Link>
          <Link href={`/goal/study/?g=${encodeURIComponent(g)}`} aria-current={here('/goal/study')}>
            study
          </Link>
          <Link
            href={`/goal/metrics/?g=${encodeURIComponent(g)}`}
            aria-current={here('/goal/metrics')}
          >
            metrics
          </Link>
        </>
      ) : null}
      <Link href="/passport/" aria-current={here('/passport')}>
        passport
      </Link>
      <ThemeToggle />
    </nav>
  );
}

export function TopBar() {
  return (
    <header className="topbar">
      <Link className="brand" href="/">
        Learning Tutor
      </Link>
      {MOCK_ENABLED ? (
        <span className="mockbadge" title="Fake in-browser gateway. No backend is being called.">
          MOCK
        </span>
      ) : null}
      <Suspense fallback={<nav className="topnav" aria-label="Main" />}>
        <Nav />
      </Suspense>
    </header>
  );
}
