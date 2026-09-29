import type { Me, QueryResponse } from '../api/types';
import { departmentName, EMPTY, formatDuration, formatNumber, humanise } from '../lib/format';
import { CopyButton } from './CopyButton';
import { Heading, KeyValue, type HeadingLevel } from './Primitives';

const PREVIEW_ROW_LIMIT = 100;

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return EMPTY;
  if (typeof value === 'number') return formatNumber(value, 4);
  if (typeof value === 'string') return value;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  return JSON.stringify(value);
}

function PreviewTable({ rows, label }: { rows: ReadonlyArray<Record<string, unknown>>; label: string }) {
  const first = rows[0];
  if (!first) return null;
  const columns = Object.keys(first);
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      <table className="table table--compact">
        <thead>
          <tr>
            {columns.map((column) => (
              <th key={column} scope="col" className="mono">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.slice(0, PREVIEW_ROW_LIMIT).map((row, index) => (
            <tr key={index}>
              {columns.map((column) => (
                <td key={column} className={typeof row[column] === 'number' ? 'num' : undefined}>
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** The collapsible "How this was answered" section of an answer card. */
export function HowAnswered({
  response,
  me,
  headingLevel,
}: {
  response: QueryResponse;
  me: Me;
  headingLevel: HeadingLevel;
}) {
  const { computation, context, versions } = response;
  const preview = Array.isArray(computation?.result_preview) ? computation.result_preview : [];
  const entityScope = Array.isArray(context?.entity_scope) ? context.entity_scope : [];

  return (
    <details className="how">
      <summary>How this was answered</summary>
      <div className="how-body">
        <dl className="kv kv--grid">
          <KeyValue label="Retrieval">{response.retrieval_mode ? humanise(response.retrieval_mode) : 'None'}</KeyValue>
          {computation && <KeyValue label="Rows matched">{formatNumber(computation.row_count, 0)}</KeyValue>}
          {computation && (
            <KeyValue label="Excluded, pending review">{formatNumber(computation.excluded_pending_review, 0)}</KeyValue>
          )}
          <KeyValue label="Cached">{response.cached ? 'Yes' : 'No'}</KeyValue>
          <KeyValue label="Latency">{formatDuration(response.latency_ms)}</KeyValue>
          <KeyValue label="Request ID">
            <code className="break">{response.request_id}</code>
          </KeyValue>
        </dl>

        {computation?.sql && (
          <div className="code-block">
            <div className="code-head">
              <span className="code-label">SQL</span>
              <CopyButton text={computation.sql} label="Copy SQL" />
            </div>
            <pre>
              <code>{computation.sql}</code>
            </pre>
          </div>
        )}

        {preview.length > 0 && (
          <div className="how-section">
            <Heading level={headingLevel} className="section-title">
              Result preview
            </Heading>
            <PreviewTable rows={preview} label="Result preview" />
          </div>
        )}

        {context && (
          <div className="how-section">
            <Heading level={headingLevel} className="section-title">
              Context used
            </Heading>
            <dl className="kv kv--grid">
              <KeyValue label="Tenant">
                {context.tenant_id === me.tenant_id ? `${me.tenant_name} (${context.tenant_id})` : context.tenant_id}
              </KeyValue>
              <KeyValue label="Role">{humanise(context.role)}</KeyValue>
              <KeyValue label="Scope">{humanise(context.scope)}</KeyValue>
              <KeyValue label="Departments">
                {entityScope.length > 0
                  ? entityScope.map((id) => departmentName(me.departments, id)).join(', ')
                  : EMPTY}
              </KeyValue>
              <KeyValue label="Employee ID">{context.employee_id ?? EMPTY}</KeyValue>
              <KeyValue label="Clearance">{context.clearance ? humanise(context.clearance) : EMPTY}</KeyValue>
            </dl>
          </div>
        )}

        {versions && (
          <div className="how-section">
            <Heading level={headingLevel} className="section-title">
              Model and versions
            </Heading>
            <dl className="kv kv--grid">
              <KeyValue label="Model">
                <code className="break">{versions.model}</code>
              </KeyValue>
              <KeyValue label="Prompts">{versions.prompts}</KeyValue>
              <KeyValue label="Data version">{String(versions.data)}</KeyValue>
              <KeyValue label="Knowledge version">{String(versions.knowledge)}</KeyValue>
            </dl>
          </div>
        )}
      </div>
    </details>
  );
}
