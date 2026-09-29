import type { DownloadedFile } from '../api/client';

/** Saves a downloaded file through a temporary, same-origin object URL. */
export function saveFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = file.filename;
  link.rel = 'noopener';
  link.hidden = true;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke later: some browsers read the URL after click() returns.
  window.setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
