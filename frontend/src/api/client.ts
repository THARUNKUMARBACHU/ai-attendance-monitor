import type {
  DevTokenRequest,
  DevUsersResponse,
  ExampleStatus,
  ExportRequest,
  FeedbackExample,
  FeedbackListResponse,
  FeedbackRequest,
  IngestionAccepted,
  JobDetail,
  JobListResponse,
  Me,
  Problem,
  QueryRequest,
  QueryResponse,
  ReadinessResponse,
  RecordsQuery,
  RecordsResponse,
  TokenResponse,
} from './types';

const TOKEN_KEY = 'ai_token';

/** The bearer token lives only in sessionStorage (this tab, until it closes). */
export const tokenStore = {
  get(): string | null {
    try {
      return window.sessionStorage.getItem(TOKEN_KEY);
    } catch {
      return null;
    }
  },
  /** Returns false when the browser blocks sessionStorage. */
  set(token: string): boolean {
    try {
      window.sessionStorage.setItem(TOKEN_KEY, token);
      return true;
    } catch {
      return false;
    }
  },
  clear(): void {
    try {
      window.sessionStorage.removeItem(TOKEN_KEY);
    } catch {
      // Storage unavailable: nothing was stored.
    }
  },
};

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;
  /** The parsed response body, if any. */
  readonly body: unknown;
  /** Already dealt with (a 401 sign-out or a cancelled request); callers should not report it. */
  readonly silent: boolean;

  constructor(problem: Problem, body: unknown = null, silent = false) {
    super(problem.detail || problem.title);
    this.name = 'ApiError';
    this.status = problem.status;
    this.problem = problem;
    this.body = body;
    this.silent = silent;
  }
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function nonEmpty(value: unknown): string | undefined {
  return typeof value === 'string' && value !== '' ? value : undefined;
}

function clientError(title: string, detail: string, silent = false): ApiError {
  return new ApiError({ type: 'about:blank', title, status: 0, detail, code: 'client_error', request_id: null }, null, silent);
}

function toProblem(response: Response, body: unknown): Problem {
  const requestId = response.headers.get('X-Request-ID');
  const title = response.statusText || `Request failed (${response.status})`;
  if (isRecord(body)) {
    // FastAPI's default validation shape puts the issue list in `detail`.
    const detailList = Array.isArray(body.detail) ? body.detail : undefined;
    return {
      type: nonEmpty(body.type) ?? 'about:blank',
      title: nonEmpty(body.title) ?? title,
      status: response.status,
      detail: nonEmpty(body.detail) ?? (detailList ? 'The request is invalid.' : ''),
      code: nonEmpty(body.code) ?? 'http_error',
      request_id: nonEmpty(body.request_id) ?? requestId,
      instance: nonEmpty(body.instance),
      errors: Array.isArray(body.errors) ? body.errors : detailList,
    };
  }
  const detail =
    typeof body === 'string' && body.trim() !== '' && !body.trimStart().startsWith('<')
      ? body.slice(0, 300)
      : response.status >= 500
        ? 'The server could not complete the request. Try again shortly.'
        : '';
  return { type: 'about:blank', title, status: response.status, detail, code: 'http_error', request_id: requestId };
}

async function readBody(response: Response): Promise<unknown> {
  const raw = await response.text();
  if (raw === '') return null;
  if (/[/+]json/i.test(response.headers.get('Content-Type') ?? '')) {
    try {
      return JSON.parse(raw) as unknown;
    } catch {
      return raw;
    }
  }
  return raw;
}

let unauthorizedHandler: (() => void) | null = null;

/** Called when an authenticated request gets a 401; the token has already been cleared. */
export function setUnauthorizedHandler(handler: (() => void) | null): void {
  unauthorizedHandler = handler;
}

let generation = 0;
const inflight = new Set<AbortController>();

/** Cancels every in-flight request and drops responses that arrive later (used on sign-out). */
export function cancelAllRequests(): void {
  generation += 1;
  for (const controller of inflight) controller.abort();
  inflight.clear();
}

