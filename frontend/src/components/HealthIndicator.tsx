import { useCallback, useEffect, useId, useRef, useState } from 'react';
import { api } from '../api/client';
import type { ReadinessResponse } from '../api/types';
import { isSilentError } from '../lib/errors';
import { formatClock, humanise } from '../lib/format';
import { usePolling } from '../lib/hooks';
import { CHECK_LABEL, CHECK_STATUS, lookup, type Tone } from '../lib/labels';
import { StatusPill } from './StatusPill';

const REFRESH_MS = 60_000;

interface HealthState {
  data: ReadinessResponse | null;
  failed: boolean;
  checkedAt: Date | null;
}

/** Top-bar readiness indicator: a dot plus a word, with the check list on click. */
export function HealthIndicator() {
  const panelId = useId();
  const wrapRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [health, setHealth] = useState<HealthState>({ data: null, failed: false, checkedAt: null });
  const [checking, setChecking] = useState(false);
  const [open, setOpen] = useState(false);

  const check = useCallback(async () => {
    setChecking(true);
    try {
      const data = await api.readiness();
      setHealth({ data, failed: false, checkedAt: new Date() });
    } catch (error) {
      if (!isSilentError(error)) setHealth({ data: null, failed: true, checkedAt: new Date() });
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);
  usePolling(check, REFRESH_MS);

  useEffect(() => {
    if (!open) return undefined;
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrapRef.current?.contains(event.target)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setOpen(false);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  let summary: { label: string; tone: Tone };
  if (health.data) {
    summary = health.data.status === 'ok' ? { label: 'All systems', tone: 'good' } : { label: 'Degraded', tone: 'warning' };
  } else if (health.failed) {
    summary = { label: 'Unreachable', tone: 'critical' };
  } else {
    summary = { label: 'Checking…', tone: 'neutral' };
  }

  return (
    <div className="health" ref={wrapRef}>
      <button
        ref={buttonRef}
        type="button"
        className="health-button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
      >
        <span className={`dot dot--${summary.tone}`} aria-hidden="true" />
        <span className="visually-hidden">System status: </span>
        {summary.label}
      </button>

      <div id={panelId} className="popover" hidden={!open}>
        <div className="popover-head">
          <p className="popover-title">System status</p>
          {health.checkedAt && <span className="muted small">Checked {formatClock(health.checkedAt)}</span>}
        </div>
        {health.data ? (
          <ul className="check-list">
            {Object.entries(health.data.checks).map(([name, result]) => {
              const meta = [
                typeof result.latency_ms === 'number' ? `${result.latency_ms} ms` : null,
                result.detail,
              ].filter(Boolean);
              return (
                <li key={name} className="check">
                  <span className="check-name">{lookup(CHECK_LABEL, name) ?? humanise(name)}</span>
                  <StatusPill map={CHECK_STATUS} value={result.status} />
                  {meta.length > 0 && <span className="check-meta">{meta.join(' · ')}</span>}
                </li>
              );
            })}
          </ul>
        ) : (
          <p className="muted">{health.failed ? 'The health endpoint could not be reached.' : 'Checking…'}</p>
        )}
        <button type="button" className="btn btn--small" onClick={() => void check()} disabled={checking}>
          {checking ? 'Checking…' : 'Check now'}
        </button>
      </div>
    </div>
  );
}
