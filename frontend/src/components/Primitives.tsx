import type { ReactNode } from 'react';

export type HeadingLevel = 2 | 3 | 4 | 5;

export function Heading({
  level,
  children,
  className,
  id,
}: {
  level: HeadingLevel;
  children: ReactNode;
  className?: string;
  id?: string;
}) {
  const Tag = `h${level}` as const;
  return (
    <Tag className={className} id={id}>
      {children}
    </Tag>
  );
}

/** One term/value pair inside a <dl className="kv">. */
export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="kv-row">
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  );
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />;
}

export function Skeleton({ lines = 3 }: { lines?: number }) {
  return (
    <div className="skeleton-block" aria-hidden="true">
      {Array.from({ length: lines }, (_, index) => (
        <span key={index} className="skeleton" />
      ))}
    </div>
  );
}
