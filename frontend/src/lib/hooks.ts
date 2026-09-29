import { useEffect, useRef, useSyncExternalStore } from 'react';

/**
 * Calls `callback` every `delayMs` until the delay becomes null or the component unmounts.
 * Ticks are chained with setTimeout, so a slow request never overlaps the next one.
 */
export function usePolling(callback: () => unknown, delayMs: number | null): void {
  const latest = useRef(callback);
  useEffect(() => {
    latest.current = callback;
  });

  useEffect(() => {
    if (delayMs === null) return undefined;
    let cancelled = false;
    let timer = 0;
    const tick = async (): Promise<void> => {
      try {
        await latest.current();
      } catch {
        // The callback reports its own errors.
      }
      if (!cancelled) timer = window.setTimeout(() => void tick(), delayMs);
    };
    timer = window.setTimeout(() => void tick(), delayMs);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [delayMs]);
}

function subscribeToVisibility(onChange: () => void): () => void {
  document.addEventListener('visibilitychange', onChange);
  return () => document.removeEventListener('visibilitychange', onChange);
}

/** False while the browser tab is hidden, so polling can pause. */
export function usePageVisible(): boolean {
  return useSyncExternalStore(subscribeToVisibility, () => document.visibilityState !== 'hidden');
}

/** A ref for a view's heading, focused whenever the view becomes active. */
export function useFocusWhenActive<T extends HTMLElement>(active: boolean) {
  const ref = useRef<T>(null);
  useEffect(() => {
    if (active) ref.current?.focus();
  }, [active]);
  return ref;
}

export function prefersReducedMotion(): boolean {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
