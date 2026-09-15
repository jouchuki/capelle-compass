import type { ReactNode } from 'react';
import { Link, NavLink, Navigate, useLocation } from 'react-router-dom';
import { useAdminCheck } from '../../hooks/useAdminCheck';
import NotFound from '../NotFound';
import Spinner from './Spinner';

interface AdminShellProps {
  children: ReactNode;
}

interface TabDef {
  to: string;
  label: string;
  end?: boolean;
}

const TABS: TabDef[] = [
  { to: '/admin', label: 'Overzicht', end: true },
  { to: '/admin/questions', label: 'Vragen' },
  { to: '/admin/feedback', label: 'Feedback' },
  { to: '/admin/tokens', label: 'Tokens' },
  { to: '/admin/engagement', label: 'Engagement' },
  { to: '/admin/users', label: 'Gebruikers' },
  { to: '/admin/codex', label: 'Codex' },
];

export default function AdminShell({ children }: AdminShellProps) {
  const access = useAdminCheck();
  const location = useLocation();

  if (access.kind === 'loading') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-[#FAFAFA]">
        <Spinner label="Toegang controleren" />
      </div>
    );
  }

  // Bookmarked /admin from a fresh browser — bounce through login first
  // so they don't see a confusing 404 when they really just need to sign in.
  if (access.kind === 'unauth') {
    const returnTo = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?return=${returnTo}`} replace />;
  }

  // Authenticated but not on the admin allowlist → hide the surface entirely.
  if (access.kind === 'denied') {
    return <NotFound />;
  }

  const email = access.email;

  return (
    <div className="min-h-screen bg-[#FAFAFA] text-slate-900">
      <header className="sticky top-0 z-20 bg-white/90 backdrop-blur border-b border-slate-100">
        <div className="max-w-7xl mx-auto px-6 py-3 flex items-center justify-between">
          <div className="flex flex-col leading-tight">
            <Link
              to="/"
              className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400 hover:text-blue-700 transition-colors w-fit"
            >
              DataKompas
            </Link>
            <span className="text-sm font-semibold text-slate-900">Admin</span>
          </div>
          <div className="flex items-center gap-4 text-sm">
            {email ? <span className="text-slate-500">{email}</span> : null}
            <Link
              to="/"
              className="font-semibold text-blue-700 hover:text-blue-800"
            >
              Terug naar app
            </Link>
          </div>
        </div>
        <nav className="max-w-7xl mx-auto px-6 flex items-center gap-6 overflow-x-auto custom-scrollbar">
          {TABS.map((tab) => (
            <NavLink
              key={tab.to}
              to={tab.to}
              end={tab.end}
              className={({ isActive }) =>
                [
                  'py-3 text-sm font-semibold whitespace-nowrap border-b-2 transition-colors',
                  isActive
                    ? 'text-blue-700 border-blue-600'
                    : 'text-slate-500 hover:text-slate-900 border-transparent',
                ].join(' ')
              }
            >
              {tab.label}
            </NavLink>
          ))}
        </nav>
      </header>
      <main className="max-w-7xl mx-auto px-6 py-6">{children}</main>
    </div>
  );
}
