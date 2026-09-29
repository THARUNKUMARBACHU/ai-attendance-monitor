import { useEffect, useState } from 'react';
import { useFeedback } from '../lib/feedback';

async function copyText(value: string): Promise<boolean> {
  try {
    if (window.isSecureContext && navigator.clipboard) {
      await navigator.clipboard.writeText(value);
      return true;
    }
  } catch {
    // Fall back to a temporary text area below.
  }
  const area = document.createElement('textarea');
  area.value = value;
  area.setAttribute('readonly', '');
  area.className = 'offscreen';
  document.body.append(area);
  area.select();
  let copied = false;
  try {
    copied = document.execCommand('copy');
  } catch {
    copied = false;
  }
  area.remove();
  return copied;
}

export function CopyButton({ text, label }: { text: string; label: string }) {
  const { announce } = useFeedback();
  const [state, setState] = useState<'idle' | 'copied' | 'failed'>('idle');

  useEffect(() => {
    if (state === 'idle') return undefined;
    const timer = window.setTimeout(() => setState('idle'), 2000);
    return () => window.clearTimeout(timer);
  }, [state]);

  async function copy() {
    const copied = await copyText(text);
    setState(copied ? 'copied' : 'failed');
    announce(copied ? 'Copied to the clipboard.' : 'Could not copy. Select the text and copy it manually.');
  }

  return (
    <button type="button" className="btn btn--small" onClick={() => void copy()}>
      {state === 'copied' ? 'Copied' : state === 'failed' ? 'Copy failed' : label}
    </button>
  );
}
