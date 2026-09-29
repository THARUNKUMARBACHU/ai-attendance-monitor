import { useState } from 'react';
import { api } from '../api/client';
import type { ExportFormat, ExportRequest } from '../api/types';
import { saveFile } from '../lib/download';
import { isSilentError } from '../lib/errors';
import { useFeedback } from '../lib/feedback';
import { Spinner } from './Primitives';

const FORMATS: ReadonlyArray<{ format: ExportFormat; label: string }> = [
  { format: 'json', label: 'JSON' },
  { format: 'xlsx', label: 'Excel' },
  { format: 'pdf', label: 'PDF' },
];

/** JSON, Excel and PDF downloads of the same dataset; the server re-checks access for each one. */
export function ExportButtons({ label, request }: { label: string; request: (format: ExportFormat) => ExportRequest }) {
  const { showError, announce } = useFeedback();
  const [busy, setBusy] = useState<ExportFormat | null>(null);

  async function run(format: ExportFormat) {
    setBusy(format);
    try {
      const file = await api.exportFile(request(format));
      saveFile(file);
      announce(`Downloaded ${file.filename}.`);
    } catch (error) {
      if (!isSilentError(error)) showError(error);
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="export-buttons" role="group" aria-label={label}>
      <span className="export-label">Export</span>
      {FORMATS.map(({ format, label: text }) => (
        <button
          key={format}
          type="button"
          className="btn btn--small"
          disabled={busy !== null}
          aria-busy={busy === format}
          onClick={() => void run(format)}
        >
          {busy === format && <Spinner />}
          {text}
        </button>
      ))}
    </div>
  );
}
