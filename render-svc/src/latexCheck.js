// LaTeX validation via KaTeX in throwOnError mode. Pure JS, no DOM needed --
// katex.renderToString builds its own internal tree and never touches a
// browser DOM, so this has nothing to do with the jsdom shim in
// mermaidCheck.js.
import katex from 'katex';

function lineForPosition(source, position) {
  if (typeof position !== 'number' || position < 0) return 1;
  const upTo = source.slice(0, position);
  return upTo.split('\n').length;
}

/**
 * @param {string} latex
 * @param {{ html?: boolean }} [opts]
 * @returns {{ok: boolean, errors: Array<{line: number, message: string}>, html?: string}}
 */
export function checkLatex(latex, opts = {}) {
  if (typeof latex !== 'string' || latex.trim() === '') {
    return { ok: false, errors: [{ line: 1, message: 'latex source is empty' }] };
  }

  try {
    const html = katex.renderToString(latex, { throwOnError: true, strict: 'warn' });
    const result = { ok: true, errors: [] };
    if (opts.html) result.html = html;
    return result;
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    const line = lineForPosition(latex, err && err.position);
    return { ok: false, errors: [{ line, message }] };
  }
}
