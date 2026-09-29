import { useEffect, useRef } from 'react';
import type { ErrorInfo } from '../lib/errors';

/** Title, detail, validation issues and request ID of a problem response. */
export function ProblemSummary({ info }: { info: ErrorInfo }) {
  return (
    <div className="problem">
      <p className="problem-title">{info.title}</p>
      {info.detail && <p className="problem-detail">{info.detail}</p>}
      {info.issues.length > 0 && (
        <ul className="problem-issues">
          {info.issues.map((issue, index) => (
            <li key={index}>{issue}</li>
          ))}
        </ul>
      )}
      {info.requestId && (
        <p className="problem-meta">
          Request ID <code>{info.requestId}</code>
        </p>
      )}
    </div>
  );
}

/** The page-level, dismissible error banner. The alert region stays mounted so new errors are announced. */
export function ErrorBanner({ error, onDismiss }: { error: ErrorInfo | null; onDismiss: () => void }) {
  const bannerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (error) bannerRef.current?.scrollIntoView({ block: 'nearest' });
  }, [error]);

  return (
    <div className="banner-region" role="alert">
      {error && (
        <div className="banner" ref={bannerRef}>
          <ProblemSummary info={error} />
          <button type="button" className="icon-button" onClick={onDismiss} aria-label="Dismiss error">
            <span aria-hidden="true">×</span>
          </button>
        </div>
      )}
    </div>
  );
}
