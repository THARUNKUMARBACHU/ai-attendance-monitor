import { useEffect, useId, useRef, useState, type FormEvent, type KeyboardEvent } from 'react';
import { api } from '../api/client';
import type { Me, QueryRequest } from '../api/types';
import { AnswerCard, type AskedQuestion } from '../components/AnswerCard';
import { Skeleton, Spinner } from '../components/Primitives';
import { StatusPill } from '../components/StatusPill';
import { formatClock } from '../lib/format';
import { useFeedback } from '../lib/feedback';
import { useFocusWhenActive } from '../lib/hooks';
import { describe, EXAMPLE_QUESTIONS, lookup, OUTCOME } from '../lib/labels';

export function AskView({ me, active }: { me: Me; active: boolean }) {
  const { showError, clearError, announce } = useFeedback();
  const uid = useId();
  const headingRef = useFocusWhenActive<HTMLHeadingElement>(active);
  const formRef = useRef<HTMLFormElement>(null);
  const questionRef = useRef<HTMLTextAreaElement>(null);
  const nextId = useRef(1);

  const [question, setQuestion] = useState('');
  const [asOf, setAsOf] = useState('');
  const [pending, setPending] = useState<string | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [history, setHistory] = useState<AskedQuestion[]>([]);

  const busy = pending !== null;
  const examples = lookup(EXAMPLE_QUESTIONS, me.role) ?? [];
  // While a question is in flight the previous answer moves into the history list.
  const current = busy ? null : (history[0] ?? null);
  const earlier = busy ? history : history.slice(1);

  useEffect(() => {
    if (!busy) return undefined;
    const started = Date.now();
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => window.clearInterval(timer);
  }, [busy]);

  async function ask(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const text = question.trim();
    if (busy) return;
    if (!text) {
      questionRef.current?.focus();
      return;
    }
    const body: QueryRequest = asOf ? { question: text, as_of: asOf } : { question: text };
    setPending(text);
    setElapsed(0);
    clearError();
    announce('Working on your question. This can take up to 20 seconds.');
    try {
      const response = await api.query(body);
      const entry: AskedQuestion = { id: nextId.current++, question: text, asOf: body.as_of ?? null, askedAt: new Date(), response };
      setHistory((items) => [entry, ...items]);
      const outcome = describe(OUTCOME, response.outcome).label;
      announce(response.outcome === 'answered' ? 'Answer ready.' : `${outcome}. ${response.message ?? ''}`.trim());
    } catch (error) {
      showError(error);
    } finally {
      setPending(null);
    }
  }

  function onQuestionKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  function applyExample(example: string) {
    setQuestion(example);
    questionRef.current?.focus();
  }

  return (
    <section className="view" hidden={!active} aria-labelledby={`${uid}-title`}>
      <header className="view-head">
        <h1 id={`${uid}-title`} ref={headingRef} tabIndex={-1}>
          Ask
        </h1>
        <p className="view-intro">
          Ask about attendance in plain English. Answers come from the records and documents you are allowed to see,
          with their sources and a confidence score.
        </p>
      </header>

      <form ref={formRef} className="card ask-form" onSubmit={(event) => void ask(event)}>
        <label htmlFor={`${uid}-question`} className="field-label">
          Your question
        </label>
        <textarea
          id={`${uid}-question`}
          ref={questionRef}
          className="textarea"
          rows={3}
          value={question}
          placeholder="Ask about attendance…"
          required
          aria-describedby={`${uid}-hint`}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={onQuestionKeyDown}
        />

        {examples.length > 0 && (
          <div className="examples" role="group" aria-labelledby={`${uid}-examples`}>
            <span id={`${uid}-examples`} className="examples-label">
              Try
            </span>
            {examples.map((example) => (
              <button key={example} type="button" className="chip" onClick={() => applyExample(example)}>
                {example}
              </button>
            ))}
          </div>
        )}

        <div className="ask-actions">
          <div className="field field--inline">
            <label htmlFor={`${uid}-as-of`} className="field-label">
              As of <span className="optional">(optional)</span>
            </label>
            <input
              id={`${uid}-as-of`}
              type="date"
              className="input"
              value={asOf}
              onChange={(event) => setAsOf(event.target.value)}
            />
          </div>
          <p id={`${uid}-hint`} className="hint">
            <kbd>Ctrl</kbd> + <kbd>Enter</kbd> to ask
          </p>
          <button type="submit" className="btn btn--primary" disabled={busy}>
            {busy ? (
              <>
                <Spinner /> Asking…
              </>
            ) : (
              'Ask'
            )}
          </button>
        </div>
      </form>

      <div className="answer-region" aria-busy={busy}>
        {pending !== null && (
          <article className="answer answer--pending" aria-label="Answer in progress">
            <p className="answer-question">{pending}</p>
            <p className="pending-note">
              <Spinner /> Searching records and documents{elapsed > 0 ? ` · ${elapsed}s` : '…'}
            </p>
            <Skeleton lines={3} />
          </article>
        )}
        {current && <AnswerCard entry={current} me={me} headingLevel={2} />}
        {!busy && history.length === 0 && (
          <p className="empty">Answers appear here with their sources, a confidence score and how they were computed.</p>
        )}
      </div>

      {earlier.length > 0 && (
        <section className="history" aria-labelledby={`${uid}-history`}>
          <h2 id={`${uid}-history`} className="section-heading">
            Earlier in this session <span className="count">{earlier.length}</span>
          </h2>
          <ol className="history-list">
            {earlier.map((entry) => (
              <li key={entry.id}>
                <details className="history-item">
                  <summary>
                    <StatusPill map={OUTCOME} value={entry.response.outcome} />
                    <span className="history-question">{entry.question}</span>
                    <time className="history-time" dateTime={entry.askedAt.toISOString()}>
                      {formatClock(entry.askedAt)}
                    </time>
                  </summary>
                  <AnswerCard entry={entry} me={me} headingLevel={3} />
                </details>
              </li>
            ))}
          </ol>
        </section>
      )}
    </section>
  );
}
