import { useEffect, useId, useState, type ChangeEvent, type FormEvent } from 'react';
import { api } from '../api/client';
import type { AttendanceRecord, ExportRecordsFilter, Me, RecordsQuery, RecordsResponse } from '../api/types';
import { ExportButtons } from '../components/ExportButtons';
import { Skeleton, Spinner } from '../components/Primitives';
import { StatusPill } from '../components/StatusPill';
import { isSilentError } from '../lib/errors';
import { EMPTY, formatDate, formatNumber, formatPercent, humanise } from '../lib/format';
import { useFeedback } from '../lib/feedback';
import { useFocusWhenActive } from '../lib/hooks';
import {
  ATTENDANCE_STATUS,
  ATTENDANCE_STATUSES,
  describe,
  EXTRACTION_LABEL,
  lookup,
  REVIEW_STATUS,
  REVIEW_STATUSES,
} from '../lib/labels';

const PAGE_SIZE = 50;
const RESTRICTED = '[restricted]';

interface Filters {
  date_from: string;
  date_to: string;
  entity_id: string;
  employee_id: string;
  status: string;
  review_status: string;
}

const NO_FILTERS: Filters = { date_from: '', date_to: '', entity_id: '', employee_id: '', status: '', review_status: '' };

/** Only non-empty filters are sent, plus limit and offset. */
function toQuery(filters: Filters, offset: number): RecordsQuery {
  const query: RecordsQuery = { limit: PAGE_SIZE, offset };
  if (filters.date_from) query.date_from = filters.date_from;
  if (filters.date_to) query.date_to = filters.date_to;
  if (filters.entity_id) query.entity_id = filters.entity_id;
  const employee = filters.employee_id.trim();
  if (employee) query.employee_id = employee;
  const status = ATTENDANCE_STATUSES.find((value) => value === filters.status);
  if (status) query.status = status;
  const review = REVIEW_STATUSES.find((value) => value === filters.review_status);
  if (review) query.review_status = review;
  return query;
}

/** The applied filters as an export filter: the same records the table shows, without paging. */
function toExportFilter(filters: Filters): ExportRecordsFilter {
  const { limit: _limit, offset: _offset, ...rest } = toQuery(filters, 0);
  return rest;
}

function rangeText(page: RecordsResponse): string {
  const count = Array.isArray(page.items) ? page.items.length : 0;
  if (page.total === 0 || count === 0) return `0 of ${formatNumber(page.total, 0)}`;
  const offset = typeof page.offset === 'number' ? page.offset : 0;
  return `${formatNumber(offset + 1, 0)}–${formatNumber(offset + count, 0)} of ${formatNumber(page.total, 0)}`;
}

function RecordRow({ record }: { record: AttendanceRecord }) {
  const restricted = record.remarks === RESTRICTED;
  const method =
    lookup(EXTRACTION_LABEL, record.extraction_method) ??
    (record.extraction_method ? humanise(record.extraction_method) : 'Unknown method');
  const confidence =
    typeof record.extraction_confidence === 'number' ? formatPercent(record.extraction_confidence) : null;
  const lowConfidence = typeof record.extraction_confidence === 'number' && record.extraction_confidence < 1;
  const extraction = `Extracted by ${method}${confidence ? `, confidence ${confidence}` : ''}`;

  return (
    <tr className={record.review_status === 'needs_review' ? 'row--review' : undefined}>
      <td className="nowrap">
        <time dateTime={record.attendance_date}>{formatDate(record.attendance_date, { weekday: true })}</time>
      </td>
      <td>
        <span className="cell-main">{record.employee_name || EMPTY}</span>
        <span className="cell-sub mono">{record.employee_id}</span>
      </td>
      <td>{record.department_name || record.department_id || EMPTY}</td>
      <td>
        <StatusPill map={ATTENDANCE_STATUS} value={record.status} />
      </td>
      <td className="num">{record.check_in ?? EMPTY}</td>
      <td className="num">{record.check_out ?? EMPTY}</td>
      <td className="num">{formatNumber(record.total_hours)}</td>
      <td
        className={restricted ? 'cell-remarks is-restricted' : 'cell-remarks'}
        title={restricted ? 'Hidden at your clearance level' : undefined}
      >
        {record.remarks || EMPTY}
      </td>
      <td>
        <StatusPill map={REVIEW_STATUS} value={record.review_status} />
      </td>
      <td className="cell-source" title={`${record.source_file}\n${extraction}`}>
        <span className="cell-main source-file">{record.source_file}</span>
        <span className="cell-sub">
          {record.source_page_or_row || EMPTY} · {method}
          {lowConfidence && confidence ? ` ${confidence}` : ''}
        </span>
      </td>
    </tr>
  );
}

