import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent as ReactDragEvent,
  type FormEvent,
} from 'react';
import { api } from '../api/client';
import type { IngestionAccepted, JobStatus, JobSummary, Me } from '../api/types';
import { ProblemSummary } from '../components/ErrorBanner';
import { JobDetailPanel } from '../components/JobDetailPanel';
import { JobsTable } from '../components/JobsTable';
import { Skeleton, Spinner } from '../components/Primitives';
import { StatusPill } from '../components/StatusPill';
import { describeError, isSilentError, type ErrorInfo } from '../lib/errors';
import { formatBytes, formatClock, plural, shortId } from '../lib/format';
import { useFeedback } from '../lib/feedback';
import { useFocusWhenActive, usePageVisible, usePolling } from '../lib/hooks';
import { ACTIVE_JOB_STATUSES, describe, JOB_STATUS } from '../lib/labels';

const JOB_POLL_MS = 2000;
const ACCEPT = '.csv,.xlsx,.docx,.pdf';

type QueueStatus = 'ready' | 'uploading' | 'accepted' | 'failed';

interface QueueItem {
  id: number;
  file: File;
  status: QueueStatus;
  result?: IngestionAccepted | undefined;
  error?: ErrorInfo | undefined;
}

function fileKey(file: File): string {
  return `${file.name}|${file.size}|${file.lastModified}`;
}

function toSummary(accepted: IngestionAccepted): JobSummary {
  return {
    job_id: accepted.job_id,
    filename: accepted.filename,
    version_no: accepted.version_no,
    status: accepted.status,
    created_at: new Date().toISOString(),
    finished_at: null,
    counts: {},
  };
}

function hasFiles(event: ReactDragEvent<HTMLElement>): boolean {
  return event.dataTransfer.types.includes('Files');
}

/** The recent-jobs list. Polls every 2 s while any job is queued or running and the view is visible. */
function useJobs(active: boolean) {
  const { showError, announce } = useFeedback();
  const visible = usePageVisible();
  const [items, setItems] = useState<JobSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null);
  const ticket = useRef(0);
  const failing = useRef(false);
  const lastStatus = useRef(new Map<string, JobStatus>());

  const refresh = useCallback(async () => {
    const current = ++ticket.current;
    try {
      const data = await api.jobs(20);
      if (current !== ticket.current) return; // a newer refresh has started
      const next = Array.isArray(data.items) ? data.items : [];
      const finished = next
        .filter((job) => {
          const before = lastStatus.current.get(job.job_id);
          return before !== undefined && ACTIVE_JOB_STATUSES.has(before) && !ACTIVE_JOB_STATUSES.has(job.status);
        })
        .map((job) => `${job.filename}: ${describe(JOB_STATUS, job.status).label}.`);
      if (finished.length > 0) announce(finished.join(' '));
      lastStatus.current = new Map(next.map((job) => [job.job_id, job.status]));
      failing.current = false;
      setItems(next);
      setUpdatedAt(new Date());
    } catch (error) {
      if (current !== ticket.current || isSilentError(error)) return;
      if (!failing.current) showError(error); // report once per run of failures, not every tick
      failing.current = true;
    } finally {
      if (current === ticket.current) setLoaded(true);
    }
  }, [announce, showError]);

  const upsert = useCallback((accepted: IngestionAccepted) => {
    lastStatus.current.set(accepted.job_id, accepted.status);
    setItems((list) => [toSummary(accepted), ...list.filter((job) => job.job_id !== accepted.job_id)]);
  }, []);

  useEffect(() => {
    if (active) void refresh();
  }, [active, refresh]);

  const polling = active && visible && items.some((job) => ACTIVE_JOB_STATUSES.has(job.status));
  usePolling(refresh, polling ? JOB_POLL_MS : null);

  return { items, loaded, updatedAt, polling, refresh, upsert };
}

