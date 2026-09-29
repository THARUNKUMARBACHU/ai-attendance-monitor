import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { api } from '../api/client';
import type { Department, JobDetail, JobStage } from '../api/types';
import { describeError, isSilentError, type ErrorInfo } from '../lib/errors';
import {
  departmentName,
  durationBetween,
  EMPTY,
  formatBytes,
  formatDateTime,
  formatNumber,
  humanise,
} from '../lib/format';
import { usePageVisible, usePolling } from '../lib/hooks';
import {
  ACTIVE_JOB_STATUSES,
  COUNT_FIELDS,
  describe,
  JOB_STATUS,
  lookup,
  STAGE_LABEL,
  STAGE_ORDER,
  STAGE_STATUS,
} from '../lib/labels';
import { ProblemSummary } from './ErrorBanner';
import { KeyValue, Skeleton } from './Primitives';
import { Pill, StatusPill } from './StatusPill';

const POLL_MS = 2000;
const FAILURE_ROW_LIMIT = 200;

type DetailState = { status: 'loading' } | { status: 'ready'; job: JobDetail } | { status: 'error'; error: ErrorInfo };

function sortStages(stages: readonly JobStage[] | undefined): JobStage[] {
  const rank = (name: string) => {
    const index = STAGE_ORDER.findIndex((stage) => stage === name);
    return index === -1 ? STAGE_ORDER.length : index;
  };
  return [...(stages ?? [])].sort((a, b) => rank(a.name) - rank(b.name));
}

function StageTimeline({ stages }: { stages: readonly JobStage[] }) {
  if (stages.length === 0) return <p className="muted">No stage information yet.</p>;
  return (
    <ol className="timeline">
      {stages.map((stage) => {
        const status = describe(STAGE_STATUS, stage.status);
        const took = durationBetween(stage.started_at, stage.finished_at);
        return (
          <li key={stage.name} className={`timeline-item timeline-item--${status.tone}`}>
            <div className="timeline-head">
              <span className="timeline-name">{lookup(STAGE_LABEL, stage.name) ?? humanise(stage.name)}</span>
              <Pill tone={status.tone}>{status.label}</Pill>
              {took && <span className="muted small">{took}</span>}
            </div>
            {stage.detail && <p className="timeline-detail">{stage.detail}</p>}
          </li>
        );
      })}
    </ol>
  );
}

function JobBody({ job, departments }: { job: JobDetail; departments: readonly Department[] }) {
  const counts = job.counts ?? {};
  const failures = Array.isArray(job.failures) ? job.failures : [];

  return (
    <div className="job-body">
      <div className="job-title-row">
        <p className="job-file">{job.filename}</p>
        <span className="tag">v{job.version_no}</span>
        <StatusPill map={JOB_STATUS} value={job.status} />
      </div>

      {job.last_error && (
        <div className="callout callout--critical">
          <p className="callout-title">Last error</p>
          <pre className="error-text">{job.last_error}</pre>
        </div>
      )}

      <div className="detail-grid">
        <section className="detail-section">
          <h3 className="section-title">Stages</h3>
          <StageTimeline stages={sortStages(job.stages)} />
        </section>
        <section className="detail-section">
          <h3 className="section-title">Counts</h3>
          <dl className="stat-grid">
            {COUNT_FIELDS.map(({ key, label, flag }) => {
              const value = counts[key];
              const flagged = flag !== undefined && typeof value === 'number' && value > 0;
              return (
                <div key={key} className={flagged ? `stat stat--${flag}` : 'stat'}>
                  <dt>{label}</dt>
                  <dd>{formatNumber(value, 0)}</dd>
                </div>
              );
            })}
          </dl>
        </section>
      </div>

      <section className="detail-section">
        <h3 className="section-title">
          Failures {failures.length > 0 && <span className="count">{failures.length}</span>}
        </h3>
        {failures.length === 0 ? (
          <p className="muted">No row-level failures.</p>
        ) : (
          <>
            <div className="table-wrap" role="region" aria-label="Failures" tabIndex={0}>
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th scope="col">Location</th>
                    <th scope="col">Reason</th>
                  </tr>
                </thead>
                <tbody>
                  {failures.slice(0, FAILURE_ROW_LIMIT).map((failure, index) => (
                    <tr key={index}>
                      <td className="nowrap">{failure.location}</td>
                      <td>{failure.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {failures.length > FAILURE_ROW_LIMIT && (
              <p className="muted small">
                Showing the first {FAILURE_ROW_LIMIT} of {formatNumber(failures.length, 0)} failures.
              </p>
            )}
          </>
        )}
      </section>

      <section className="detail-section">
        <h3 className="section-title">File</h3>
        <dl className="kv kv--grid">
          <KeyValue label="Job ID">
            <code className="break">{job.job_id}</code>
          </KeyValue>
          <KeyValue label="Department">
            {job.entity_id ? departmentName(departments, job.entity_id) : 'Not department-specific'}
          </KeyValue>
          <KeyValue label="Media type">{job.media_type || EMPTY}</KeyValue>
          <KeyValue label="Size">{formatBytes(job.size_bytes)}</KeyValue>
          <KeyValue label="Checksum">
            <code className="break">{job.checksum || EMPTY}</code>
          </KeyValue>
          <KeyValue label="Attempts">
            {typeof job.attempts === 'number' ? `${job.attempts} of ${job.max_attempts}` : EMPTY}
          </KeyValue>
          <KeyValue label="Created">{formatDateTime(job.created_at)}</KeyValue>
          <KeyValue label="Started">{formatDateTime(job.started_at)}</KeyValue>
          <KeyValue label="Finished">{formatDateTime(job.finished_at)}</KeyValue>
        </dl>
      </section>
    </div>
  );
}

/** Detail of one ingestion job. Render with `key={jobId}` so each job starts fresh. */
export function JobDetailPanel({
  jobId,
  active,
  departments,
  onClose,
}: {
  jobId: string;
  active: boolean;
  departments: readonly Department[];
  onClose: () => void;
}) {
  const uid = useId();
  const visible = usePageVisible();
  const headingRef = useRef<HTMLHeadingElement>(null);
  const [state, setState] = useState<DetailState>({ status: 'loading' });

  const load = useCallback(
    async (signal?: AbortSignal) => {
      try {
        const job = await api.job(jobId, signal);
        setState({ status: 'ready', job });
      } catch (error) {
        const info = describeError(error);
        // A failed refresh keeps the last good data; only a failed first load replaces it.
        if (!isSilentError(error) && info) setState((current) => (current.status === 'ready' ? current : { status: 'error', error: info }));
      }
    },
    [jobId],
  );

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    headingRef.current?.focus();
    return () => controller.abort();
  }, [load]);

  const running = state.status === 'ready' && ACTIVE_JOB_STATUSES.has(state.job.status);
  usePolling(load, active && visible && running ? POLL_MS : null);

  return (
    <section className="card job-detail" aria-labelledby={`${uid}-title`}>
      <div className="card-head">
        <h2 id={`${uid}-title`} ref={headingRef} tabIndex={-1} className="card-title">
          Job details
        </h2>
        <div className="card-head-actions">
          {running && <span className="muted small">Updating every 2 seconds</span>}
          <button type="button" className="btn btn--small" onClick={onClose}>
            Close
          </button>
        </div>
      </div>
      {state.status === 'loading' && <Skeleton lines={5} />}
      {state.status === 'error' && (
        <div className="callout callout--critical">
          <ProblemSummary info={state.error} />
        </div>
      )}
      {state.status === 'ready' && <JobBody job={state.job} departments={departments} />}
    </section>
  );
}