interface RequestOptions {
  method?: 'GET' | 'POST';
  json?: unknown;
  form?: FormData;
  /** Send the bearer token (default true). */
  auth?: boolean;
  timeoutMs?: number;
  signal?: AbortSignal | undefined;
  accept?: string;
}

interface Sent<T> {
  response: Response;
  data: T;
  token: string | null;
}

/** Sends one request and reads its body with `read`; network failures, timeouts and cancellations
 *  become ApiErrors. HTTP error statuses are left to the caller. */
async function send<T>(
  path: string,
  options: RequestOptions,
  read: (response: Response) => Promise<T>,
): Promise<Sent<T>> {
  // Relative, same-origin paths only: the token must never be sent anywhere else.
  if (!path.startsWith('/') || path.startsWith('//')) throw new Error(`Refusing to request ${path}`);
  const {
    method = 'GET',
    json,
    form,
    auth = true,
    timeoutMs = 30_000,
    signal,
    accept = 'application/json, application/problem+json',
  } = options;

  const headers = new Headers({ Accept: accept });
  const token = auth ? tokenStore.get() : null;
  if (token) headers.set('Authorization', `Bearer ${token}`);
  let body: BodyInit | undefined;
  if (form) {
    body = form; // the browser sets the multipart boundary
  } else if (json !== undefined) {
    headers.set('Content-Type', 'application/json');
    body = JSON.stringify(json);
  }

  const controller = new AbortController();
  const startedIn = generation;
  let timedOut = false;
  const timer = window.setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const forwardAbort = (): void => controller.abort();
  if (signal?.aborted) controller.abort();
  else signal?.addEventListener('abort', forwardAbort, { once: true });
  inflight.add(controller);

  let response: Response;
  let data: T;
  try {
    response = await fetch(path, {
      method,
      headers,
      body,
      signal: controller.signal,
      credentials: 'same-origin',
      cache: 'no-store',
    });
    data = await read(response);
  } catch {
    if (timedOut) throw clientError('Request timed out', 'The server took too long to respond. Please try again.');
    if (controller.signal.aborted) throw clientError('Request cancelled', 'The request was cancelled.', true);
    throw clientError('Network error', 'Could not reach the server. Check your connection and try again.');
  } finally {
    window.clearTimeout(timer);
    signal?.removeEventListener('abort', forwardAbort);
    inflight.delete(controller);
  }

  if (startedIn !== generation || signal?.aborted) {
    throw clientError('Request cancelled', 'The request was cancelled.', true);
  }
  return { response, data, token };
}

/** Throws the problem for an HTTP error response; a 401 on an authenticated request signs out. */
function fail(response: Response, data: unknown, token: string | null): never {
  const problem = toProblem(response, data);
  if (response.status === 401 && token) {
    tokenStore.clear();
    unauthorizedHandler?.();
    throw new ApiError(problem, data, true);
  }
  throw new ApiError(problem, data);
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { response, data, token } = await send(path, options, readBody);
  if (response.ok) return data as T;
  return fail(response, data, token);
}

export interface DownloadedFile {
  blob: Blob;
  filename: string;
}

/** The server's file name from Content-Disposition, if it is a plain, safe name. */
function fileNameFrom(header: string | null): string {
  const match = /filename="?([^";]+)"?/i.exec(header ?? '');
  const name = match?.[1]?.trim() ?? '';
  return /^[A-Za-z0-9._-]{1,200}$/.test(name) ? name : 'attendance-export';
}

async function requestFile(path: string, options: RequestOptions): Promise<DownloadedFile> {
  const { response, data, token } = await send<unknown>(path, { ...options, accept: '*/*' }, (res) =>
    res.ok ? res.blob() : readBody(res),
  );
  if (!response.ok) return fail(response, data, token);
  if (!(data instanceof Blob)) throw clientError('Unexpected response', 'The export could not be read.');
  return { blob: data, filename: fileNameFrom(response.headers.get('Content-Disposition')) };
}

