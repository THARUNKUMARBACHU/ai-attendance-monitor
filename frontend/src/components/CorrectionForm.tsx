import { useId, useState, type FormEvent } from 'react';
import { api } from '../api/client';
import type { FeedbackExample, FeedbackRequest, QueryResponse } from '../api/types';
import { isSilentError } from '../lib/errors';
import { useFeedback } from '../lib/feedback';
import { formatNumber, shortId } from '../lib/format';
import { EXAMPLE_STATUS } from '../lib/labels';
import { KeyValue, Spinner } from './Primitives';
import { StatusPill } from './StatusPill';

/**
 * Lets a reviewer correct an answer. The server validates the correction, replays this question with it,
 * and activates it only if the replayed answer holds up against the data. It then applies to equivalent
 * questions asked in the same access scope, until it is deactivated on the Feedback page.
 */
export function CorrectionForm({ response }: { response: QueryResponse }) {
  const uid = useId();
  const { showError, announce } = useFeedback();
  const [feedback, setFeedback] = useState('');
  const [ideal, setIdeal] = useState(response.answer ?? '');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FeedbackExample | null>(null);
  const ready = feedback.trim().length >= 3 && ideal.trim().length >= 3;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ready) return;
    setBusy(true);
    const body: FeedbackRequest = {
      request_id: response.request_id,
      feedback: feedback.trim(),
      ideal_final_output: ideal.trim(),
    };
    if (note.trim()) body.approval_note = note.trim();
    try {
      const example = await api.submitFeedback(body);
      setResult(example);
      announce(example.status === 'active' ? 'The correction is active.' : 'The correction was rejected.');
    } catch (error) {
      if (!isSilentError(error)) showError(error);
    } finally {
      setBusy(false);
    }
  }

  return (
    <details className="correction">
      <summary>Improve this answer</summary>
      <form className="correction-form" onSubmit={submit}>
        <p className="hint">
          Your correction is checked, replayed on this question and, if it holds up against the data, used for
          equivalent questions in the same access scope. It can be rolled back on the Feedback page.
        </p>
        <div className="field">
          <label htmlFor={`${uid}-feedback`} className="field-label">
            What was wrong or missing?
          </label>
          <textarea
            id={`${uid}-feedback`}
            className="textarea"
            rows={2}
            required
            minLength={3}
            maxLength={2000}
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
        </div>
        <div className="field">
          <label htmlFor={`${uid}-ideal`} className="field-label">
            Ideal answer
          </label>
          <textarea
            id={`${uid}-ideal`}
            className="textarea"
            rows={4}
            required
            minLength={3}
            maxLength={4000}
            value={ideal}
            onChange={(event) => setIdeal(event.target.value)}
            aria-describedby={`${uid}-ideal-hint`}
          />
          <p id={`${uid}-ideal-hint`} className="hint">
            Use only facts from the data: every number is checked against it.
          </p>
        </div>
        <div className="field">
          <label htmlFor={`${uid}-note`} className="field-label">
            Approval note <span className="optional">(optional)</span>
          </label>
          <input
            id={`${uid}-note`}
            className="input"
            maxLength={500}
            value={note}
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        <div className="button-row button-row--end">
          <button type="submit" className="btn btn--primary" disabled={busy || !ready}>
            {busy && <Spinner />}
            {busy ? 'Verifying…' : 'Submit and verify'}
          </button>
        </div>
      </form>
      {result && <CorrectionResult example={result} />}
    </details>
  );
}

function CorrectionResult({ example }: { example: FeedbackExample }) {
  const active = example.status === 'active';
  return (
    <div className={`callout callout--${active ? 'good' : 'critical'} correction-result`} role="status">
      <p className="callout-title">
        <StatusPill map={EXAMPLE_STATUS} value={example.status} /> {example.message}
      </p>
      <dl className="kv kv--grid">
        <KeyValue label="Example">
          <span className="mono">{shortId(example.example_id)}</span>
        </KeyValue>
        <KeyValue label="Knowledge version">{example.knowledge_version ?? example.version}</KeyValue>
        <KeyValue label="Scope">
          {example.scope.scope === 'tenant' ? 'Whole organisation' : example.scope.entity_scope.join(', ') || 'Own records'}
        </KeyValue>
        {example.replay && (
          <KeyValue label="Replay similarity">{formatNumber(example.replay.similarity * 100, 0)}%</KeyValue>
        )}
      </dl>
      {example.problems.length > 0 && (
        <ul className="problem-issues">
          {example.problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
