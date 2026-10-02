/**
 * Markdown + LaTeX rendering for teach steps, feedback and `learner.md`.
 *
 * Pipeline: pull math out first (so `marked` never mangles `_` or `*` inside TeX),
 * render markdown, render each math island with KaTeX, then run a small allow-list
 * sanitiser over the result. Gateway text originates from an LLM and from corpus
 * documents - CONTRACTS.md hard rule 4 says corpus text is data, so it is never
 * trusted to carry markup.
 */
import { marked } from 'marked';
import katex from 'katex';

interface MathIsland {
  tex: string;
  display: boolean;
}

// Alphanumeric on purpose: markdown gives it no meaning, so marked will not trim,
// escape or wrap it the way it would a token containing punctuation.
const PLACEHOLDER = (i: number) => `LTMATHZ${i}Z`;

/** Extract `$$...$$`, `\[...\]`, `$...$` and `\(...\)` into placeholders. */
function extractMath(src: string): { text: string; islands: MathIsland[] } {
  const islands: MathIsland[] = [];
  let text = src;

  const patterns: Array<{ re: RegExp; display: boolean }> = [
    { re: /\$\$([\s\S]+?)\$\$/g, display: true },
    { re: /\\\[([\s\S]+?)\\\]/g, display: true },
    { re: /\\\(([\s\S]+?)\\\)/g, display: false },
    // Single `$` needs a non-space first/last char so a lone currency sign survives.
    { re: /(?<!\\)\$(?!\s)((?:[^$\\\n]|\\.)+?)(?<!\s)\$/g, display: false },
  ];

  for (const { re, display } of patterns) {
    text = text.replace(re, (_m, tex: string) => {
      islands.push({ tex, display });
      return PLACEHOLDER(islands.length - 1);
    });
  }

  return { text, islands };
}

function renderMath(island: MathIsland): string {
  try {
    return katex.renderToString(island.tex, {
      displayMode: island.display,
      throwOnError: false,
      output: 'html',
      strict: 'ignore',
    });
  } catch {
    const tag = island.display ? 'pre' : 'code';
    return `<${tag} class="tex-error">${escapeHtml(island.tex)}</${tag}>`;
  }
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

const ALLOWED_TAGS = new Set([
  'A', 'ABBR', 'B', 'BLOCKQUOTE', 'BR', 'CODE', 'DD', 'DEL', 'DIV', 'DL', 'DT', 'EM',
  'FIGCAPTION', 'FIGURE', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6', 'HR', 'I', 'IMG', 'LI',
  'OL', 'P', 'PRE', 'S', 'SPAN', 'STRONG', 'SUB', 'SUP', 'TABLE', 'TBODY', 'TD',
  'TFOOT', 'TH', 'THEAD', 'TR', 'UL',
  // KaTeX HTML output + the MathML it emits alongside it
  'MATH', 'SEMANTICS', 'MROW', 'MI', 'MO', 'MN', 'MSUP', 'MSUB', 'MSUBSUP', 'MFRAC',
  'MSQRT', 'MROOT', 'MTEXT', 'MSPACE', 'MTABLE', 'MTR', 'MTD', 'MSTYLE', 'MPADDED',
  'MOVER', 'MUNDER', 'MUNDEROVER', 'ANNOTATION', 'MENCLOSE', 'MPHANTOM',
]);

const ALLOWED_ATTRS = new Set([
  'href', 'title', 'alt', 'src', 'class', 'style', 'colspan', 'rowspan', 'aria-hidden',
  'width', 'height', 'mathvariant', 'stretchy', 'encoding', 'display', 'separator',
  'scriptlevel', 'displaystyle', 'fence', 'accent', 'lspace', 'rspace', 'depth', 'voffset',
]);

/**
 * Allow-list sanitiser. Uses the browser DOM, so on the server (static prerender) it
 * returns the empty string and the component renders after hydration instead.
 */
function sanitize(html: string): string {
  if (typeof window === 'undefined' || typeof window.DOMParser === 'undefined') return '';
  const doc = new window.DOMParser().parseFromString(`<body>${html}</body>`, 'text/html');

  const walk = (el: Element) => {
    for (const child of Array.from(el.children)) walk(child);
    if (!ALLOWED_TAGS.has(el.tagName.toUpperCase())) {
      el.replaceWith(...Array.from(el.childNodes));
      return;
    }
    for (const attr of Array.from(el.attributes)) {
      const name = attr.name.toLowerCase();
      const bad =
        !ALLOWED_ATTRS.has(name) ||
        name.startsWith('on') ||
        (/^(href|src)$/.test(name) && /^\s*(javascript|data|vbscript):/i.test(attr.value));
      if (bad) el.removeAttribute(attr.name);
    }
    if (el.tagName === 'A') {
      el.setAttribute('rel', 'noopener noreferrer');
      el.setAttribute('target', '_blank');
    }
  };

  for (const child of Array.from(doc.body.children)) walk(child);
  return doc.body.innerHTML;
}

/** Markdown (+ LaTeX) to sanitised HTML. Returns '' during server prerender. */
export function renderMarkdown(src: string): string {
  if (!src) return '';
  const { text, islands } = extractMath(src);
  let html = marked.parse(text, { async: false, gfm: true, breaks: false }) as string;
  html = html.replace(/LTMATHZ(\d+)Z/g, (_m, i: string) => renderMath(islands[Number(i)]));
  return sanitize(html);
}

/**
 * Inline markdown (+ LaTeX) - no block wrapper. Used for answer options, where the text is
 * a phrase, not a paragraph, but still carries `$...$` maths.
 */
export function renderMarkdownInline(src: string): string {
  if (!src) return '';
  const { text, islands } = extractMath(src);
  let html = marked.parseInline(text, { async: false, gfm: true }) as string;
  html = html.replace(/LTMATHZ(\d+)Z/g, (_m, i: string) => renderMath(islands[Number(i)]));
  return sanitize(html);
}
