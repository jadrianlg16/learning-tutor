#!/usr/bin/env node
/**
 * Regenerate docs/screenshots/*.png from a running mock server.
 *
 *   npm run dev:mock          # in one shell (PORT defaults to 3000)
 *   npm run screenshots       # in another
 *
 * Uses the browser already on the machine in headless mode - no Puppeteer, no Chromium
 * download, nothing added to package.json.
 *
 * Every shot is one plain URL captured in a window tall enough to hold the whole page: the
 * mock seeds goals parked at the plan review, at the probe and mid-teach, and the map page
 * deep-links a concept with `?node=`. No scripted clicking, and - deliberately - no
 * scrolling: a programmatic scroll under `--virtual-time-budget` leaves headless Chrome
 * painting a stale band where the sticky header was, so the window is simply made tall
 * enough instead.
 *
 * Config (all env, no machine-specific values in this file):
 *   BASE_URL     default http://localhost:${PORT ?? 3000}
 *   CHROME_PATH  path to chrome/edge/chromium; probed from the usual locations otherwise
 */
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '..', 'docs', 'screenshots');
const BASE = process.env.BASE_URL ?? `http://localhost:${process.env.PORT ?? 3000}`;

const CANDIDATES = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean);

const chrome = CANDIDATES.find((p) => existsSync(p));
if (!chrome) {
  console.error('No Chrome/Edge/Chromium found. Set CHROME_PATH.');
  process.exit(1);
}

/**
 * Two shot sets. The default one runs against `npm run dev:mock` and its seeded goals.
 * Setting SHOT_GOAL switches to the live set: the same screens against a real gateway
 * (`BASE_URL=http://localhost:5033 SHOT_GOAL=<goal id> SHOT_NODE=<node id> npm run
 * screenshots`). The committed screenshots are all from the mock set. Nothing here is
 * machine-specific: ids and base URL are env, the browser is probed.
 */
const LIVE_GOAL = process.env.SHOT_GOAL;
const LIVE_NODE = process.env.SHOT_NODE ?? '';
const g = encodeURIComponent(LIVE_GOAL ?? '');

const LIVE_SHOTS = [
  {
    name: 'map-overview',
    url: `${BASE}/goal/map/?g=${g}`,
    width: 1440,
    height: 1400,
    scale: 1.5,
    what: 'the map at fit-to-width: chapters packed into rows, toolbar, both legends',
  },
  {
    name: 'map-receipts-drawer',
    url: `${BASE}/goal/map/?g=${g}${LIVE_NODE ? `&node=${encodeURIComponent(LIVE_NODE)}` : ''}`,
    width: 1440,
    height: 1100,
    scale: 1.5,
    what: 'a concept deep-linked: receipts in the right-hand drawer, graph still in place',
  },
  {
    name: 'map-mobile-drawer',
    // 492, not 375: headless Chrome on Windows clamps a window to ~492 DIP. Still inside
    // the <=560px branch, so this is the phone layout; a true 375px pass is done by hand.
    url: `${BASE}/goal/map/?g=${g}${LIVE_NODE ? `&node=${encodeURIComponent(LIVE_NODE)}` : ''}`,
    width: 492,
    height: 1200,
    scale: 2,
    what: 'the map on the phone surface: stacked toolbar, receipts as a bottom sheet',
  },
  {
    name: 'map-session-panels',
    url: `${BASE}/goal/?g=${g}`,
    width: 1240,
    height: 1500,
    scale: 1.5,
    what: 'the session screen: sticky goal header, phase rail, collapsible panels',
  },
];

/** `#anchor` scrolls the shot to the element; the app re-applies it after data loads. */
const MOCK_SHOTS = [
  {
    name: 'plan-graph',
    url: `${BASE}/goal/?g=g_stokes`,
    width: 1240,
    height: 1820,
    scale: 2,
    what: 'the plan: dependency graph coloured by state, feasibility line',
  },
];

const SHOTS = LIVE_GOAL ? LIVE_SHOTS : MOCK_SHOTS;

mkdirSync(OUT, { recursive: true });
const profile = mkdtempSync(path.join(tmpdir(), 'lt-shot-'));

try {
  for (const shot of SHOTS) {
    const file = path.join(OUT, `${shot.name}.png`);
    execFileSync(
      chrome,
      [
        '--headless=new',
        '--disable-gpu',
        '--hide-scrollbars',
        '--no-first-run',
        '--no-default-browser-check',
        `--user-data-dir=${profile}`,
        `--force-device-scale-factor=${shot.scale ?? 2}`,
        // Light is the primary identity (paper/ink); without this the shots follow the host OS.
        '--blink-settings=preferredColorScheme=1',
        '--run-all-compositor-stages-before-draw',
        '--virtual-time-budget=9000',
        `--window-size=${shot.width},${shot.height}`,
        `--screenshot=${file}`,
        shot.url,
      ],
      { stdio: 'inherit', timeout: 90_000 },
    );
    console.log(`wrote ${path.relative(process.cwd(), file)}  - ${shot.what}`);
  }
} finally {
  rmSync(profile, { recursive: true, force: true });
}