function toSearch(query: RecordsQuery): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value));
  }
  return params.toString();
}

function isReadiness(value: unknown): value is ReadinessResponse {
  return isRecord(value) && (value.status === 'ok' || value.status === 'degraded') && isRecord(value.checks);
}

async function readiness(): Promise<ReadinessResponse> {
  try {
    const data = await request<unknown>('/health/ready', { auth: false, timeoutMs: 15_000 });
    if (isReadiness(data)) return data;
    throw clientError('Unexpected response', 'The health endpoint returned an unexpected response.');
  } catch (error) {
    // A degraded service answers 503 with the same body.
    if (error instanceof ApiError && error.status === 503 && isReadiness(error.body)) return error.body;
    throw error;
  }
}

/** /me, with fallbacks for fields an older backend may not send yet. */
async function me(): Promise<Me> {
  const raw = await request<Partial<Me>>('/api/v1/me');
  const entityScope = Array.isArray(raw.entity_scope) ? raw.entity_scope : [];
  return {
    tenant_id: raw.tenant_id ?? '',
    tenant_name: raw.tenant_name || raw.tenant_id || '',
    product_id: raw.product_id ?? '',
    module: raw.module ?? '',
    user_id: raw.user_id ?? '',
    display_name: raw.display_name || raw.user_id || '',
    role: raw.role ?? 'employee',
    scope: raw.scope ?? 'self',
    entity_scope: entityScope,
    employee_id: raw.employee_id ?? null,
    clearance: raw.clearance ?? '',
    permissions: Array.isArray(raw.permissions) ? raw.permissions : [],
    departments: Array.isArray(raw.departments)
      ? raw.departments
      : entityScope.map((id) => ({ entity_id: id, name: id })),
  };
}

export const api = {
  devUsers: (signal?: AbortSignal) =>
    request<DevUsersResponse>('/api/v1/auth/dev-users', { auth: false, signal }),

  devToken: (userId: string) => {
    const body: DevTokenRequest = { user_id: userId };
    return request<TokenResponse>('/api/v1/auth/dev-token', { method: 'POST', json: body, auth: false });
  },

  me,
  readiness,

  query: (body: QueryRequest) =>
    request<QueryResponse>('/api/v1/query', { method: 'POST', json: body, timeoutMs: 90_000 }),

  upload: (file: File, entityId: string | null) => {
    const form = new FormData();
    form.append('file', file, file.name);
    if (entityId) form.append('entity_id', entityId);
    return request<IngestionAccepted>('/api/v1/ingestions', { method: 'POST', form, timeoutMs: 120_000 });
  },

  jobs: (limit = 20) => request<JobListResponse>(`/api/v1/ingestions?limit=${limit}`),

  job: (jobId: string, signal?: AbortSignal) =>
    request<JobDetail>(`/api/v1/ingestions/${encodeURIComponent(jobId)}`, { signal }),

  records: (query: RecordsQuery, signal?: AbortSignal) =>
    request<RecordsResponse>(`/api/v1/records?${toSearch(query)}`, { signal }),

  /** The export file (JSON, XLSX or PDF) for records or one question, built under your current access. */
  exportFile: (body: ExportRequest) =>
    requestFile('/api/v1/exports', { method: 'POST', json: body, timeoutMs: 120_000 }),

  /** Submits a correction; the server validates and replays it before answering (can take a while). */
  submitFeedback: (body: FeedbackRequest) =>
    request<FeedbackExample>('/api/v1/feedback', { method: 'POST', json: body, timeoutMs: 120_000 }),

  feedbackExamples: (status: ExampleStatus | null, signal?: AbortSignal) =>
    request<FeedbackListResponse>(`/api/v1/feedback?limit=100${status ? `&status=${status}` : ''}`, { signal }),

  deactivateFeedback: (exampleId: string) =>
    request<FeedbackExample>(`/api/v1/feedback/${encodeURIComponent(exampleId)}/deactivate`, { method: 'POST' }),
};
