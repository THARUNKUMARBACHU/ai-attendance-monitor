import { useId } from 'react';
import type { Confidence } from '../api/types';
import { clamp } from '../lib/format';
import { BAND, describe } from '../lib/labels';
import { Pill } from './StatusPill';

export function ConfidenceMeter({ confidence }: { confidence: Confidence }) {
  const labelId = useId();
  const score = typeof confidence.score === 'number' && Number.isFinite(confidence.score) ? confidence.score : 0;
  const percent = Math.round(clamp(score, 0, 1) * 100);
  const band = describe(BAND, confidence.band);
  const reasons = Array.isArray(confidence.reasons) ? confidence.reasons : [];

  return (
    <div className="confidence">
      <div className="confidence-row">
        <span id={labelId} className="confidence-label">
          Confidence
        </span>
        <div
          className="meter"
          role="meter"
          aria-labelledby={labelId}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={percent}
          aria-valuetext={`${percent}%, ${band.label.toLowerCase()} confidence`}
        >
          <span className={`meter-fill meter-fill--${band.tone}`} style={{ width: `${percent}%` }} />
        </div>
        <span className="confidence-value">
          <strong>{percent}%</strong>
          <Pill tone={band.tone}>{band.label}</Pill>
        </span>
      </div>
      {reasons.length > 0 && (
        <ul className="confidence-reasons">
          {reasons.map((reason, index) => (
            <li key={index}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
