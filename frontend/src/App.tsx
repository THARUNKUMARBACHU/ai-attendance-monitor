import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { api, cancelAllRequests, setUnauthorizedHandler, tokenStore } from './api/client';
import type { Me } from './api/types';
import { AppShell } from './components/AppShell';
import { ErrorBanner } from './components/ErrorBanner';
import { Spinner } from './components/Primitives';
import { describeError, type ErrorInfo } from './lib/errors';
import { FeedbackContext, type Feedback } from './lib/feedback';
import { SignInView } from './views/SignInView';

type Session =
  | { status: 'booting' }
  | { status: 'boot-failed' }
  | { status: 'signed-out'; message: string | null }
  | { status: 'signed-in'; me: Me };

const SESSION_EXPIRED = 'Your session has expired or is no longer valid. Please sign in again.';

function BootScreen({
  failed,
  banner,
  onRetry,
  onSignIn,
}: {
  failed: boolean;
  banner: ReactNode;
  onRetry: () => void;
  onSignIn: () => void;
}) {
  return (
    <main id="main" tabIndex={-1} className="signin container">
      <div className="signin-head">
        <span className="brand-mark brand-mark--large" aria-hidden="true" />
        <h1>Attendance Intelligence</h1>
      </div>
      {banner}
      {failed ? (
        <div className="card signin-failed">
          <p>Your profile could not be loaded.</p>
          <div className="button-row">
            <button type="button" className="btn btn--primary" onClick={onRetry}>
              Try again
            </button>
            <button type="button" className="btn" onClick={onSignIn}>
              Sign in again
            </button>
          </div>
        </div>
      ) : (
        <p className="muted state-line">
          <Spinner /> Loading your workspace…
        </p>
      )}
    </main>
  );
}

export function App() {
  const [session, setSession] = useState<Session>(() =>
    tokenStore.get() ? { status: 'booting' } : { status: 'signed-out', message: null },
  );
  const [error, setError] = useState<ErrorInfo | null>(null);
  const [liveMessage, setLiveMessage] = useState('');

  const announce = useCallback((message: string) => {
    // Clear first so repeating the same message is announced again.
    setLiveMessage('');
    window.requestAnimationFrame(() => setLiveMessage(message));
  }, []);
  const showError = useCallback((reason: unknown) => {
    const info = describeError(reason);
    if (info) setError(info);
  }, []);
  const clearError = useCallback(() => setError(null), []);
  const feedback = useMemo<Feedback>(() => ({ showError, clearError, announce }), [showError, clearError, announce]);

  const signOut = useCallback((message: string | null) => {
    cancelAllRequests();
    tokenStore.clear();
    setError(null);
    setSession({ status: 'signed-out', message });
  }, []);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      signOut(SESSION_EXPIRED);
      announce(SESSION_EXPIRED);
    });
    return () => setUnauthorizedHandler(null);
  }, [signOut, announce]);

  /** Loads /me with the stored token; resolves true once signed in. */
  const loadProfile = useCallback(async (): Promise<boolean> => {
    try {
      const me = await api.me();
      setError(null);
      setSession({ status: 'signed-in', me });
      announce(`Signed in as ${me.display_name}.`);
      return true;
    } catch (reason) {
      showError(reason);
      return false;
    }
  }, [announce, showError]);

  const resume = useCallback(async () => {
    setSession({ status: 'booting' });
    const ok = await loadProfile();
    // A 401 has already returned to sign-in; anything else offers a retry.
    if (!ok && tokenStore.get()) setSession({ status: 'boot-failed' });
  }, [loadProfile]);

  const booted = useRef(false);
  useEffect(() => {
    if (booted.current) return;
    booted.current = true;
    if (tokenStore.get()) void resume();
  }, [resume]);

  const banner = <ErrorBanner error={error} onDismiss={clearError} />;

  function renderScreen(): ReactNode {
    switch (session.status) {
      case 'signed-in':
        return (
          <AppShell
            me={session.me}
            banner={banner}
            onSignOut={() => {
              signOut('You have been signed out.');
              announce('Signed out.');
            }}
          />
        );
      case 'signed-out':
        return <SignInView message={session.message} banner={banner} onSignedIn={loadProfile} />;
      case 'booting':
      case 'boot-failed':
        return (
          <BootScreen
            failed={session.status === 'boot-failed'}
            banner={banner}
            onRetry={() => void resume()}
            onSignIn={() => signOut(null)}
          />
        );
    }
  }

  return (
    <FeedbackContext value={feedback}>
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      {renderScreen()}
      <div className="visually-hidden" aria-live="polite" aria-atomic="true">
        {liveMessage}
      </div>
    </FeedbackContext>
  );
}