export function RecordsView({ me, active }: { me: Me; active: boolean }) {
  const { showError, announce } = useFeedback();
  const uid = useId();
  const headingRef = useFocusWhenActive<HTMLHeadingElement>(active);
  const [draft, setDraft] = useState<Filters>(NO_FILTERS);
  const [applied, setApplied] = useState<Filters>(NO_FILTERS);
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<RecordsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [started, setStarted] = useState(false);

  // Load the first page the first time the view is opened.
  useEffect(() => {
    if (active) setStarted(true);
  }, [active]);

  useEffect(() => {
    if (!started) return undefined;
    const controller = new AbortController();
    setLoading(true);
    api
      .records(toQuery(applied, offset), controller.signal)
      .then((data) => {
        if (controller.signal.aborted) return;
        setPage(data);
        const count = Array.isArray(data.items) ? data.items.length : 0;
        announce(count === 0 ? 'No records match these filters.' : `Showing records ${rangeText(data)}.`);
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted && !isSilentError(error)) showError(error);
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    // A newer request (filters or page changed) cancels this one.
    return () => controller.abort();
  }, [started, applied, offset, announce, showError]);

  function update(key: keyof Filters) {
    return (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      const { value } = event.target;
      setDraft((filters) => ({ ...filters, [key]: value }));
    };
  }

  function apply(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setApplied({ ...draft, employee_id: draft.employee_id.trim() });
    setOffset(0);
    setStarted(true);
  }

  function reset() {
    setDraft(NO_FILTERS);
    setApplied({ ...NO_FILTERS });
    setOffset(0);
  }

  const items = page && Array.isArray(page.items) ? page.items : [];
  const total = page?.total ?? 0;

  return (
    <section className="view" hidden={!active} aria-labelledby={`${uid}-title`}>
      <header className="view-head">
        <h1 id={`${uid}-title`} ref={headingRef} tabIndex={-1}>
          Records
        </h1>
        <p className="view-intro">
          Attendance records you are allowed to see, each traced to the file and row it came from.
        </p>
      </header>

      <form className="card filters" onSubmit={apply} aria-label="Filter records">
        <div className="filter-grid">
          <div className="field">
            <label htmlFor={`${uid}-from`} className="field-label">
              From
            </label>
            <input
              id={`${uid}-from`}
              type="date"
              className="input"
              value={draft.date_from}
              max={draft.date_to || undefined}
              onChange={update('date_from')}
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-to`} className="field-label">
              To
            </label>
            <input
              id={`${uid}-to`}
              type="date"
              className="input"
              value={draft.date_to}
              min={draft.date_from || undefined}
              onChange={update('date_to')}
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-dept`} className="field-label">
              Department
            </label>
            <select id={`${uid}-dept`} className="select" value={draft.entity_id} onChange={update('entity_id')}>
              <option value="">All</option>
              {me.departments.map((department) => (
                <option key={department.entity_id} value={department.entity_id}>
                  {department.name}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-employee`} className="field-label">
              Employee ID
            </label>
            <input
              id={`${uid}-employee`}
              type="text"
              className="input"
              value={draft.employee_id}
              placeholder="e.g. E001"
              autoComplete="off"
              spellCheck={false}
              onChange={update('employee_id')}
            />
          </div>
          <div className="field">
            <label htmlFor={`${uid}-status`} className="field-label">
              Status
            </label>
            <select id={`${uid}-status`} className="select" value={draft.status} onChange={update('status')}>
              <option value="">All</option>
              {ATTENDANCE_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {describe(ATTENDANCE_STATUS, status).label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor={`${uid}-review`} className="field-label">
              Review status
            </label>
            <select
              id={`${uid}-review`}
              className="select"
              value={draft.review_status}
              onChange={update('review_status')}
            >
              <option value="">All</option>
              {REVIEW_STATUSES.map((status) => (
                <option key={status} value={status}>
                  {describe(REVIEW_STATUS, status).label}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div className="button-row button-row--end">
          <button type="button" className="btn btn--ghost" onClick={reset}>
            Reset
          </button>
          <button type="submit" className="btn btn--primary">
            Apply
          </button>
        </div>
      </form>

      <section className="card" aria-labelledby={`${uid}-results`}>
        <div className="card-head">
          <h2 id={`${uid}-results`} className="card-title">
            Results
          </h2>
          <div className="card-head-actions">
            {loading && page && (
              <span className="muted small state-line">
                <Spinner /> Loading…
              </span>
            )}
            {page && <span className="muted small">{`${formatNumber(total, 0)} ${total === 1 ? 'record' : 'records'}`}</span>}
            {page && total > 0 && me.permissions.includes('export') && (
              <ExportButtons
                label="Export the filtered records"
                request={(format) => ({ format, records: toExportFilter(applied) })}
              />
            )}
          </div>
        </div>

        <div aria-busy={loading}>
          {!page && (loading || !started) && <Skeleton lines={6} />}
          {page && items.length === 0 && <p className="empty">No records match these filters.</p>}
          {items.length > 0 && (
            <div
              className={loading ? 'table-wrap is-stale' : 'table-wrap'}
              role="region"
              aria-labelledby={`${uid}-results`}
              tabIndex={0}
            >
              <table className="table table--records">
                <caption className="visually-hidden">Attendance records, {page ? rangeText(page) : ''}</caption>
                <thead>
                  <tr>
                    <th scope="col">Date</th>
                    <th scope="col">Employee</th>
                    <th scope="col">Department</th>
                    <th scope="col">Status</th>
                    <th scope="col" className="num">
                      In
                    </th>
                    <th scope="col" className="num">
                      Out
                    </th>
                    <th scope="col" className="num">
                      Hours
                    </th>
                    <th scope="col">Remarks</th>
                    <th scope="col">Review</th>
                    <th scope="col">Source</th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((record) => (
                    <RecordRow key={record.record_id} record={record} />
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {page && total > 0 && (
          <nav className="pager" aria-label="Record pages">
            <p className="pager-range">{rangeText(page)}</p>
            <div className="button-row">
              <button
                type="button"
                className="btn btn--small"
                disabled={loading || offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                Previous
              </button>
              <button
                type="button"
                className="btn btn--small"
                disabled={loading || offset + PAGE_SIZE >= total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                Next
              </button>
            </div>
          </nav>
        )}
      </section>
    </section>
  );
}
