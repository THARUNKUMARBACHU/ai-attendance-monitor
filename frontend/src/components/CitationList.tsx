import type { Citation } from '../api/types';
import { humanise, plural } from '../lib/format';
import { lookup } from '../lib/labels';
import { Heading, type HeadingLevel } from './Primitives';

const KIND_LABEL: Readonly<Record<string, string>> = { records: 'Records', document: 'Document' };

export function CitationList({
  citations,
  domId,
  highlighted,
  headingLevel,
}: {
  citations: readonly Citation[];
  /** The element id for a citation, so answer chips can scroll to it. */
  domId: (citationId: string) => string;
  highlighted: string | null;
  headingLevel: HeadingLevel;
}) {
  return (
    <section className="citations">
      <Heading level={headingLevel} className="section-title">
        Sources
      </Heading>
      <ol className="citation-list">
        {citations.map((citation) => {
          const meta = [
            citation.locations,
            typeof citation.record_count === 'number' ? plural(citation.record_count, 'record') : null,
          ].filter(Boolean);
          return (
            <li
              key={citation.id}
              id={domId(citation.id)}
              tabIndex={-1}
              className={`citation${highlighted === citation.id ? ' is-highlighted' : ''}`}
            >
              <div className="citation-head">
                <span className="cite-id">{citation.id}</span>
                <span className="citation-kind">{lookup(KIND_LABEL, citation.kind) ?? humanise(citation.kind)}</span>
                <span className="citation-file">{citation.source_file}</span>
              </div>
              {meta.length > 0 && <p className="citation-meta">{meta.join(' · ')}</p>}
              {citation.snippet && <blockquote className="citation-snippet">{citation.snippet}</blockquote>}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
