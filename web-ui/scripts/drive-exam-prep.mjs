#!/usr/bin/env node
/**
 * Drive the exam-prep flows end to end against `npm run dev:mock` and save screenshots.
 *
 *   PORT=3117 npm run dev:mock                      # one shell
 *   PUPPETEER_DIR=<folder with puppeteer-core installed> BASE_URL=http://localhost:3117 \
 *     node scripts/drive-exam-prep.mjs              # another
 *
 * puppeteer-core is NOT a dependency of this app (nothing is added to package.json): install
 * it anywhere (`npm i puppeteer-core` in a scratch folder) and point PUPPETEER_DIR at that
 * folder. It drives the Chrome/Edge already on the machine (CHROME_PATH, else probed).
 *
 * What it does, asserting as it goes (exit 1 on the first failed check):
 *   1. Progress is the default tab for a goal with a blueprint; baseline numbers read.
 *   2. Practice in Mixed: 6 questions answered by keyboard; every answer request carries the
 *      question's `order`; the key never appears before the attempt.
 *   3. Practice focused on area 2: 5 questions, every one from area 2.
 *   4. Progress again: first attempts, area 2 and today's activity moved by exactly 11.
 *   5. Mock: default size 60; start 30; answer 8, change one, flag one; no feedback in the DOM;
 *      reload -> the mock resumes with the same answers, flag and question; submit with 22
 *      unanswered (confirm dialog says so); results by area; "only wrong" filter.
 *   6. Auto-submit: a 1-minute mock started through the API is submitted by the page when
 *      its clock runs out.
 *   7. Screenshots (desktop 1280 and phone 390, light and dark for Progress) and a
 *      no-horizontal-scroll check at 390 px on every tab.
 */
import { existsSync, mkdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const OUT = path.resolve(HERE, '..', 'docs', 'screenshots');
const BASE = process.env.BASE_URL ?? `http://localhost:${process.env.PORT ?? 3000}`;
const GOAL = process.env.SHOT_GOAL ?? 'egel-isoft';
const STUDY = `${BASE}/goal/study/?g=${encodeURIComponent(GOAL)}`;

const require = createRequire(path.join(process.env.PUPPETEER_DIR ?? process.cwd(), 'package.json'));
let puppeteer;
try {
  puppeteer = require('puppeteer-core');
} catch {
  console.error('puppeteer-core not found. Install it in a scratch folder and set PUPPETEER_DIR to it.');
  process.exit(1);
}

const chrome = [
  process.env.CHROME_PATH,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].find((p) => p && existsSync(p));
if (!chrome) {
  console.error('No Chrome/Edge/Chromium found. Set CHROME_PATH.');
  process.exit(1);
}

mkdirSync(OUT, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
let checks = 0;
function check(cond, what, detail = '') {
  checks += 1;
  phase = what;
  if (!cond) {
    console.error(`FAIL ${what}${detail ? ` - ${detail}` : ''}`);
    throw new Error(what);
  }
  console.log(`ok   ${what}${detail ? ` - ${detail}` : ''}`);
}

const browser = await puppeteer.launch({ executablePath: chrome, headless: true, args: ['--no-first-run', '--hide-scrollbars'] });
const ctx = await browser.createBrowserContext();
const page = await ctx.newPage();
const consoleErrors = [];
let phase = 'start';
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(`[${phase}] ${m.text()}`);
});
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));
page.on('response', (r) => {
  if (r.status() >= 400) consoleErrors.push(`${r.status()} ${r.url()}`);
});

async function viewport(width, scheme = 'light') {
  await page.setViewport({ width, height: 900, deviceScaleFactor: width < 600 ? 2 : 1.5 });
  await page.emulateMediaFeatures([{ name: 'prefers-color-scheme', value: scheme }]);
}
/**
 * Full-page by default, from the top (a capture that starts scrolled paints the sticky
 * header mid-page). `viewport: true` = exactly what a phone screen shows, which is the
 * honest picture for the fixed exam bar. Next's dev-only indicator is removed first.
 */
