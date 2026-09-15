import { useEffect, useState } from 'react';
import { adminCheck } from '../api/client';

/**
 * Authentication + authorization gate for the /admin dashboard.
 *
 * Hits ``GET /api/admin/check`` on mount and exposes one of four states:
 *
 *   - ``"loading"`` — request in flight; render a spinner.
 *   - ``"admin"``   — full access; render the dashboard.
 *   - ``"unauth"``  — no valid session (401). Consumer redirects to
 *                    /login so a bookmarked /admin link from a fresh
 *                    browser routes through sign-in.
 *   - ``"denied"``  — authenticated but not on the admin allowlist
 *                    (403), or backend explicitly returned ``is_admin:
 *                    false``. Consumer renders a 404 (no signal that
 *                    admin even exists).
 *
 * Network errors are mapped to ``"denied"`` so a flaky check never
 * leaks the admin UI on transient failures.
 */
export type AdminAccessState =
  | { kind: 'loading' }
  | { kind: 'admin'; email: string }
  | { kind: 'denied' }
  | { kind: 'unauth' };

export function useAdminCheck(): AdminAccessState {
  const [state, setState] = useState<AdminAccessState>({ kind: 'loading' });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const result = await adminCheck();
        if (cancelled) return;
        setState(result);
      } catch {
        if (cancelled) return;
        setState({ kind: 'denied' });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return state;
}
