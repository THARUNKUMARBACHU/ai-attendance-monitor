import type {
  AttendanceStatus,
  CheckName,
  CheckStatus,
  ConfidenceBand,
  ExampleStatus,
  JobCounts,
  JobStatus,
  Outcome,
  ReasonCode,
  ReviewStatus,
  Role,
  StageName,
  StageStatus,
} from '../api/types';
import { humanise } from './format';

/** Visual tone; always paired with a text label, never colour alone. */
export type Tone = 'good' | 'warning' | 'critical' | 'info' | 'violet' | 'neutral';

export interface Label {
  label: string;
  tone: Tone;
}

type Labels<K extends string> = Readonly<Record<K, Label>>;

/** Own-property lookup, so values such as "constructor" never hit the prototype. */
export function lookup<V>(map: Readonly<Record<string, V>>, key: string | null | undefined): V | undefined {
  return key && Object.hasOwn(map, key) ? map[key] : undefined;
}

/** The label and tone for a status value; unknown values fall back to a neutral, humanised label. */
export function describe(map: Readonly<Record<string, Label>>, value: string | null | undefined): Label {
  return lookup(map, value) ?? { label: value ? humanise(value) : 'Unknown', tone: 'neutral' };
}

export const OUTCOME: Labels<Outcome> = {
  answered: { label: 'Answered', tone: 'good' },
  unavailable: { label: 'Unavailable', tone: 'warning' },
  denied: { label: 'Denied', tone: 'critical' },
  needs_review: { label: 'Needs review', tone: 'warning' },
  out_of_scope: { label: 'Out of scope', tone: 'neutral' },
};

export const REASON: Readonly<Record<ReasonCode, string>> = {
  no_data: 'No data for the requested period',
  insufficient_evidence: 'Not enough evidence',
  out_of_scope_entity: 'Outside the data you can see',
  other_tenant: 'Not available in your organisation’s data',
  policy_violation: 'Blocked by policy',
  not_attendance: 'Not an attendance question',
  provider_unavailable: 'The language model is unavailable',
  planning_failed: 'The question could not be interpreted',
  query_failed: 'The data could not be queried',
  low_confidence: 'Low confidence',
};

export const EXAMPLE_STATUS: Labels<ExampleStatus> = {
  active: { label: 'Active', tone: 'good' },
  inactive: { label: 'Deactivated', tone: 'neutral' },
  rejected: { label: 'Rejected', tone: 'critical' },
};

export const BAND: Labels<ConfidenceBand> = {
  high: { label: 'High', tone: 'good' },
  medium: { label: 'Medium', tone: 'warning' },
  low: { label: 'Low', tone: 'critical' },
};

export const JOB_STATUS: Labels<JobStatus> = {
  queued: { label: 'Queued', tone: 'neutral' },
  running: { label: 'Running', tone: 'info' },
  completed: { label: 'Completed', tone: 'good' },
  completed_with_errors: { label: 'Completed with errors', tone: 'warning' },
  failed: { label: 'Failed', tone: 'critical' },
  duplicate: { label: 'Duplicate', tone: 'violet' },
};

export const ACTIVE_JOB_STATUSES: ReadonlySet<string> = new Set<JobStatus>(['queued', 'running']);

export const STAGE_STATUS: Labels<StageStatus> = {
  pending: { label: 'Pending', tone: 'neutral' },
  running: { label: 'Running', tone: 'info' },
  done: { label: 'Done', tone: 'good' },
  failed: { label: 'Failed', tone: 'critical' },
  skipped: { label: 'Skipped', tone: 'neutral' },
};

export const STAGE_ORDER: readonly StageName[] = ['validate', 'extract', 'normalise', 'store', 'index'];

export const STAGE_LABEL: Readonly<Record<StageName, string>> = {
  validate: 'Validate',
  extract: 'Extract',
  normalise: 'Normalise',
  store: 'Store',
  index: 'Index',
};

export const CHECK_STATUS: Labels<CheckStatus> = {
  up: { label: 'Up', tone: 'good' },
  down: { label: 'Down', tone: 'critical' },
  configured: { label: 'Configured', tone: 'info' },
  not_configured: { label: 'Not configured', tone: 'neutral' },
};

export const CHECK_LABEL: Readonly<Record<CheckName, string>> = {
  service: 'API service',
  database: 'Database',
  queue: 'Job queue',
  cache: 'Cache',
  search: 'Search',
  vector: 'Vector index',
  model_provider: 'Model provider',
};

export const ATTENDANCE_STATUS: Labels<AttendanceStatus> = {
  PRESENT: { label: 'Present', tone: 'good' },
  ABSENT: { label: 'Absent', tone: 'critical' },
  LEAVE: { label: 'Leave', tone: 'violet' },
  WFH: { label: 'WFH', tone: 'info' },
  HALF_DAY: { label: 'Half day', tone: 'warning' },
  HOLIDAY: { label: 'Holiday', tone: 'neutral' },
  WEEKLY_OFF: { label: 'Weekly off', tone: 'neutral' },
};

export const ATTENDANCE_STATUSES: readonly AttendanceStatus[] = [
  'PRESENT',
  'ABSENT',
  'LEAVE',
  'WFH',
  'HALF_DAY',
  'HOLIDAY',
  'WEEKLY_OFF',
];

export const REVIEW_STATUS: Labels<ReviewStatus> = {
  auto_accepted: { label: 'Auto-accepted', tone: 'neutral' },
  needs_review: { label: 'Needs review', tone: 'warning' },
  verified: { label: 'Verified', tone: 'good' },
  rejected: { label: 'Rejected', tone: 'critical' },
};

export const REVIEW_STATUSES: readonly ReviewStatus[] = ['auto_accepted', 'needs_review', 'verified', 'rejected'];

/** Friendly names for common extraction methods; other values are humanised. */
export const EXTRACTION_LABEL: Readonly<Record<string, string>> = {
  native: 'Native',
  csv: 'CSV',
  xlsx: 'XLSX',
  docx: 'DOCX',
  docx_table: 'DOCX table',
  pdf: 'PDF',
  pdf_text: 'PDF text',
  ocr: 'OCR',
  manual: 'Manual',
};

export const ROLE_LABEL: Readonly<Record<Role, string>> = {
  admin: 'Admin',
  manager: 'Manager',
  employee: 'Employee',
};

export const COUNT_FIELDS: ReadonlyArray<{ key: keyof JobCounts; label: string; flag?: Tone }> = [
  { key: 'rows_read', label: 'Rows read' },
  { key: 'records_created', label: 'Records created' },
  { key: 'records_updated', label: 'Records updated' },
  { key: 'records_unchanged', label: 'Unchanged' },
  { key: 'records_removed', label: 'Removed' },
  { key: 'needs_review', label: 'Needs review', flag: 'warning' },
  { key: 'rejected', label: 'Rejected', flag: 'critical' },
  { key: 'chunks_indexed', label: 'Chunks indexed' },
  { key: 'chunks_quarantined', label: 'Chunks quarantined', flag: 'warning' },
];

export const EXAMPLE_QUESTIONS: Readonly<Record<Role, readonly string[]>> = {
  admin: [
    'Who was present on 5 August 2026?',
    'What was the overall attendance percentage in August 2026?',
    'Which department had the highest attendance in August 2026?',
    'Why was Vikram Reddy on leave on 18 August 2026?',
  ],
  manager: [
    'Who had the highest attendance in my department in August 2026?',
    'What was the attendance percentage for Engineering in August 2026?',
  ],
  employee: ['What was my attendance percentage in August 2026?'],
};
