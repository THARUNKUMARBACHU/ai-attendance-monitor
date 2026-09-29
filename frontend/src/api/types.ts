// Types mirroring the Attendance Intelligence HTTP API contract (v1).
// Timestamps are ISO 8601 strings; calendar dates are YYYY-MM-DD strings.

export type Role = 'admin' | 'manager' | 'employee';
export type Scope = 'tenant' | 'department' | 'self';

// ---- Errors: RFC 9457 problem details (application/problem+json) ----

/** One entry of `errors`; request validation failures use `{loc, msg, type}`. */
export type ProblemIssue =
  | string
  | { loc?: Array<string | number>; msg?: string; type?: string; [key: string]: unknown };

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
  code: string;
  request_id: string | null;
  instance?: string | undefined;
  errors?: ProblemIssue[] | undefined;
}

// ---- GET /api/v1/auth/dev-users, POST /api/v1/auth/dev-token ----

export interface DevUser {
  user_id: string;
  display_name: string;
  tenant_id: string;
  tenant_name: string;
  role: Role;
  scope: Scope;
  scope_description: string;
}

export interface DevUsersResponse {
  users: DevUser[];
}

export interface DevTokenRequest {
  user_id: string;
}

export interface TokenResponse {
  access_token: string;
  token_type: 'bearer';
  expires_in: number;
}

// ---- GET /api/v1/me ----

export interface Department {
  entity_id: string;
  name: string;
}

export interface Me {
  tenant_id: string;
  tenant_name: string;
  product_id: string;
  module: string;
  user_id: string;
  display_name: string;
  role: Role;
  scope: Scope;
  entity_scope: string[];
  employee_id: string | null;
  clearance: string;
  permissions: string[];
  departments: Department[];
}

// ---- GET /health/ready (200 when ok, 503 when degraded; same body) ----

export type CheckName = 'service' | 'database' | 'queue' | 'cache' | 'search' | 'vector' | 'model_provider';
export type CheckStatus = 'up' | 'down' | 'configured' | 'not_configured';

export interface HealthCheck {
  status: CheckStatus;
  latency_ms: number | null;
  detail: string | null;
}

export interface ReadinessResponse {
  status: 'ok' | 'degraded';
  checks: Record<CheckName, HealthCheck>;
}

// ---- Ingestion: POST /api/v1/ingestions, GET /api/v1/ingestions[/{job_id}] ----

export type JobStatus = 'queued' | 'running' | 'completed' | 'completed_with_errors' | 'failed' | 'duplicate';

export interface IngestionAccepted {
  job_id: string;
  status: 'queued' | 'duplicate';
  filename: string;
  source_id: string;
  version_no: number;
  checksum: string;
  media_type: string;
  size_bytes: number;
  duplicate_of_job_id: string | null;
}

export interface JobCounts {
  rows_read: number;
  records_created: number;
  records_updated: number;
  records_unchanged: number;
  records_removed: number;
  needs_review: number;
  rejected: number;
  chunks_indexed: number;
  chunks_quarantined: number;
}

export interface JobSummary {
  job_id: string;
  filename: string;
  version_no: number;
  status: JobStatus;
  created_at: string;
  finished_at: string | null;
  /** May be partial or empty while the job is running. */
  counts: Partial<JobCounts>;
}

export interface JobListResponse {
  items: JobSummary[];
}

export type StageName = 'validate' | 'extract' | 'normalise' | 'store' | 'index';
export type StageStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped';

export interface JobStage {
  name: StageName;
  status: StageStatus;
  started_at: string | null;
  finished_at: string | null;
  detail: string | null;
}

export interface JobFailure {
  location: string;
  reason: string;
}

export interface JobDetail extends JobSummary {
  stages: JobStage[];
  attempts: number;
  max_attempts: number;
  failures: JobFailure[];
  last_error: string | null;
  checksum: string;
  media_type: string;
  size_bytes: number;
  entity_id: string | null;
  started_at: string | null;
}

// ---- GET /api/v1/records ----

export type AttendanceStatus = 'PRESENT' | 'ABSENT' | 'LEAVE' | 'WFH' | 'HALF_DAY' | 'HOLIDAY' | 'WEEKLY_OFF';
export type ReviewStatus = 'auto_accepted' | 'needs_review' | 'verified' | 'rejected';

