import type { ReactNode } from 'react';
import { describe, lookup, ROLE_LABEL, type Label, type Tone } from '../lib/labels';
import { humanise } from '../lib/format';

export function Pill({ tone, children, title }: { tone: Tone; children: ReactNode; title?: string | undefined }) {
  return (
    <span className={`pill pill--${tone}`} title={title}>
      {children}
    </span>
  );
}

/** A coloured pill with its text label, for any status value in a label map. */
export function StatusPill({ map, value }: { map: Readonly<Record<string, Label>>; value: string | null | undefined }) {
  const { label, tone } = describe(map, value);
  return <Pill tone={tone}>{label}</Pill>;
}

const ROLE_CLASS: Readonly<Record<string, string>> = { admin: 'admin', manager: 'manager', employee: 'employee' };

export function RoleBadge({ role }: { role: string }) {
  const label = lookup(ROLE_LABEL, role) ?? humanise(role);
  return <span className={`badge badge--${lookup(ROLE_CLASS, role) ?? 'other'}`}>{label}</span>;
}
