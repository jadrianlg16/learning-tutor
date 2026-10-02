import { test } from 'node:test';
import assert from 'node:assert/strict';

import { renderMermaidToSvg } from '../src/mermaidRender.js';
import { checkMermaid } from '../src/mermaidCheck.js';
import { checkLatex } from '../src/latexCheck.js';

// Force an OS-assigned ephemeral port for the in-process HTTP test below, so
// the suite never collides with a real render-svc (default 5035) that might
// already be running on this machine.
process.env.RENDER_PORT = '0';

// A 5-node flowchart (A,B,C,D,E) with two classDefs and class assignments.
const FLOWCHART_WITH_CLASSDEF = `
flowchart TD
  A[Start] --> B[Load Data]
  B --> C{Valid?}
  C -->|Yes| D[Process]
  C -->|No| E[Reject]
  classDef success fill:#22c55e,stroke:#166534
  classDef failure fill:#ef4444,stroke:#7f1d1d
  class A,D success
  class E failure
`;

test('renders a 5-node flowchart and applies classDef styling', () => {
  const { svg, warnings } = renderMermaidToSvg(FLOWCHART_WITH_CLASSDEF, 'default');

  assert.equal(typeof svg, 'string');
  assert.match(svg, /^<svg/);
  assert.deepEqual(warnings, []);

  // beautiful-mermaid resolves `classDef` into inline fill/stroke styles
  // rather than emitting `class="success"` attributes on the SVG nodes (see
  // docs/modules/render-svc.md -- "Known limitations"). The meaningful,
  // renderer-agnostic assertion is therefore that BOTH classDef colors made
  // it into the output: that is the only way #22c55e (success, on A and D)
  // and #ef4444 (failure, on E) could both appear, and it proves the class
  // assignments were resolved correctly rather than ignored or collapsed
  // into a single style.
  assert.ok(svg.includes('22c55e'), 'expected the "success" classDef fill color in the SVG');
  assert.ok(svg.includes('ef4444'), 'expected the "failure" classDef fill color in the SVG');
});

test('rejects a theme name it does not recognize but still renders (documented fallback)', () => {
  const { svg, warnings } = renderMermaidToSvg('flowchart TD\n A-->B', 'not-a-real-theme');
  assert.match(svg, /^<svg/);
  assert.equal(warnings.length, 1);
});

test('checkMermaid flags an invalid diagram with a line number', async () => {
  const badDiagram = 'flowchart TD\n  A[[[ broken --->';
  const result = await checkMermaid(badDiagram);

  assert.equal(result.ok, false);
  assert.ok(Array.isArray(result.errors));
  assert.ok(result.errors.length >= 1);
  assert.equal(typeof result.errors[0].line, 'number');
  assert.equal(typeof result.errors[0].message, 'string');
  assert.ok(result.errors[0].message.length > 0);
});

test('checkMermaid accepts a valid diagram', async () => {
  const result = await checkMermaid('flowchart TD\n  A[Start] --> B[End]');
  assert.equal(result.ok, true);
  assert.deepEqual(result.errors, []);
});

test('checkLatex validates a correct LaTeX string', () => {
  const result = checkLatex('x^2 + y^2 = z^2', { html: true });
  assert.equal(result.ok, true);
  assert.deepEqual(result.errors, []);
  assert.equal(typeof result.html, 'string');
  assert.ok(result.html.includes('katex'));
});

test('checkLatex rejects a malformed LaTeX string', () => {
  const result = checkLatex('\\frac{1}{');
  assert.equal(result.ok, false);
  assert.ok(result.errors.length >= 1);
  assert.equal(typeof result.errors[0].line, 'number');
});

test('checkLatex rejects empty input', () => {
  const result = checkLatex('   ');
  assert.equal(result.ok, false);
  assert.ok(result.errors.length >= 1);
});

// End-to-end: start the real HTTP server on an ephemeral port and hit it,
// to prove the routing/body-parsing/timeout wiring works, not just the
// underlying render/check/latex functions in isolation.
test('HTTP server: /healthz, /render, /check, /latex/check all respond correctly', async () => {
  // src/server.js calls server.listen() as a module side effect at import
  // time (using RENDER_PORT=0 set above, so the OS assigns a free port) --
  // never call .listen() again here, just wait for it to come up if needed.
  const { default: server } = await import('../src/server.js');
  if (!server.listening) {
    await new Promise((resolve) => server.once('listening', resolve));
  }

  const { port } = server.address();
  const base = `http://127.0.0.1:${port}`;

  const healthz = await fetch(`${base}/healthz`);
  assert.equal(healthz.status, 200);
  const healthzBody = await healthz.json();
  assert.equal(healthzBody.ok, true);
  assert.equal(typeof healthzBody.version, 'string');

  const renderRes = await fetch(`${base}/render`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ mermaid: 'flowchart TD\n A-->B', theme: 'default', format: 'svg' }),
  });
  assert.equal(renderRes.status, 200);
  const renderBody = await renderRes.json();
  assert.match(renderBody.svg, /^<svg/);
  assert.deepEqual(renderBody.warnings, []);

  const checkRes = await fetch(`${base}/check`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ mermaid: 'not mermaid at all' }),
  });
  assert.equal(checkRes.status, 200);
  const checkBody = await checkRes.json();
  assert.equal(checkBody.ok, false);
  assert.ok(checkBody.errors.length >= 1);

  const latexRes = await fetch(`${base}/latex/check`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ latex: 'a^2+b^2=c^2' }),
  });
  assert.equal(latexRes.status, 200);
  const latexBody = await latexRes.json();
  assert.equal(latexBody.ok, true);

  server.close();
});