async function shot(name, { viewport: onlyViewport = false, scrollY = 0 } = {}) {
  await page.evaluate((y) => {
    document.querySelectorAll('nextjs-portal').forEach((el) => el.remove());
    window.scrollTo(0, y);
  }, scrollY);
  await sleep(400);
  const file = path.join(OUT, `${name}.png`);
  await page.screenshot({ path: file, fullPage: !onlyViewport, captureBeyondViewport: !onlyViewport });
  console.log(`shot ${path.relative(process.cwd(), file)}`);
}
async function noHScroll(where) {
  const d = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  check(d.sw <= d.cw, `no horizontal scroll: ${where}`, `scrollWidth ${d.sw}, clientWidth ${d.cw}`);
}
/** The in-page fake gateway answers `fetch` too: read the same JSON the UI reads. */
const api = (p, init) =>
  page.evaluate(
    async (p, init) => {
      const r = await fetch(`/api${p}`, init);
      return { status: r.status, body: await r.json() };
    },
    p,
    init ?? null,
  );
/** Log every gateway request the app makes (method, path, JSON body). */
async function tapRequests() {
  await page.evaluate(() => {
    if (window.__tapped) return;
    window.__tapped = true;
    window.__req = [];
    const f = window.fetch;
    window.fetch = (input, init) => {
      const url = typeof input === 'string' ? input : input.url;
      if (url.includes('/api/')) {
        let body = null;
        try {
          body = init && typeof init.body === 'string' ? JSON.parse(init.body) : null;
        } catch {
          body = null;
        }
        window.__req.push({ method: (init && init.method) || 'GET', url, body });
      }
      return f(input, init);
    };
  });
}
const requests = () => page.evaluate(() => window.__req ?? []);
async function tab(id) {
  await page.click(`#study-tab-${id}`);
  await page.waitForFunction((id) => document.querySelector(`#study-tab-${id}`)?.getAttribute('aria-selected') === 'true', {}, id);
}
const text = (sel) => page.$eval(sel, (el) => el.textContent.replace(/\s+/g, ' ').trim());

