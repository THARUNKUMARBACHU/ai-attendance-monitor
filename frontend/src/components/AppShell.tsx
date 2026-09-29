import { useEffect, useState, type ReactNode } from 'react';
import type { Me } from '../api/types';
import { describeScope } from '../lib/format';
import { AskView } from '../views/AskView';
import { FeedbackView } from '../views/FeedbackView';
import { RecordsView } from '../views/RecordsView';
import { UploadView } from '../views/UploadView';
import { HealthIndicator } from './HealthIndicator';
import { RoleBadge } from './StatusPill';

type ViewId = 'ask' | 'upload' | 'records' | 'feedback';

/** Line icons for the side navigation (decorative: the label always follows). */
const ICON_PATHS: Record<ViewId, string> = {
  ask: 'M4 5h16v11H9l-5 4V5z',
  upload: 'M12 15V4M7.5 8.5 12 4l4.5 4.5M4 15v4h16v-4',
  records: 'M4 5h16v14H4zM4 10h16M4 15h16M10 5v14',
  feedback: 'M12 3l7 3v6c0 4-3 7.5-7 9-4-1.5-7-5-7-9V6l7-3zM8.5 12l2.5 2.5 4.5-5',
};

function NavIcon({ view }: { view: ViewId }) {
  return (
    <svg className="side-link-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path
        d={ICON_PATHS[view]}
        fill="none"
        stroke="currentColor"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

/**
 * The signed-in layout: a top bar, a left sidebar for navigation and the main area. Views switch by
 * state (no router); inactive views stay mounted but hidden, so the Ask history, record filters and
 * upload queue survive switching views.
 */
export function AppShell({ me, banner, onSignOut }: { me: Me; banner: ReactNode; onSignOut: () => void }) {
  const canIngest = me.permissions.includes('ingest');
  const canReview = me.permissions.includes('feedback:submit');
  const [view, setView] = useState<ViewId>('ask');

  const items: Array<{ id: ViewId; label: string }> = [{ id: 'ask', label: 'Ask' }];
  if (canIngest) items.push({ id: 'upload', label: 'Upload & jobs' });
  items.push({ id: 'records', label: 'Records' });
  if (canReview) items.push({ id: 'feedback', label: 'Feedback' });
  const currentLabel = items.find((item) => item.id === view)?.label ?? 'Ask';

  useEffect(() => {
    document.title = `${currentLabel} · Attendance Intelligence`;
  }, [currentLabel]);

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">Attendance Intelligence</span>
            {me.tenant_name && (
              <span className="brand-tenant">
                <span className="visually-hidden">Tenant: </span>
                {me.tenant_name}
              </span>
            )}
          </div>
          <div className="topbar-actions">
            <HealthIndicator />
            <div className="user">
              <span className="user-name" title={me.user_id}>
                {me.display_name}
              </span>
              <RoleBadge role={me.role} />
            </div>
            <button type="button" className="btn btn--small" onClick={onSignOut}>
              Sign out
            </button>
          </div>
        </div>
      </header>

      <div className="shell">
        <aside className="sidebar">
          <nav className="side-nav" aria-label="Main">
            <ul>
              {items.map((item) => (
                <li key={item.id}>
                  <button
                    type="button"
                    className="side-link"
                    aria-current={view === item.id ? 'page' : undefined}
                    onClick={() => setView(item.id)}
                  >
                    <NavIcon view={item.id} />
                    {item.label}
                  </button>
                </li>
              ))}
            </ul>
          </nav>
          <p className="sidebar-scope">
            <span className="sidebar-scope-label">You can see</span>
            <strong>{describeScope(me)}</strong>
          </p>
        </aside>

        <div className="content">
          <div className="content-inner">
            {banner}
            <main id="main" tabIndex={-1} className="main">
              <AskView me={me} active={view === 'ask'} />
              {canIngest && <UploadView me={me} active={view === 'upload'} />}
              <RecordsView me={me} active={view === 'records'} />
              {canReview && <FeedbackView me={me} active={view === 'feedback'} />}
            </main>
          </div>
        </div>
      </div>
    </div>
  );
}
