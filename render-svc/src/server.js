import http from 'node:http';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

import { renderMermaidToSvg } from './mermaidRender.js';
import { checkMermaid } from './mermaidCheck.js';
import { checkLatex } from './latexCheck.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const pkg = JSON.parse(readFileSync(join(__dirname, '..', 'package.json'), 'utf8'));

// Note: `Number(x) || 5035` would silently turn RENDER_PORT=0 (ask the OS
// for an ephemeral port, used by the test suite) into 5035, since 0 is
// falsy. Only fall back to the default when the env var is actually unset.
const PORT =
  process.env.RENDER_PORT !== undefined && process.env.RENDER_PORT !== ''
    ? Number(process.env.RENDER_PORT)
    : 5035;
const HOST = '0.0.0.0';
const MAX_BODY_BYTES = 1 * 1024 * 1024; // 1 MB
const RENDER_TIMEOUT_MS = 20_000;

function sendJson(res, status, body) {
  const payload = JSON.stringify(body);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
  });
  res.end(payload);
}

function readJsonBody(req) {
  return new Promise((resolve, reject) => {
    let received = 0;
    const chunks = [];
    let tooLarge = false;

    req.on('data', (chunk) => {
      received += chunk.length;
      if (received > MAX_BODY_BYTES) {
        // Stop buffering, but deliberately do NOT destroy the socket here.
        // Killing the connection mid-upload (before the client has finished
        // sending) leaves curl and other HTTP/1.1 clients waiting on a
        // broken pipe instead of reading the error response: `req.destroy()`
        // here makes a 2 MB request hang instead of returning 413. Draining
        // the rest of the body and responding normally lets the client
        // close cleanly.
        tooLarge = true;
        return;
      }
      chunks.push(chunk);
    });

    req.on('end', () => {
      if (tooLarge) {
        const err = new Error('request body exceeds 1 MB limit');
        err.statusCode = 413;
        reject(err);
        return;
      }
      if (chunks.length === 0) {
        resolve({});
        return;
      }
      try {
        resolve(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      } catch {
        const err = new Error('invalid JSON body');
        err.statusCode = 400;
        reject(err);
      }
    });

    req.on('error', (err) => {
      err.statusCode = err.statusCode ?? 400;
      reject(err);
    });
  });
}

// Wraps a synchronous or async handler with a hard timeout so a pathological
// diagram can never hang a request past RENDER_TIMEOUT_MS.
function withTimeout(promise, ms, label) {
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const err = new Error(`${label} timed out after ${ms}ms`);
      err.statusCode = 504;
      reject(err);
    }, ms);
  });
  return Promise.race([promise, timeout]).finally(() => clearTimeout(timer));
}

const routes = {
  'POST /render': async (body) => {
    const { mermaid, theme = 'default', format = 'svg' } = body ?? {};
    if (typeof mermaid !== 'string' || mermaid.trim() === '') {
      return { status: 400, body: { error: 'body.mermaid (string) is required' } };
    }
    if (format !== 'svg') {
      return { status: 400, body: { error: 'only format "svg" is supported' } };
    }
    const result = await withTimeout(
      Promise.resolve().then(() => renderMermaidToSvg(mermaid, theme)),
      RENDER_TIMEOUT_MS,
      'render',
    );
    return { status: 200, body: result };
  },

  'POST /check': async (body) => {
    const { mermaid } = body ?? {};
    if (typeof mermaid !== 'string' || mermaid.trim() === '') {
      return { status: 400, body: { error: 'body.mermaid (string) is required' } };
    }
    const result = await withTimeout(checkMermaid(mermaid), RENDER_TIMEOUT_MS, 'check');
    return { status: 200, body: result };
  },

  'POST /latex/check': async (body) => {
    const { latex, html } = body ?? {};
    if (typeof latex !== 'string' || latex.trim() === '') {
      return { status: 400, body: { error: 'body.latex (string) is required' } };
    }
    const result = checkLatex(latex, { html: Boolean(html) });
    return { status: 200, body: result };
  },
};

const server = http.createServer(async (req, res) => {
  const start = process.hrtime.bigint();
  const method = req.method ?? 'GET';
  const url = req.url ?? '/';

  const finish = (status) => {
    const durationMs = Number(process.hrtime.bigint() - start) / 1e6;
    // eslint-disable-next-line no-console
    console.log(
      JSON.stringify({
        ts: new Date().toISOString(),
        method,
        path: url,
        status,
        duration_ms: Math.round(durationMs * 100) / 100,
      }),
    );
  };

  try {
    if (method === 'GET' && url === '/healthz') {
      sendJson(res, 200, { ok: true, version: pkg.version });
      finish(200);
      return;
    }

    const routeKey = `${method} ${url.split('?')[0]}`;
    const handler = routes[routeKey];
    if (!handler) {
      sendJson(res, 404, { error: `no route for ${method} ${url}` });
      finish(404);
      return;
    }

    const body = await readJsonBody(req);
    const { status, body: responseBody } = await handler(body);
    sendJson(res, status, responseBody);
    finish(status);
  } catch (err) {
    const status = err && err.statusCode ? err.statusCode : 500;
    const message = err instanceof Error ? err.message : String(err);
    sendJson(res, status, { error: message });
    finish(status);
  }
});

server.listen(PORT, HOST, () => {
  // eslint-disable-next-line no-console
  console.log(JSON.stringify({ ts: new Date().toISOString(), msg: `render-svc listening on ${HOST}:${PORT}` }));
});

export default server;
