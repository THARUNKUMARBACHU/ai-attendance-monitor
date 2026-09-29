import type { JobCounts, JobSummary } from '../api/types';
import { formatDateTime, formatNumber } from '../lib/format';
import { JOB_STATUS } from '../lib/labels';
import { StatusPill } from './StatusPill';

function Count({ counts, field, flag }: { counts: Partial<JobCounts>; field: keyof JobCounts; flag?: string }) {
  const value = counts[field];
  const flagged = flag !== undefined && typeof value === 'number' && value > 0;
  return <td className={flagged ? `num is-flagged is-${flag}` : 'num'}>{formatNumber(value, 0)}</td>;
}

export function JobsTable({
  jobs,
  selectedId,
  onSelect,
}: {
  jobs: readonly JobSummary[];
  selectedId: string | null;
  onSelect: (jobId: string) => void;
}) {
  return (
    <div className="table-wrap" role="region" aria-label="Recent ingestion jobs" tabIndex={0}>
      <table className="table">
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col" className="num">
              Version
            </th>
            <th scope="col">Status</th>
            <th scope="col">Created</th>
            <th scope="col" className="num">
              Rows
            </th>
            <th scope="col" className="num">
              New
            </th>
            <th scope="col" className="num">
              Updated
            </th>
            <th scope="col" className="num">
              Review
            </th>
            <th scope="col" className="num">
              Rejected
            </th>
          </tr>
        </thead>
        <tbody>
          {jobs.map((job) => {
            const counts = job.counts ?? {};
            const selected = job.job_id === selectedId;
            return (
              <tr key={job.job_id} className={selected ? 'is-selected' : undefined}>
                <td className="cell-file">
                  <button
                    type="button"
                    className="link-button"
                    title={job.filename}
                    aria-current={selected ? 'true' : undefined}
                    onClick={() => onSelect(job.job_id)}
                  >
                    {job.filename}
                  </button>
                </td>
                <td className="num">v{job.version_no}</td>
                <td>
                  <StatusPill map={JOB_STATUS} value={job.status} />
                </td>
                <td className="nowrap">
                  <time dateTime={job.created_at}>{formatDateTime(job.created_at)}</time>
                </td>
                <Count counts={counts} field="rows_read" />
                <Count counts={counts} field="records_created" />
                <Count counts={counts} field="records_updated" />
                <Count counts={counts} field="needs_review" flag="warning" />
                <Count counts={counts} field="rejected" flag="critical" />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