try {
  /* ------------------------------------------------------------ 1. progress baseline */
  await viewport(1280);
  await page.goto(STUDY, { waitUntil: 'networkidle0' });
  await page.waitForSelector('.progresshead');
  const selected = await page.$eval('[role=tab][aria-selected=true]', (el) => el.id);
  check(selected === 'study-tab-progress', 'Progress is the default tab for a goal with a blueprint', selected);
  await tapRequests();
  const p0 = (await api(`/goals/${GOAL}/progress`)).body;
  const area2 = (p) => p.areas.find((a) => a.code === '2');
  const today0 = p0.activity[p0.activity.length - 1];
  console.log(
    `     baseline: first_attempts ${p0.totals.first_attempts}, attempts ${p0.totals.attempts}, area 2 first ${area2(p0).first_attempts}, today answers ${today0.answers}`,
  );
  check(area2(p0).first_attempts === 0, 'area 2 starts with no data', 'rendered as "no data yet", not 0%');
  const area2Text = await page.$$eval('.arearow', (rows) => rows[1].textContent);
  check(/no data yet/.test(area2Text) && !/\b0%/.test(area2Text.replace(/\d+% · \d+ items/, '')), 'area 2 row says "no data yet" and shows no 0%');

  /* ------------------------------------------------------------ 2. practice, mixed */
  await tab('practice');
  await page.waitForSelector('section.qcard');
  check((await page.$eval('.focuspick select', (s) => s.value)) === '', 'focus defaults to Mixed');
  const mixedAreas = [];
  for (let i = 0; i < 6; i += 1) {
    await page.waitForFunction(() => document.querySelector('section.qcard .options .opt:not([disabled])'));
    const before = await page.$eval('section.qcard', (el) => el.textContent);
    check(!/correct answer/.test(before), `mixed #${i + 1}: no key in the DOM before the attempt`);
    const head = await text('section.qcard .qhead');
    mixedAreas.push((head.match(/Area (\d)/) ?? [])[1]);
    await page.keyboard.press(['a', 'b', 'c'][i % 3]);
    await page.keyboard.press('Enter');
    await page.waitForSelector('section.qcard [role=status] .note');
    const verdict = await text('section.qcard [role=status] .note');
    console.log(`     mixed #${i + 1} [${head.slice(0, 70)}] -> ${verdict.slice(0, 40)}`);
    if (i === 1) await shot('practice-feedback-desktop');
    await page.keyboard.press('Enter');
    await page.waitForFunction((prev) => document.querySelector('section.qcard')?.textContent !== prev, {}, before);
  }
  let answers = (await requests()).filter((r) => r.url.includes('/practice/answer'));
  check(answers.length === 6, 'six practice answers sent', `${answers.length}`);
  check(
    answers.every((r) => Array.isArray(r.body.order) && r.body.order.length === 3),
    'every practice answer carries the shown option order',
    JSON.stringify(answers.map((r) => r.body.order)),
  );
  check(new Set(mixedAreas).size >= 2, 'Mixed interleaves areas', `areas served: ${mixedAreas.join(',')}`);

  /* ------------------------------------------------------------ 3. practice, focused */
  await page.select('.focuspick select', '2');
  await page.waitForFunction(() => /focus: 2 /.test(document.querySelector('.tabpanel')?.textContent ?? ''));
  await shot('practice-focus-desktop');
  const focusAreas = [];
  for (let i = 0; i < 5; i += 1) {
    await page.waitForFunction(() => document.querySelector('section.qcard .options .opt:not([disabled])'));
    const before = await page.$eval('section.qcard', (el) => el.textContent);
    const head = await text('section.qcard .qhead');
    focusAreas.push((head.match(/Area (\d)/) ?? [])[1]);
    // mouse this time
    const opts = await page.$$('section.qcard .options .opt');
    await opts[i % opts.length].click();
    await page.click('section.qcard .btn.primary');
    await page.waitForSelector('section.qcard [role=status] .note');
    await page.click('section.qcard .row .btn.primary');
    await page.waitForFunction((prev) => document.querySelector('section.qcard')?.textContent !== prev, {}, before);
  }
  const nextReqs = (await requests()).filter((r) => r.url.includes('/practice/next'));
  check(nextReqs.some((r) => r.url.includes('focus=2')), 'practice/next is called with focus=2');
  check(focusAreas.every((a) => a === '2'), 'every focused question is from area 2', focusAreas.join(','));
  answers = (await requests()).filter((r) => r.url.includes('/practice/answer'));
  check(answers.length === 11, 'eleven practice answers in total', `${answers.length}`);

  /* ------------------------------------------------------------ 4. progress moved */
  await tab('progress');
  await page.waitForSelector('.progresshead');
  const p1 = (await api(`/goals/${GOAL}/progress`)).body;
  const today1 = p1.activity[p1.activity.length - 1];
  check(today1.answers - today0.answers === 11, "today's activity grew by 11 answers", `${today0.answers} -> ${today1.answers}`);
  check(p1.totals.attempts - p0.totals.attempts === 11, 'total attempts grew by 11', `${p0.totals.attempts} -> ${p1.totals.attempts}`);
  check(area2(p1).first_attempts >= 5, 'area 2 now has first attempts', `${area2(p0).first_attempts} -> ${area2(p1).first_attempts}`);
  const area2Now = await page.$$eval('.arearow', (rows) => rows[1].querySelector('.measure:last-child .mval').textContent);
  check(/of \d+ first try/.test(area2Now), 'area 2 row now shows its first-try counts', area2Now);
  await shot('progress-desktop-light-after-practice');
  // "practise area 3" hands the focus over to the Practice tab
  const areaLinks = await page.$$('.areafoot .linkbtn');
  await areaLinks[2].click();
  await page.waitForFunction(() => document.querySelector('#study-tab-practice')?.getAttribute('aria-selected') === 'true');
  await page.waitForSelector('section.qcard');
  check((await page.$eval('.focuspick select', (s) => s.value)) === '3', '"practise area 3" opens Practice focused on area 3');
  check(/Area 3/.test(await text('section.qcard .qhead')), 'and serves an area 3 question');
  await tab('progress');
  await page.waitForSelector('.progresshead');

  /* ------------------------------------------------------------ 5. mock exam */
  await tab('mock');
  await page.waitForSelector('.sizes input[type=radio]');
  const def = await page.$eval('.sizes input[type=radio]:checked', (el) => el.value);
  check(def === '60', 'mock size defaults to 60 when 60 are available', def);
  await shot('mock-lobby-desktop');
  await page.click('.sizes input[type=radio][value="30"]');
  await page.click('.card .btn.primary');
  await page.waitForSelector('.examhead .timer');
  const open = (await api(`/goals/${GOAL}/mocks`)).body.open;
  check(open && open.n === 30, 'a 30-question mock is open', open?.session_id);
  const sid = open.session_id;
  const examHtml0 = await page.$eval('.tabpanel', (el) => el.innerHTML);
  check(!/correct answer|opt right|opt wrong|explanation/i.test(examHtml0), 'no feedback of any kind on the exam screen');
  const picks = ['a', 'b', 'c', 'a', 'b', 'c', 'a', 'b'];
  for (let i = 0; i < picks.length; i += 1) {
    await page.keyboard.press(picks[i]);
    if (i === 2) await page.keyboard.press('f'); // flag question 3
    if (i === 4) await page.keyboard.press('3'); // confidence on question 5
    await page.keyboard.press('Enter');
  }
  // go back to question 2 and change its answer B -> C
  await page.click('.qnav-grid li:nth-child(2) .qn');
  await page.keyboard.press('c');
  await page.click('.qnav-grid li:nth-child(9) .qn');
  const status1 = await text('.examhead .examstat .long');
  check(/8 of 30 answered · 1 flagged/.test(status1), 'exam header counts 8 answered, 1 flagged', status1);
  const nav1 = await page.$$eval('.qnav-grid .qn', (b) => b.map((x) => x.className));
  const draft1 = await page.evaluate((sid) => localStorage.getItem(`lt-mock:${sid}`), sid);
  check(!!draft1, 'answers are in localStorage under the session id', `lt-mock:${sid}`);
  const pressed1 = [];
  for (const n of [1, 2, 3, 8]) {
    await page.click(`.qnav-grid li:nth-child(${n}) .qn`);
    pressed1.push(await page.$eval('.options .opt[aria-pressed=true] .key', (k) => k.textContent).catch(() => null));
  }
  check(pressed1[1] === 'C', 'question 2 shows the changed answer C', pressed1.join(','));
  await page.click('.qnav-grid li:nth-child(9) .qn');
  const examHtml1 = await page.$eval('.tabpanel', (el) => el.innerHTML);
  check(!/correct answer|opt right|opt wrong/i.test(examHtml1), 'still no feedback after answering');
  await shot('mock-exam-desktop');

  // reload: the fake gateway keeps the open mock (sessionStorage), the page its draft
  await page.reload({ waitUntil: 'networkidle0' });
  await page.waitForSelector('.examhead .timer');
  await tapRequests(); // the reload dropped the request log
  check((await page.$eval('[role=tab][aria-selected=true]', (el) => el.id)) === 'study-tab-mock', 'after reload the Mock exam tab is back');
  const status2 = await text('.examhead .examstat .long');
  check(status2 === status1, 'after reload: same answered and flagged counts', status2);
  const nav2 = await page.$$eval('.qnav-grid .qn', (b) => b.map((x) => x.className));
  check(JSON.stringify(nav2) === JSON.stringify(nav1), 'after reload: navigator identical (answered/flagged/current)');
  const pressed2 = [];
  for (const n of [1, 2, 3, 8]) {
    await page.click(`.qnav-grid li:nth-child(${n}) .qn`);
    pressed2.push(await page.$eval('.options .opt[aria-pressed=true] .key', (k) => k.textContent).catch(() => null));
  }
  check(JSON.stringify(pressed2) === JSON.stringify(pressed1), 'after reload: the same options are selected', pressed2.join(','));
  check((await page.$$eval('.qnav-grid .qn.flagged', (b) => b.map((x) => x.textContent))).join() === '3', 'after reload: question 3 is still flagged');

  // phone view of the exam: a real 390x844 screen, scrolled to the question
  await page.setViewport({ width: 390, height: 844, deviceScaleFactor: 2 });
  await sleep(300);
  await noHScroll('mock exam at 390');
  const qTop = await page.$eval('.examgrid', (el) => el.getBoundingClientRect().top + window.scrollY - 12);
  await shot('mock-exam-phone', { viewport: true, scrollY: qTop });
  await viewport(1280);

  // submit
  await page.click('.examhead .btn.primary');
  await page.waitForSelector('[role=dialog]');
  const dlg = await text('[role=dialog]');
  check(/22 of 30 unanswered/.test(dlg) && /1 still flagged/.test(dlg), 'confirm dialog says 22 unanswered and 1 flagged', dlg.slice(0, 90));
  await shot('mock-submit-confirm-desktop');
  await page.click('[role=dialog] .btn.primary');
  await page.waitForSelector('.reviewlist');
  const sub = (await requests()).filter((r) => r.url.includes('/submit')).pop();
  check(sub && sub.body.answers.length === 30, 'submit sends all 30 items', `${sub?.body.answers.length}`);
  check(sub.body.answers.filter((a) => a.response === null).length === 22, '22 sent as null (recorded as idk)');
  check(sub.body.answers.every((a) => Array.isArray(a.order)), 'every mock answer carries its order');
  check(
    (await page.evaluate((sid) => localStorage.getItem(`lt-mock:${sid}`), sid)) === null,
    'the local draft is cleared after submit',
  );
  const score = await text('.progresshead .kpi.hero');
  const result = (await api(`/goals/${GOAL}/mocks/${sid}`)).body;
  check(result.status === 'submitted' && score.includes(`${result.correct} of 30`), 'results show the graded score', score);
  const rows = await page.$$eval('.reviewlist > li', (l) => l.length);
  check(rows === 30, 'review lists all 30 items', `${rows}`);
  await shot('mock-result-desktop');
  await page.click('#mock-review-h ~ label input, section[aria-labelledby=mock-review-h] input[type=checkbox]');
  const wrongRows = await page.$$eval('.reviewlist > li', (l) => l.length);
  check(wrongRows === 30 - result.correct, '"only wrong" leaves the wrong and unanswered ones', `${wrongRows}`);
  await viewport(390);
  await sleep(300);
  await noHScroll('mock results at 390');
  await shot('mock-result-phone');
  await viewport(1280);

  /* ------------------------------------------------------------ 6. progress after the mock */
  await tab('progress');
  await page.waitForSelector('.multiples');
  const p2 = (await api(`/goals/${GOAL}/progress`)).body;
  check(p2.mocks.filter((m) => m.submitted_at).length === 1, 'progress lists the submitted mock');
  check(p2.totals.attempts - p1.totals.attempts === 30, 'the mock added 30 attempts', `${p1.totals.attempts} -> ${p2.totals.attempts}`);
  for (const [w, scheme] of [
    [1280, 'light'],
    [1280, 'dark'],
    [390, 'light'],
    [390, 'dark'],
  ]) {
    await viewport(w, scheme);
    await sleep(400);
    if (w === 390) await noHScroll(`progress at 390 ${scheme}`);
    await shot(`progress-${w === 390 ? 'phone' : 'desktop'}-${scheme}`);
  }
  await viewport(390);
  for (const t of ['practice', 'mock', 'cards', 'tables', 'import']) {
    await tab(t);
    await sleep(600);
    await noHScroll(`${t} tab at 390`);
    if (t === 'practice') await shot('practice-phone');
    if (t === 'mock') await shot('mock-lobby-phone');
  }
  await viewport(1280);

  /* ------------------------------------------------------------ 7. auto-submit */
  const started = await api(`/goals/${GOAL}/mocks`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ n: 3, minutes: 1 }),
  });
  check(started.status === 200 && started.body.minutes === 1, 'a 1-minute, 3-question mock started through the API');
  await tab('progress');
  await tab('mock');
  await page.waitForSelector('.examhead .timer');
  await page.keyboard.press('b');
  console.log('     waiting for the clock to run out (about a minute)...');
  await page.waitForSelector('.reviewlist', { timeout: 100_000 });
  const auto = await text('.tabpanel');
  check(/submitted automatically/.test(auto), 'the page auto-submitted when time ran out');
  const autoRes = (await api(`/goals/${GOAL}/mocks/${started.body.session_id}`)).body;
  check(autoRes.status === 'submitted' && autoRes.answered === 1, 'auto-submit sent the one answer it had', `answered ${autoRes.answered}`);

  // The app ships no favicon: its 404 (and Chrome's generic console line for it) is not ours.
  const failed = consoleErrors.filter((e) => /^\d{3} /.test(e));
  const onlyFavicon = failed.every((e) => /favicon\.ico/.test(e));
  const errs = consoleErrors.filter(
    (e) => !/favicon\.ico/.test(e) && !(onlyFavicon && /Failed to load resource: .*404/.test(e)),
  );
  check(errs.length === 0, 'no console errors or failed requests', JSON.stringify(errs));
  console.log(`\nALL ${checks} CHECKS PASSED`);
} catch (e) {
  console.error(e);
  await page.screenshot({ path: path.join(OUT, '_failure.png'), fullPage: true }).catch(() => {});
  console.error('console:', JSON.stringify(consoleErrors));
  process.exitCode = 1;
} finally {
  await browser.close();
}
