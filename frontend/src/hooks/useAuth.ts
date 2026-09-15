import { useState, useCallback, useEffect } from 'react';
import type { TokenResponse } from '../types';
import { fetchMe, logout as apiLogout } from '../api/client';

interface AuthState {
  userId: string | null;
  email: string | null;
  isAuthenticated: boolean;
  // `loading` covers the brief moment on mount before we've checked
  // the cookie via /api/auth/me. Components should render a splash
  // while it's true instead of flashing the login page.
  loading: boolean;
}

const INITIAL: AuthState = {
  userId: null,
  email: null,
  isAuthenticated: false,
  loading: true,
};

export function useAuth() {
  const [auth, setAuth] = useState<AuthState>(INITIAL);

  useEffect(() => {
    let cancelled = false;
    fetchMe()
      .then((me) => {
        if (cancelled) return;
        if (me) {
          setAuth({
            userId: me.user_id,
            email: me.email,
            isAuthenticated: true,
            loading: false,
          });
        } else {
          setAuth({ ...INITIAL, loading: false });
        }
      })
      .catch(() => {
        if (!cancelled) setAuth({ ...INITIAL, loading: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleLogin = useCallback((response: TokenResponse) => {
    setAuth({
      userId: response.user_id,
      email: response.email,
      isAuthenticated: true,
      loading: false,
    });
  }, []);

  const handleLogout = useCallback(async () => {
    try {
      await apiLogout();
    } catch {
      // Even if the backend call fails (network, already expired),
      // clear the local state so the UI returns to the login screen.
    }
    setAuth({ ...INITIAL, loading: false });
  }, []);

  return { ...auth, handleLogin, handleLogout };
}
