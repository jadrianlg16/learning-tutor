// Mermaid -> SVG rendering.
//
// Library choice: `beautiful-mermaid` (see docs/modules/render-svc.md for the
// full writeup). It re-implements flowchart + state-diagram layout in pure JS
// (elkjs for layout, no DOM) and renders synchronously to an SVG string --
// no headless browser, no Chromium, nothing async beyond the module import.
// The tradeoff: it is a separate, smaller re-implementation of Mermaid's
// grammar, not the real thing, so it does not support every diagram type
// (sequence/ER/gantt/etc.) that the real `mermaid` parser (used by /check)
// accepts. See "Known limitations" in the module doc.
import { renderMermaidSVG, THEMES } from 'beautiful-mermaid';

const THEME_COLORS = {
  default: { bg: '#FFFFFF', fg: '#27272A' },
  dark: { bg: '#1E1E2E', fg: '#E4E4E7' },
};

/**
 * @param {string} mermaidSource
 * @param {'default'|'dark'} theme
 * @returns {{svg: string, warnings: string[]}}
 */
export function renderMermaidToSvg(mermaidSource, theme = 'default') {
  const warnings = [];
  let colors = THEME_COLORS[theme];
  if (!colors) {
    warnings.push(`unknown theme "${theme}", falling back to "default"`);
    colors = THEME_COLORS.default;
  }

  const svg = renderMermaidSVG(mermaidSource, colors);
  return { svg, warnings };
}

// Exported for reference/tests: the library's own named theme palettes.
export const NAMED_THEMES = THEMES;
