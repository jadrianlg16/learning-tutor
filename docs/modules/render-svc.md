# render-svc

Node service, no headless browser: Mermaid → SVG, Mermaid syntax check, LaTeX (KaTeX)
validation. Per [CONTRACTS.md](../../CONTRACTS.md), this is the one Node container in the
stack — everything else is Python.

Source: `render-svc/src/`. Entry point: `render-svc/src/server.js` (plain `node:http`, no
Express — kept dependency-free since the routing here is 3 endpoints + health).

## Endpoints

### `POST /render`

Body: `{ "mermaid": "...", "theme": "default"|"dark", "format": "svg" }`
Response: `{ "svg": "<svg ...>...</svg>", "warnings": [] }`

```bash
curl -s -X POST http://localhost:5035/render \
  -H "Content-Type: application/json" \
  -d '{
    "mermaid": "flowchart TD\n  A[Start] --> B[Load Data]\n  B --> C{Valid?}\n  C -->|Yes| D[Process]\n  C -->|No| E[Reject]\n  classDef success fill:#22c55e,stroke:#166534\n  classDef failure fill:#ef4444,stroke:#7f1d1d\n  class A,D success\n  class E failure",
    "theme": "default",
    "format": "svg"
  }'
```

### `POST /check`

Body: `{ "mermaid": "..." }` → `{ "ok": true|false, "errors": [{ "line": N, "message": "..." }] }`
Parses only — never renders. This is the primitive behind the tutor's "write → check → fix →
render" loop.

```bash
curl -s -X POST http://localhost:5035/check \
  -H "Content-Type: application/json" \
  -d '{"mermaid": "flowchart TD\n  A[[[ broken --->"}'
# {"ok":false,"errors":[{"line":2,"message":"Parse error on line 2: ... Expecting 'TAGEND', ... got 'SQS'"}]}
```

### `POST /latex/check`

Body: `{ "latex": "...", "html": false }` → `{ "ok": true|false, "errors": [...], "html"?: "..." }`
`html` is optional in the response; pass `"html": true` in the request to get the rendered
KaTeX HTML back (only used for previewing — the tutor never needs it to decide pass/fail).

```bash
curl -s -X POST http://localhost:5035/latex/check \
  -H "Content-Type: application/json" \
  -d '{"latex": "\\frac{1}{"}'
# {"ok":false,"errors":[{"line":1,"message":"KaTeX parse error: ..."}]}
```

### `GET /healthz`

`{ "ok": true, "version": "0.1.0" }` (version comes from `package.json`).

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `RENDER_PORT` | `5035` | Port to bind. `0` asks the OS for an ephemeral port (used by the test suite). |

No other configuration. Binds `0.0.0.0`. Request body cap: 1 MB (413 on overflow). Per-request
timeout: 20 s (504 on overflow, covers `/render` and `/check`; `/latex/check` is synchronous
and effectively instant). One JSON log line per request to stdout:
`{"ts","method","path","status","duration_ms"}`.

## Library decision

Requirement: render Mermaid → SVG **without a headless browser if at all possible**.

| Option | Needs a browser/Chromium? | Verdict |
|---|---|---|
| `@mermaid-js/mermaid-cli` (`mmdc`) | Yes — bundles Puppeteer + Chromium | Rejected. Real mermaid, but the image is enormous (the official `minlag/mermaid-cli` base is 1GB+) for a single-user tool. |
| `mermaid-isomorphic` | Yes outside a browser — its own README states it needs `playwright` + `npx playwright install --with-deps chromium` | Rejected for the same reason; verified by reading its `peerDependencies` (`{"playwright": "1"}`) before installing anything. |
| `beautiful-mermaid` | No — pure JS (`elkjs` for layout), synchronous, "zero DOM dependencies" per its own description | **Chosen for `/render`.** |

`beautiful-mermaid@1.1.3` re-implements flowchart + state-diagram parsing and layout itself
(it is not a wrapper around the real `mermaid` parser). Verified hands-on:
- `parseMermaid()` / `renderMermaidSVG()` run in plain Node with no `window`/`document` at all.
- `classDef` + `class` assignments are parsed into `classAssignments` / `classDefs` and resolved
  into inline `fill`/`stroke` styles on the positioned SVG nodes — see "Known limitations" below
  for what this means for testing.
- Rendering a 5-node flowchart is synchronous and takes low single-digit milliseconds.

