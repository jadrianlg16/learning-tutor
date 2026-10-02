import type { Metadata, Viewport } from 'next';
import 'katex/dist/katex.min.css';
import './globals.css';
import { TopBar } from '@/components/TopBar';

export const metadata: Metadata = {
  title: 'Learning Tutor',
  description: 'Probe, plan, teach - with an evidence-backed learner model.',
};

export const viewport: Viewport = {
  width: 'device-width',
  initialScale: 1,
  // The question card is the phone surface; let it scale but do not lock zoom.
  themeColor: [
    { media: '(prefers-color-scheme: light)', color: '#f4f5f7' },
    { media: '(prefers-color-scheme: dark)', color: '#101319' },
  ],
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        {/* The webfonts themselves are pulled by an @import at the top of globals.css and
            every family has a real local fallback stack, so a box with no font access still
            renders correctly. next/font is deliberately not used: it fetches at *build*
            time, which would make the Docker build need network access. */}
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
      </head>
      <body>
        <div className="shell">
          <a className="skip" href="#main">
            Skip to content
          </a>
          <TopBar />
          {children}
        </div>
      </body>
    </html>
  );
}