export interface AttendanceRecord {
  record_id: string;
  /** Stable identity of one employee-day; may be missing from an older backend. */
  record_key?: string;
  attendance_date: string;
  employee_id: string;
  employee_name: string;
  department_id: string;
  department_name: string;
  status: AttendanceStatus;
  check_in: string | null;
  check_out: string | null;
  total_hours: number | null;
  /** Shown as returned; may be "[restricted]" at lower clearance. */
  remarks: string | null;
  review_status: ReviewStatus;
  extraction_method: string;
  extraction_confidence: number;
  source_file: string;
  source_page_or_row: string;
}

export interface RecordsQuery {
  date_from?: string;
  date_to?: string;
  entity_id?: string;
  employee_id?: string;
  status?: AttendanceStatus;
  review_status?: ReviewStatus;
  source_file?: string;
  limit?: number;
  offset?: number;
}

export interface RecordsResponse {
  items: AttendanceRecord[];
  total: number;
  limit: number;
  offset: number;
}

// ---- POST /api/v1/query (non-answer outcomes are still 200) ----

export type Outcome = 'answered' | 'unavailable' | 'denied' | 'needs_review' | 'out_of_scope';

export type ReasonCode =
  | 'no_data'
  | 'insufficient_evidence'
  | 'out_of_scope_entity'
  | 'other_tenant'
  | 'policy_violation'
  | 'not_attendance'
  | 'provider_unavailable'
  | 'planning_failed'
  | 'query_failed'
  | 'low_confidence';

export type RetrievalMode = 'structured' | 'document' | 'hybrid';
export type ConfidenceBand = 'high' | 'medium' | 'low';

export interface QueryRequest {
  question: string;
  as_of?: string;
}

export interface Confidence {
  score: number;
  band: ConfidenceBand;
  reasons: string[];
}

export interface Citation {
  id: string;
  kind: 'records' | 'document';
  source_file: string;
  locations: string;
  record_count: number | null;
  snippet: string | null;
}

export interface Computation {
  sql: string | null;
  row_count: number;
  excluded_pending_review: number;
  result_preview: Array<Record<string, unknown>>;
}

export interface QueryContext {
  tenant_id: string;
  product_id: string;
  module: string;
  user_id: string;
  role: Role;
  scope: Scope;
  entity_scope: string[];
  employee_id: string | null;
  clearance: string;
  permissions: string[];
}

export interface Versions {
  model: string;
  prompts: string;
  data: number;
  knowledge: number;
}

/** A reviewer-approved example that guided an answer. */
export interface FeedbackUsed {
  example_id: string;
  version: number;
  similarity: number;
}

export interface QueryResponse {
  request_id: string;
  outcome: Outcome;
  reason_code: ReasonCode | null;
  message: string | null;
  answer: string | null;
  retrieval_mode: RetrievalMode | null;
  confidence: Confidence | null;
  citations: Citation[];
  computation: Computation | null;
  /** May be missing from an older backend. */
  feedback_applied?: FeedbackUsed[];
  context: QueryContext;
  versions: Versions;
  cached: boolean;
  latency_ms: number;
}

// ---- POST /api/v1/exports (the response is the file itself) ----

export type ExportFormat = 'json' | 'xlsx' | 'pdf';

export interface ExportRecordsFilter {
  date_from?: string;
  date_to?: string;
  entity_id?: string;
  employee_id?: string;
  status?: AttendanceStatus;
  review_status?: ReviewStatus;
  source_file?: string;
}

/** Either a question's `request_id`, or a `records` filter (none: everything the caller may see). */
export interface ExportRequest {
  format: ExportFormat;
  request_id?: string;
  records?: ExportRecordsFilter;
}

// ---- Feedback/training: /api/v1/feedback ----

export type ExampleStatus = 'active' | 'inactive' | 'rejected';

export interface FeedbackRequest {
  request_id: string;
  feedback: string;
  ideal_final_output: string;
  approval_note?: string;
}

export interface FeedbackScope {
  scope: Scope;
  entity_scope: string[];
  employee_id: string | null;
  clearance: string;
}

export interface FeedbackReplay {
  request_id: string;
  outcome: string;
  reason_code: string | null;
  answer: string | null;
  similarity: number;
  numbers_missing: string[];
  feedback_applied: FeedbackUsed[];
  confidence: Confidence | null;
}

export interface FeedbackExample {
  example_id: string;
  status: ExampleStatus;
  version: number;
  scope: FeedbackScope;
  request_id: string;
  question: string;
  feedback: string;
  ideal_output: string;
  reviewer_id: string;
  approval_note: string | null;
  problems: string[];
  replay: FeedbackReplay | null;
  created_at: string;
  deactivated_at: string | null;
  deactivated_by: string | null;
  knowledge_version: number | null;
  message: string | null;
}

export interface FeedbackListResponse {
  items: FeedbackExample[];
}
