'use client';

import { useEffect, useState } from 'react';
import { renderMarkdown, renderMarkdownInline } from '@/lib/markdown';

/**
 * Markdown + KaTeX, sanitised. Rendering happens in an effect because the sanitiser needs
 * a DOM; the raw text is shown as plain text until then, so nothing is ever invisible.
 */
export function Markdown({
  children,
  className,
  inline = false,
}: {
  children: string;
  className?: string;
  inline?: boolean;
}) {
  const [html, setHtml] = useState<string | null>(null);

  useEffect(() => {
    setHtml(inline ? renderMarkdownInline(children) : renderMarkdown(children));
  }, [children, inline]);

  if (inline) {
    if (html === null) return <span>{children}</span>;
    return <span className={className} dangerouslySetInnerHTML={{ __html: html }} />;
  }

  if (html === null) {
    return (
      <div className={className ?? 'prose'}>
        <p style={{ whiteSpace: 'pre-wrap' }}>{children}</p>
      </div>
    );
  }
  return <div className={className ?? 'prose'} dangerouslySetInnerHTML={{ __html: html }} />;
}
