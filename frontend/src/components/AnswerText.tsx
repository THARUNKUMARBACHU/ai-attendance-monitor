import type { ReactNode } from 'react';
import type { Citation } from '../api/types';

// [D1], [S2], or a group such as [D1, S2].
const MARKER = /\[([A-Z]{1,3}\d{1,4}(?:\s*,\s*[A-Z]{1,3}\d{1,4})*)\]/g;

/**
 * Answer text with citation markers turned into chips. The text is split into React nodes;
 * it is never interpreted as HTML. Markers without a matching citation stay as plain text.
 */
export function AnswerText({
  text,
  citations,
  onCite,
}: {
  text: string;
  citations: readonly Citation[];
  onCite: (id: string) => void;
}) {
  const byId = new Map(citations.map((citation) => [citation.id, citation]));
  const nodes: ReactNode[] = [];
  let cursor = 0;

  for (const match of text.matchAll(MARKER)) {
    const ids = (match[1] ?? '').split(/\s*,\s*/);
    if (!ids.every((id) => byId.has(id))) continue;
    const start = match.index;
    if (start > cursor) nodes.push(text.slice(cursor, start));
    for (const id of ids) {
      const source = byId.get(id)?.source_file ?? '';
      nodes.push(
        <button
          key={`${start}-${id}`}
          type="button"
          className="cite-chip"
          title={source}
          aria-label={`Source ${id}: ${source}`}
          onClick={() => onCite(id)}
        >
          {id}
        </button>,
      );
    }
    cursor = start + match[0].length;
  }
  if (cursor < text.length) nodes.push(text.slice(cursor));

  return <div className="answer-text">{nodes}</div>;
}