For `/check`, `beautiful-mermaid`'s parser turned out to be **too lenient to use as a validator**
(see limitations) — it does not throw on many malformed diagrams (unbalanced brackets, dangling
arrows). Instead, `/check` uses the **real** `mermaid` npm package's own jison-generated parsers
via `mermaid.parse()`. That call touches `DOMPurify.addHook()` internally even when nothing is
being sanitized, which throws in plain Node (`DOMPurify.addHook is not a function`) without a
`window`. The fix, verified hands-on, is a `jsdom` shim (`render-svc/src/mermaidCheck.js`):
`jsdom` is a **pure-JS DOM implementation**, not a headless browser — no rendering engine, no
Chromium, no page JS sandbox, just enough `window`/`document`/`DOMParser` for DOMPurify's setup
code to stop throwing. With that shim, `mermaid.parse()` gives real Mermaid-grammar validation,
including real line numbers in parse errors, at a fraction of the dependency weight of a browser.

Net effect: **no headless browser anywhere in this service.** `/render` uses a from-scratch pure-JS
renderer; `/check` uses the real Mermaid parser behind a lightweight DOM shim.

LaTeX (`/latex/check`) needed no investigation: `katex.renderToString(..., { throwOnError: true })`
is pure JS and does not touch a DOM at all.

## Image size

About 295 MB (`docker images lt-render-svc` after `docker build -t lt-render-svc
./render-svc`): a `node:20-alpine` multi-stage build, `npm ci --omit=dev`, no browser
binaries. For comparison, the official `minlag/mermaid-cli` base is over 1 GB.

## Known limitations

1. **`/check` validates a superset of what `/render` can actually draw.** `/check` uses the
   real Mermaid grammar (jison parsers from the `mermaid` package), which accepts sequence
   diagrams, ER diagrams, gantt charts, etc. `/render` uses `beautiful-mermaid`, which only
   supports flowcharts and state diagrams (it auto-detects between those two and nothing else).
   A diagram that passes `/check` (e.g. a `sequenceDiagram`) can still fail or render oddly at
   `/render`. If the tutor needs other diagram types rendered, this is the thing to revisit
   first — likely by having `/check` restrict itself to what `/render` supports, or by adding a
   fallback renderer.
2. **`beautiful-mermaid`'s own parser is too lenient to double as a validator.** Verified by
   hand: `parseMermaid('flowchart TD\n  A[[[ broken --->')` does **not** throw — it silently
   parses `A` as a node and drops the rest. This is why `/check` does not reuse
   `beautiful-mermaid`'s parser and instead pulls in the real `mermaid` package + a `jsdom` shim
   just for that one endpoint.
3. **`classDef`/`class` styling is resolved, not literal, in the rendered SVG.** `beautiful-mermaid`
   turns `classDef success fill:#22c55e` + `class A,D success` into inline `fill:#22c55e` /
   `stroke:...` styles on nodes A and D — it does **not** emit a `class="success"` attribute
   anywhere in the SVG. `render-svc/test/render.test.js` asserts on the resolved fill colors
   (both classDef colors must appear in the output) rather than on literal class-name text,
   since the latter is never present. Worth knowing if anyone downstream tries to
   `svg.includes('success')` expecting it to work.
4. **`mermaid-isomorphic` and `mermaid-cli` were ruled out on paper, not benchmarked.** Both
   require Playwright/Puppeteer + a Chromium install; that was disqualifying on its own given
   the "no headless browser if at all possible" requirement, so no rendering-quality comparison
   was done. If `beautiful-mermaid`'s flowchart/state-diagram-only coverage becomes a real
   blocker, mermaid-cli behind the official `minlag/mermaid-cli` Chromium image is the planned
   fallback.
5. **No caching.** Every `/render` and `/check` call redoes the work from scratch. Fine at
   single-user scale; revisit if the gateway starts calling this in a tight retry loop.

## Tests

`render-svc/test/render.test.js`, run with `node --test` (no test framework dependency).
Covers: rendering a 5-node flowchart with two `classDef`s (asserts both resolved fill colors
appear — see limitation 3 above), an unrecognized theme name falling back with a warning, a
malformed diagram producing a `{line, message}` error via `/check`'s real-parser path, a valid
diagram passing `/check`, valid and invalid LaTeX via `/latex/check` (including the `html`
option), and an end-to-end pass through the real HTTP server (`/healthz`, `/render`, `/check`,
`/latex/check`) on an OS-assigned port. Eight tests; run them with `npm test` from
`render-svc/`.

## Docker

```
docker build -t lt-render-svc ./render-svc
docker run --rm -p 5035:5035 lt-render-svc
```

Multi-stage `node:20-alpine`, `npm ci --omit=dev` in a separate stage from the runtime image,
runs as a non-root `render` user, `HEALTHCHECK` hits `/healthz` internally. No `RENDER_PORT`
needs to be set for the default; the compose author should map host `5035` → container
`5035` (or set `RENDER_PORT` + republish if another service needs that host port) and does not
need a volume — the service is stateless.

The built image has been exercised from outside the container with `curl`: `/healthz`,
`/render` (an SVG with both classDef colours present), `/check` and `/latex/check` (a valid and
an invalid input each) all respond as documented above.
