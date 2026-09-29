import { useEffect, useId, useState } from 'react';
import { api } from '../api/client';
import type { ExampleStatus, FeedbackExample, Me } from '../api/types';
import { Skeleton, Spinner } from '../components/Primitives';
import { StatusPill } from '../components/StatusPill';
import { isSilentError } from '../lib/errors';
import { departmentName, EMPTY, formatDateTime, shortId } from '../lib/format';
import { useFeedback } from '../lib/feedback';
import { useFocusWhenActive } from '../lib/hooks';
import { EXAMPLE_STATUS } from '../lib/labels';

const STATUSES: readonly ExampleStatus[] = ['active', 'inactive', 'rejected'];

function scopeText(example: FeedbackExample, me: Me): string {
  const { scope } = example;
  if (scope.scope === 'tenant') return 'Whole organisation';
  if (scope.scope === 'department') return scope.entity_scope.map((id) => departmentName(me.departments, id)).join(', ');
  return `Own records of ${scope.employee_id ?? 'one employee'}`;
}

/** Approved corrections (the feedback/training loop): what applies where, and a one-click rollback. */
export function FeedbackView({ me, active }: { me: Me; active: boolean }) {
  const { showError, announce } = useFeedback();
  const uid = useId();
  const headingRef = useFocusWhenActive<HTMLHeadingElement>(active);
  const [status, setStatus] = useState<ExampleStatus | ''>('');
  const [items, setItems] = useState<FeedbackExample[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [reload, setReload] = useState(0);
  const [confirming, setConfirming] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);

  useEffect(() => {
    if (!active) return undefined;
    const controller = new AbortController();
    setLoading(true);
    api
      .feedbackExamples(status || null, controller.signal)
      .then((data) => {
        if (!controller.signal.aborted) setItems(Array.isArray(data.items) ? data.items : []);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isSilentError(error)) showError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [active, status, reload, showError]);

  async function deactivate(example: FeedbackExample) {
    if (confirming !== example.example_id) {
      setConfirming(example.example_id);
      return;
    }
    setWorking(example.example_id);
    try {
      const updated = await api.deactivateFeedback(example.example_id);
      setItems((list) => list?.map((item) => (item.example_id === updated.example_id ? updated : item)) ?? null);
      announce(updated.message ?? 'The example was deactivated.');
    } catch (error) {
      if (!isSilentError(error)) showError(error);
    } finally {
      setWorking(null);
      setConfirming(null);
    }
  }

  return (
    <section className="view" hidden={!active} aria-labelledby={`${uid}-title`}>
      <header className="view-head">
        <h1 id={`${uid}-title`} ref={headingRef} tabIndex={-1}>
          Feedback
        </h1>
        <p className="view-intro">
          Reviewer-approved corrections. An active one guides equivalent questions asked in exactly the same access
          scope; deactivating it rolls it back from the next knowledge version. Correct an answer from the Ask page.
        </p>
      </header>

      <section className="card" aria-labelledby={`${uid}-list`}>
        <div className="card-head">
          <h2 id={`${uid}-list`} className="card-title">
            Examples
          </h2>
          <div className="card-head-actions">
            {loading && items && (
              <span className="muted small state-line">
                <Spinner /> Loading…
              </span>
            )}
            <label className="visually-hidden" htmlFor={`${uid}-status`}>
              Status
            </label>
            <select
              id={`${uid}-status`}
              className="select select--small"
              value={status}
              onChange={(event) => setStatus(STATUSES.find((value) => value === event.target.value) ?? '')}
            >
              <option value="">All statuses</option>
              {STATUSES.map((value) => (
                <option key={value} value={value}>
                  {EXAMPLE_STATUS[value].label}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn--small" onClick={() => setReload((n) => n + 1)} disabled={loading}>
              Refresh
            </button>
          </div>
        </div>

        <div aria-busy={loading}>
          {!items && <Skeleton lines={4} />}
          {items && items.length === 0 && <p className="empty">No corrections yet.</p>}
          {items && items.length > 0 && (
            <div className="table-wrap" role="region" aria-labelledby={`${uid}-list`} tabIndex={0}>
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Status</th>
                    <th scope="col">Question and ideal answer</th>
                    <th scope="col">Scope</th>
                    <th scope="col" className="num">
                      Version
                    </th>
                    <th scope="col">Reviewer</th>
                    <th scope="col">Created</th>
                    <th scope="col">
                      <span className="visually-hidden">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((example) => (
                    <tr key={example.example_id}>
                      <td>
                        <StatusPill map={EXAMPLE_STATUS} value={example.status} />
                        <span className="cell-sub mono">{shortId(example.example_id)}</span>
                      </td>
                      <td className="cell-feedback">
                        <span className="cell-main">{example.question}</span>
                        <span className="cell-sub">{example.ideal_output}</span>
                        {example.problems.length > 0 && (
                          <span className="cell-sub cell-problems">{example.problems.join(' ')}</span>
                        )}
                      </td>
                      <td>{scopeText(example, me)}</td>
                      <td className="num">{example.version}</td>
                      <td>{example.reviewer_id}</td>
                      <td className="nowrap">
                        {formatDateTime(example.created_at)}
                        {example.deactivated_at && (
                          <span className="cell-sub">Deactivated {formatDateTime(example.deactivated_at)}</span>
                        )}
                      </td>
                      <td className="nowrap">
                        {example.status === 'active' ? (
                          <button
                            type="button"
                            className={confirming === example.example_id ? 'btn btn--small btn--danger' : 'btn btn--small'}
                            disabled={working !== null}
                            onClick={() => void deactivate(example)}
                            onBlur={() => setConfirming((id) => (id === example.example_id ? null : id))}
                          >
                            {working === example.example_id && <Spinner />}
                            {confirming === example.example_id ? 'Confirm deactivate' : 'Deactivate'}
                          </button>
                        ) : (
                          EMPTY
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </section>
    </section>
  );
}
