import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import LoginRoute from './LoginRoute';

/**
 * "Sign in with Microsoft" behaviour on the DataKompas login screen.
 *
 * The button is gated on the backend's ``/api/auth/config`` advertisement:
 * hidden when ``sso_enabled:false``, shown when ``true``. Clicking it does a
 * full-page navigation to the OIDC login endpoint with the post-login
 * destination round-tripped through ``?return=``. A failed callback bounces
 * back with ``?sso_error=1`` and we render an inline notice.
 *
 * ``fetchAuthConfig`` is mocked per-test; ``useAuth`` is stubbed to a
 * settled, unauthenticated state so the login card actually renders.
 */

const fetchAuthConfigMock = vi.fn();

vi.mock('../api/client', () => ({
  // Only the auth-config probe matters here; the password helpers and ApiError
  // must still exist as named exports for the component import to resolve.
  fetchAuthConfig: () => fetchAuthConfigMock(),
  login: vi.fn(),
  register: vi.fn(),
  ApiError: class ApiError extends Error {},
}));

vi.mock('../hooks/useAuth', () => ({
  useAuth: () => ({
    userId: null,
    email: null,
    isAuthenticated: false,
    loading: false,
    handleLogin: vi.fn(),
    handleLogout: vi.fn(),
  }),
}));

const BUTTON_LABEL = 'Aanmelden met Microsoft';

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <LoginRoute />
    </MemoryRouter>,
  );
}

describe('LoginRoute — Sign in with Microsoft', () => {
  beforeEach(() => {
    fetchAuthConfigMock.mockReset();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('hides the Microsoft button when sso_enabled is false', async () => {
    fetchAuthConfigMock.mockResolvedValue({ sso_enabled: false, sso_provider: null });
    renderAt('/login');
    // The password form is up immediately...
    expect(screen.getByLabelText('E-mailadres')).toBeInTheDocument();
    // ...and the SSO probe resolves without ever rendering the button.
    await waitFor(() => expect(fetchAuthConfigMock).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: BUTTON_LABEL })).not.toBeInTheDocument();
  });

  it('shows the Microsoft button when sso_enabled is true', async () => {
    fetchAuthConfigMock.mockResolvedValue({ sso_enabled: true, sso_provider: 'microsoft' });
    renderAt('/login');
    expect(await screen.findByRole('button', { name: BUTTON_LABEL })).toBeInTheDocument();
  });

  it('navigates to the OIDC login URL with the encoded return target on click', async () => {
    fetchAuthConfigMock.mockResolvedValue({ sso_enabled: true, sso_provider: 'microsoft' });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    renderAt('/login?return=' + encodeURIComponent('/f/abc?x=1'));
    const button = await screen.findByRole('button', { name: BUTTON_LABEL });
    button.click();

    expect(assign).toHaveBeenCalledWith(
      '/api/auth/oidc/login?return=' + encodeURIComponent('/f/abc?x=1'),
    );
  });

  it('falls back to "/" as the return target when no return param is present', async () => {
    fetchAuthConfigMock.mockResolvedValue({ sso_enabled: true, sso_provider: 'microsoft' });
    const assign = vi.fn();
    vi.stubGlobal('location', { ...window.location, assign });

    renderAt('/login');
    const button = await screen.findByRole('button', { name: BUTTON_LABEL });
    button.click();

    expect(assign).toHaveBeenCalledWith(
      '/api/auth/oidc/login?return=' + encodeURIComponent('/'),
    );
  });

  it('renders the inline error when sso_error=1 is in the URL', async () => {
    fetchAuthConfigMock.mockResolvedValue({ sso_enabled: true, sso_provider: 'microsoft' });
    renderAt('/login?sso_error=1');
    expect(
      await screen.findByText('Inloggen met Microsoft mislukt, probeer opnieuw.'),
    ).toBeInTheDocument();
  });
});
