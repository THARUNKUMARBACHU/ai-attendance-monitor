import { useEffect, useId, useState } from 'react';
import type { Me, QueryResponse } from '../api/types';
import { formatClock, formatDate, humanise } from '../lib/format';
import { prefersReducedMotion } from '../lib/hooks';
import { describe, lookup, OUTCOME, REASON } from '../lib/labels';
import { AnswerText } from './AnswerText';
import { CitationList } from './CitationList';
import { ConfidenceMeter } from './ConfidenceMeter';
import { CorrectionForm } from './CorrectionForm';
import { ExportButtons } from './ExportButtons';
import { HowAnswered } from './HowAnswered';
import { Heading } from './Primitives';
import { Pill } from './StatusPill';

export interface AskedQuestion {
  id: number;
  question: string;
  asOf: string | null;
  askedAt: Date;
  response: QueryResponse;
}

export function AnswerCard({ entry, me, headingLevel }: { entry: AskedQuestion; me: Me; headingLevel: 2 | 3 }) {
  const { response } = entry;
  const uid = useId();
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const citations = Array.isArray(response.citations) ? response.citations : [];
  const outcome = describe(OUTCOME, response.outcome);
  const reason = response.reason_code ? (lookup(REASON, response.reason_code) ?? humanise(response.reason_code)) : null;
  const sectionLevel = headingLevel === 2 ? 3 : 4;
  const citationDomId = (citationId: string) => `${uid}-source-${citationId}`;
  const applied = Array.isArray(response.feedback_applied) ? response.feedback_applied : [];
  const canExport = me.permissions.includes('export') && response.outcome !== 'denied';
  const canCorrect =
    me.permissions.includes('feedback:submit') &&
    (response.outcome === 'answered' || response.outcome === 'needs_review');

  useEffect(() => {
    if (!highlighted) return undefined;
    const timer = window.setTimeout(() => setHighlighted(null), 2400);
    return () => window.clearTimeout(timer);
  }, [highlighted]);

  function showCitation(citationId: string) {
    const node = document.getElementById(citationDomId(citationId));
    if (node) {
      node.scrollIntoView({ block: 'nearest', behavior: prefersReducedMotion() ? 'auto' : 'smooth' });
      node.focus({ preventScroll: true });
    }
    setHighlighted(citationId);
  }

  return (
    <article className={`answer answer--${outcome.tone}`} aria-labelledby={`${uid}-question`}>
      <div className="answer-meta">
        <Pill tone={outcome.tone}>{outcome.label}</Pill>
        {response.cached && <span className="tag">Cached</span>}
        <span className="answer-time">
          {entry.asOf ? `As of ${formatDate(entry.asOf)} · ` : ''}
          Asked {formatClock(entry.askedAt)}
        </span>
      </div>
      <Heading level={headingLevel} id={`${uid}-question`} className="answer-question">
        {entry.question}
      </Heading>

      {response.message && (
        <div className={`callout callout--${outcome.tone}`}>
          <p>{response.message}</p>
          {reason && <p className="callout-reason">Reason: {reason}</p>}
        </div>
      )}
      {!response.message && !response.answer && reason && (
        <div className={`callout callout--${outcome.tone}`}>
          <p>{reason}</p>
        </div>
      )}

      {response.answer && <AnswerText text={response.answer} citations={citations} onCite={showCitation} />}
      {applied.length > 0 && (
        <p className="applied-note">
          <Pill tone="info">Reviewer-approved</Pill>
          This answer follows an approved correction for an equivalent question (knowledge version{' '}
          {response.versions.knowledge}).
        </p>
      )}
      {response.confidence && <ConfidenceMeter confidence={response.confidence} />}
      {citations.length > 0 && (
        <CitationList
          citations={citations}
          domId={citationDomId}
          highlighted={highlighted}
          headingLevel={sectionLevel}
        />
      )}
      <HowAnswered response={response} me={me} headingLevel={sectionLevel} />
      {canExport && (
        <div className="answer-actions">
          <ExportButtons
            label="Export this answer with its evidence"
            request={(format) => ({ format, request_id: response.request_id })}
          />
        </div>
      )}
      {canCorrect && <CorrectionForm response={response} />}
    </article>
  );
}