export function UploadView({ me, active }: { me: Me; active: boolean }) {
  const { clearError, announce } = useFeedback();
  const uid = useId();
  const headingRef = useFocusWhenActive<HTMLHeadingElement>(active);
  const jobsHeadingRef = useRef<HTMLHeadingElement>(null);
  const nextItemId = useRef(1);
  const dragDepth = useRef(0);

  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [entityId, setEntityId] = useState('');
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [selectedJobId, setSelectedJobId] = useState<string | null>(null);
  const jobs = useJobs(active);

  const readyItems = queue.filter((item) => item.status === 'ready');
  const finishedCount = queue.filter((item) => item.status === 'accepted' || item.status === 'failed').length;

  // Stop files dropped outside the drop zone from navigating away from the app.
  useEffect(() => {
    if (!active) return undefined;
    const block = (event: DragEvent) => {
      if (event.dataTransfer?.types.includes('Files')) event.preventDefault();
    };
    window.addEventListener('dragover', block);
    window.addEventListener('drop', block);
    return () => {
      window.removeEventListener('dragover', block);
      window.removeEventListener('drop', block);
    };
  }, [active]);

  function patch(id: number, changes: Partial<QueueItem>) {
    setQueue((items) => items.map((item) => (item.id === id ? { ...item, ...changes } : item)));
  }

  function addFiles(list: FileList | null) {
    const waiting = new Set(readyItems.map((item) => fileKey(item.file)));
    const additions: QueueItem[] = [];
    for (const file of Array.from(list ?? [])) {
      if (waiting.has(fileKey(file))) continue;
      waiting.add(fileKey(file));
      additions.push({ id: nextItemId.current++, file, status: 'ready' });
    }
    if (additions.length === 0) return;
    setQueue((items) => [...items, ...additions]);
    announce(`${plural(additions.length, 'file')} ready to upload.`);
  }

  /** Uploads the given items one at a time. */
  async function upload(items: readonly QueueItem[]) {
    if (uploading || items.length === 0) return;
    setUploading(true);
    clearError();
    const entity = entityId || null;
    let accepted = 0;
    let failed = 0;
    for (const item of items) {
      patch(item.id, { status: 'uploading', error: undefined });
      try {
        const result = await api.upload(item.file, entity);
        patch(item.id, { status: 'accepted', result });
        jobs.upsert(result);
        accepted += 1;
      } catch (error) {
        if (isSilentError(error)) {
          patch(item.id, { status: 'ready' });
          break; // signed out or cancelled
        }
        patch(item.id, { status: 'failed', error: describeError(error) ?? undefined });
        failed += 1;
      }
    }
    setUploading(false);
    if (accepted > 0) void jobs.refresh();
    announce(
      failed > 0 ? `${plural(accepted, 'file')} uploaded, ${failed} failed.` : `${plural(accepted, 'file')} uploaded.`,
    );
  }

  function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void upload(readyItems);
  }

  function onFileInput(event: ChangeEvent<HTMLInputElement>) {
    addFiles(event.currentTarget.files);
    event.currentTarget.value = ''; // allow choosing the same file again
  }

  function onDragEnter(event: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  }

  function onDragOver(event: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = 'copy';
  }

  function onDragLeave(event: ReactDragEvent<HTMLDivElement>) {
    if (!hasFiles(event)) return;
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }

  function onDrop(event: ReactDragEvent<HTMLDivElement>) {
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    addFiles(event.dataTransfer.files);
  }

  function closeDetail() {
    setSelectedJobId(null);
    jobsHeadingRef.current?.focus();
  }

  function renderStatus(item: QueueItem) {
    switch (item.status) {
      case 'ready':
        return <span className="muted">Ready to upload</span>;
      case 'uploading':
        return (
          <span className="state-line">
            <Spinner /> Uploading…
          </span>
        );
      case 'failed':
        return item.error ? <ProblemSummary info={item.error} /> : <span>Upload failed.</span>;
      case 'accepted': {
        const result = item.result;
        if (!result) return null;
        if (result.status === 'duplicate') {
          return (
            <span className="state-line">
              <StatusPill map={JOB_STATUS} value="duplicate" />
              Already uploaded: identical file.
              {result.duplicate_of_job_id && (
                <button
                  type="button"
                  className="link-button"
                  onClick={() => setSelectedJobId(result.duplicate_of_job_id)}
                >
                  View original job
                </button>
              )}
            </span>
          );
        }
        return (
          <span className="state-line">
            <StatusPill map={JOB_STATUS} value={result.status} />
            Job <code>{shortId(result.job_id)}</code>
            {result.version_no > 1 ? ` · version ${result.version_no}` : ''}
            <button type="button" className="link-button" onClick={() => setSelectedJobId(result.job_id)}>
              View job
            </button>
          </span>
        );
      }
    }
  }

  return (
    <section className="view" hidden={!active} aria-labelledby={`${uid}-title`}>
      <header className="view-head">
        <h1 id={`${uid}-title`} ref={headingRef} tabIndex={-1}>
          Upload &amp; jobs
        </h1>
        <p className="view-intro">
          Upload attendance files for {me.tenant_name || 'your tenant'}. Each file is validated, parsed and indexed in
          the background; progress appears under Recent jobs.
        </p>
      </header>

      <form className="card upload-card" onSubmit={onSubmit} aria-labelledby={`${uid}-upload`}>
        <h2 id={`${uid}-upload`} className="card-title">
          Upload files
        </h2>
        <div
          className={`dropzone${dragging ? ' is-dragover' : ''}`}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          <input
            id={`${uid}-file`}
            type="file"
            className="file-input"
            accept={ACCEPT}
            multiple
            aria-describedby={`${uid}-file-hint`}
            onChange={onFileInput}
          />
          <label htmlFor={`${uid}-file`} className="dropzone-label">
            <span className="link-text">Choose files</span> or drag them here
          </label>
          <p id={`${uid}-file-hint`} className="dropzone-hint">
            CSV, XLSX, DOCX or PDF, including scanned PDFs. Several files upload one at a time.
          </p>
        </div>

        {queue.length > 0 && (
          <ul className="upload-list" aria-label="Selected files">
            {queue.map((item) => (
              <li key={item.id} className={`upload-item upload-item--${item.status}`}>
                <div className="upload-file">
                  <span className="upload-name">{item.file.name}</span>
                  <span className="upload-size">{formatBytes(item.file.size)}</span>
                </div>
                <div className="upload-state">{renderStatus(item)}</div>
                <div className="upload-item-actions">
                  {item.status === 'failed' && (
                    <button type="button" className="btn btn--small" disabled={uploading} onClick={() => void upload([item])}>
                      Retry
                    </button>
                  )}
                  {(item.status === 'ready' || item.status === 'failed') && (
                    <button
                      type="button"
                      className="btn btn--small btn--ghost"
                      disabled={uploading}
                      aria-label={`Remove ${item.file.name}`}
                      onClick={() => setQueue((items) => items.filter((other) => other.id !== item.id))}
                    >
                      Remove
                    </button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        <div className="upload-actions">
          <div className="field">
            <label htmlFor={`${uid}-entity`} className="field-label">
              Department
            </label>
            <select
              id={`${uid}-entity`}
              className="select"
              value={entityId}
              disabled={uploading}
              onChange={(event) => setEntityId(event.target.value)}
            >
              <option value="">Not department-specific</option>
              {me.departments.map((department) => (
                <option key={department.entity_id} value={department.entity_id}>
                  {department.name}
                </option>
              ))}
            </select>
          </div>
          <div className="button-row">
            {finishedCount > 0 && (
              <button
                type="button"
                className="btn btn--ghost"
                disabled={uploading}
                onClick={() => setQueue((items) => items.filter((item) => item.status === 'ready' || item.status === 'uploading'))}
              >
                Clear finished
              </button>
            )}
            <button type="submit" className="btn btn--primary" disabled={uploading || readyItems.length === 0}>
              {uploading ? (
                <>
                  <Spinner /> Uploading…
                </>
              ) : readyItems.length > 1 ? (
                `Upload ${readyItems.length} files`
              ) : (
                'Upload'
              )}
            </button>
          </div>
        </div>
      </form>

      <section className="card" aria-labelledby={`${uid}-jobs`}>
        <div className="card-head">
          <h2 id={`${uid}-jobs`} ref={jobsHeadingRef} tabIndex={-1} className="card-title">
            Recent jobs
          </h2>
          <div className="card-head-actions">
            {jobs.polling ? (
              <span className="muted small state-line">
                <Spinner /> Updating every 2 seconds
              </span>
            ) : (
              jobs.updatedAt && <span className="muted small">Updated {formatClock(jobs.updatedAt)}</span>
            )}
            <button type="button" className="btn btn--small" onClick={() => void jobs.refresh()}>
              Refresh
            </button>
          </div>
        </div>
        {!jobs.loaded ? (
          <Skeleton lines={4} />
        ) : jobs.items.length === 0 ? (
          <p className="empty">No uploads yet. Files you upload appear here.</p>
        ) : (
          <JobsTable jobs={jobs.items} selectedId={selectedJobId} onSelect={setSelectedJobId} />
        )}
      </section>

      {selectedJobId && (
        <JobDetailPanel
          key={selectedJobId}
          jobId={selectedJobId}
          active={active}
          departments={me.departments}
          onClose={closeDetail}
        />
      )}
    </section>
  );
}
