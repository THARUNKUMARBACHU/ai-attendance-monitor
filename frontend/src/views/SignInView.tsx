import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from 'react';
import { api, ApiError, tokenStore } from '../api/client';
import type { DevUser } from '../api/types';
import { Skeleton, Spinner } from '../components/Primitives';
import { RoleBadge } from '../components/StatusPill';
import { isSilentError } from '../lib/errors';
import { useFeedback } from '../lib/feedback';

type LoadState =
  | { status: 'loading' }
  | { status: 'ready'; users: DevUser[]; accessCodeRequired: boolean }
  | { status: 'disabled' }
  | { status: 'failed' };

interface TenantGroup {
  tenantId: string;
  name: string;
  users: DevUser[];
}

function groupByTenant(users: readonly DevUser[]): TenantGroup[] {
  const groups = new Map<string, TenantGroup>();
  for (const user of users) {
    const group = groups.get(user.tenant_id) ?? { tenantId: user.tenant_id, name: user.tenant_name || user.tenant_id, users: [] };
    group.users.push(user);
    groups.set(user.tenant_id, group);
  }
  return [...groups.values()];
}

export function SignInView({
  message,
  banner,
  onSignedIn,
}: {
  /** Why the user is here, e.g. after a 401 or a sign-out. */
  message: string | null;
  banner: ReactNode;
  /** Loads the profile for the stored token; resolves false if that failed. */
  onSignedIn: () => Promise<boolean>;
}) {
  const { showError, clearError, announce } = useFeedback();
  const uid = useId();
  const [state, setState] = useState<LoadState>({ status: 'loading' });
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [accessCode, setAccessCode] = useState('');
  const groups = useMemo(() => (state.status === 'ready' ? groupByTenant(state.users) : []), [state]);
  const codeRequired = state.status === 'ready' && state.accessCodeRequired;
  const codeMissing = codeRequired && accessCode.trim() === '';

  const load = useCallback(
    async (signal?: AbortSignal) => {
      setState({ status: 'loading' });
      try {
        const data = await api.devUsers(signal);
        setState({
          status: 'ready',
          users: Array.isArray(data.users) ? data.users : [],
          accessCodeRequired: data.access_code_required === true,
        });
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) {
          setState({ status: 'disabled' });
        } else if (!isSilentError(error)) {
          setState({ status: 'failed' });
          showError(error);
        }
      }
    },
    [showError],
  );

  useEffect(() => {
    document.title = 'Sign in · Attendance Intelligence';
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  async function signIn(user: DevUser) {
    setPendingUser(user.user_id);
    clearError();
    announce(`Signing in as ${user.display_name}…`);
    try {
      const token = await api.devToken(user.user_id, codeRequired ? accessCode.trim() : undefined);
      if (typeof token.access_token !== 'string' || token.access_token === '') {
        throw new Error('The server did not return an access token.');
      }
      if (!tokenStore.set(token.access_token)) {
        throw new Error('This browser is blocking session storage, which is needed to keep you signed in.');
      }
      if (!(await onSignedIn())) tokenStore.clear();
    } catch (error) {
      showError(error);
    } finally {
      setPendingUser(null);
    }
  }

  return (
    <main id="main" tabIndex={-1} className="signin container">
      <div className="signin-head">
        <span className="brand-mark brand-mark--large" aria-hidden="true" />
        <h1>Attendance Intelligence</h1>
      </div>
      {banner}
      {message && <p className="notice">{message}</p>}

      {state.status === 'disabled' ? (
        <p className="card signin-idp">Sign-in is provided by your organisation's identity provider.</p>
      ) : (
        <>
          <p className="signin-note">
            {codeRequired
              ? 'Development sign-in: enter the access code from your invitation, then pick a demo user.'
              : 'Development sign-in: pick a demo user. No passwords in this build.'}
          </p>
          {codeRequired && (
            <div className="field signin-code">
              <label htmlFor={`${uid}-code`} className="field-label">
                Access code
              </label>
              <input
                id={`${uid}-code`}
                className="input"
                type="password"
                autoComplete="off"
                spellCheck={false}
                value={accessCode}
                onChange={(event) => setAccessCode(event.target.value)}
              />
            </div>
          )}
          <div aria-busy={state.status === 'loading'}>
            {state.status === 'loading' && (
              <div className="user-grid">
                <Skeleton lines={3} />
                <Skeleton lines={3} />
                <Skeleton lines={3} />
              </div>
            )}
            {state.status === 'failed' && (
              <div className="card signin-failed">
                <p>The demo users could not be loaded.</p>
                <button type="button" className="btn" onClick={() => void load()}>
                  Try again
                </button>
              </div>
            )}
            {state.status === 'ready' && groups.length === 0 && (
              <p className="card">No demo users are configured.</p>
            )}
            {groups.map((group, index) => (
              <section key={group.tenantId} className="tenant-group" aria-labelledby={`${uid}-tenant-${index}`}>
                <h2 id={`${uid}-tenant-${index}`} className="tenant-name">
                  {group.name}
                </h2>
                <ul className="user-grid">
                  {group.users.map((user) => (
                    <li key={user.user_id}>
                      <button
                        type="button"
                        className="user-card"
                        disabled={pendingUser !== null || codeMissing}
                        onClick={() => void signIn(user)}
                      >
                        <span className="user-card-top">
                          <span className="user-card-name">{user.display_name}</span>
                          <RoleBadge role={user.role} />
                        </span>
                        <span className="user-card-scope">{user.scope_description}</span>
                        <span className="user-card-id">{user.user_id}</span>
                        {pendingUser === user.user_id && (
                          <span className="user-card-status">
                            <Spinner /> Signing in…
                          </span>
                        )}
                      </button>
                    </li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        </>
      )}
    </main>
  );
}
