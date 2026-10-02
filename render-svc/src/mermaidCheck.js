// Mermaid syntax validation, parse-only (no SVG rendering).
//
// Why this needs jsdom: the real `mermaid` package's parser (jison-generated,
// per diagram type) does not itself touch the DOM, but `mermaid.parse()` runs
// DOMPurify config setup on the way in, and DOMPurify calls `window`-only APIs
// even when never asked to sanitize anything. jsdom is a pure-JS DOM
// *implementation* (no rendering engine, no network, no JS sandbox for pages)
// -- it is not a headless browser like Puppeteer/Chromium/Playwright, and it
// is only used here, for /check. /render never loads it.
//
// This buys real Mermaid grammar validation (the same jison parsers mermaid
// itself uses) without pulling in Chromium.
import { JSDOM } from 'jsdom';

let mermaidPromise;

function ensureDom() {
  if (typeof globalThis.window === 'undefined') {
    const dom = new JSDOM('<!DOCTYPE html><html><body></body></html>');
    globalThis.window = dom.window;
    globalThis.document = dom.window.document;
    globalThis.navigator = dom.window.navigator;
    globalThis.DOMParser = dom.window.DOMParser;
  }
}

async function getMermaid() {
  if (!mermaidPromise) {
    ensureDom();
    mermaidPromise = import('mermaid').then((m) => m.default);
  }
  return mermaidPromise;
}

// Pull a 1-based line number out of a jison "Parse error on line N:" message.
// Falls back to line 1 (e.g. header-detection errors have no line number).
function extractLine(message) {
  const match = /line (\d+)/i.exec(message ?? '');
  if (match) return Number(match[1]);
  return 1;
}

/**
 * @param {string} mermaid - Mermaid source text.
 * @returns {Promise<{ok: boolean, errors: Array<{line: number, message: string}>}>}
 */
export async function checkMermaid(mermaidSource) {
  if (typeof mermaidSource !== 'string' || mermaidSource.trim() === '') {
    return { ok: false, errors: [{ line: 1, message: 'mermaid source is empty' }] };
  }

  const mermaidLib = await getMermaid();
  try {
    await mermaidLib.parse(mermaidSource, { suppressErrors: false });
    return { ok: true, errors: [] };
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return { ok: false, errors: [{ line: extractLine(message), message }] };
  }
}
