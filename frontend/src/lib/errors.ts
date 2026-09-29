import { ApiError, isRecord } from '../api/client';
import type { ProblemIssue } from '../api/types';

/** What the UI shows for an error: the problem's title/detail plus the request ID. */
export interface ErrorInfo {
  title: string;
  detail: string | null;
  requestId: string | null;
  issues: string[];
}

export function isSilentError(error: unknown): boolean {
  if (error instanceof ApiError) return error.silent;
  return error instanceof DOMException && error.name === 'AbortError';
}

/** Turns any thrown value into banner content, or null when there is nothing to show. */
export function describeError(error: unknown): ErrorInfo | null {
  if (isSilentError(error)) return null;
  if (error instanceof ApiError) {
    const { problem } = error;
    const title = problem.status === 403 ? "You don't have access to this" : problem.title || 'Request failed';
    const detail = problem.detail && problem.detail !== title ? problem.detail : null;
    return { title, detail, requestId: problem.request_id, issues: formatIssues(problem.errors) };
  }
  const detail = error instanceof Error ? error.message : String(error);
  return { title: 'Something went wrong', detail: detail || null, requestId: null, issues: [] };
}

function formatIssues(issues: ProblemIssue[] | undefined): string[] {
  if (!Array.isArray(issues)) return [];
  return issues.slice(0, 8).map((issue) => {
    if (typeof issue === 'string') return issue;
    if (!isRecord(issue)) return String(issue);
    const location = Array.isArray(issue.loc)
      ? issue.loc.filter((part) => part !== 'body' && part !== 'query').join('.')
      : '';
    const message =
      typeof issue.msg === 'string'
        ? issue.msg
        : typeof issue.message === 'string'
          ? issue.message
          : JSON.stringify(issue);
    return location ? `${location}: ${message}` : message;
  });
}
