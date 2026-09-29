import type { Department, Me } from '../api/types';

const DATE = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'short', year: 'numeric' });
const DATE_WITH_WEEKDAY = new Intl.DateTimeFormat(undefined, {
  weekday: 'short',
  day: 'numeric',
  month: 'short',
  year: 'numeric',
});
const DATE_TIME = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });
const CLOCK = new Intl.DateTimeFormat(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });

export const EMPTY = '—';

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Formats a calendar date (YYYY-MM-DD) without shifting it through a time zone. */
export function formatDate(value: string | null | undefined, options: { weekday?: boolean } = {}): string {
  if (!value) return EMPTY;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (!match) return value;
  const date = new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
  return (options.weekday ? DATE_WITH_WEEKDAY : DATE).format(date);
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return EMPTY;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : DATE_TIME.format(date);
}

export function formatClock(date: Date): string {
  return CLOCK.format(date);
}

export function formatNumber(value: number | null | undefined, maximumFractionDigits = 2): string {
  if (typeof value !== 'number' || !Number.isFinite(value)) return EMPTY;
  return value.toLocaleString(undefined, { maximumFractionDigits });
}

/** 0.93 -> "93%" */
export function formatPercent(fraction: number): string {
  return `${Math.round(clamp(fraction, 0, 1) * 100)}%`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (typeof bytes !== 'number' || !Number.isFinite(bytes)) return EMPTY;
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB'];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toLocaleString(undefined, { maximumFractionDigits: 1 })} ${units[unit]}`;
}

export function formatDuration(ms: number | null | undefined): string {
  if (typeof ms !== 'number' || !Number.isFinite(ms)) return EMPTY;
  if (ms < 1000) return `${Math.round(ms)} ms`;
  return `${(ms / 1000).toLocaleString(undefined, { maximumFractionDigits: ms < 10_000 ? 2 : 1 })} s`;
}

export function durationBetween(start: string | null | undefined, end: string | null | undefined): string | null {
  if (!start || !end) return null;
  const ms = new Date(end).getTime() - new Date(start).getTime();
  return Number.isFinite(ms) && ms >= 0 ? formatDuration(ms) : null;
}

/** "completed_with_errors" -> "Completed with errors" */
export function humanise(value: string): string {
  const words = value.replace(/[_-]+/g, ' ').trim().toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function plural(count: number, singular: string, pluralForm = `${singular}s`): string {
  return `${formatNumber(count, 0)} ${count === 1 ? singular : pluralForm}`;
}

export function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

export function departmentName(departments: readonly Department[], id: string | null | undefined): string {
  if (!id) return EMPTY;
  return departments.find((department) => department.entity_id === id)?.name ?? id;
}

/** The user's data scope in words: "whole tenant", "Engineering" or "your own records". */
export function describeScope(me: Me): string {
  if (me.scope === 'tenant') return 'whole tenant';
  if (me.scope === 'self') return 'your own records';
  if (me.scope === 'department') {
    const names = me.entity_scope.map((id) => departmentName(me.departments, id));
    return names.length > 0 ? names.join(', ') : 'your department';
  }
  return humanise(String(me.scope));
}
