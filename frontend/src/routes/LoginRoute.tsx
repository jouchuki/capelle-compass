import { useEffect, useState, type FormEvent } from 'react';
import { Navigate, useNavigate, useSearchParams } from 'react-router-dom';
import { motion } from 'motion/react';
import { Loader2 } from 'lucide-react';
import { useAuth } from '../hooks/useAuth';
import { login, register, fetchAuthConfig, ApiError } from '../api/client';

type Mode = 'login' | 'register';

/**
 * The Microsoft four-square mark, rendered inline so the SSO button looks
 * native without pulling in an icon dependency. Decorative — the button's
 * own text carries the accessible label.
 */
function MicrosoftLogo() {
  return (
    <svg
      aria-hidden="true"
      width="18"
      height="18"
      viewBox="0 0 21 21"
      className="shrink-0"
    >
      <rect x="1" y="1" width="9" height="9" fill="#f25022" />
      <rect x="11" y="1" width="9" height="9" fill="#7fba00" />
      <rect x="1" y="11" width="9" height="9" fill="#00a4ef" />
      <rect x="11" y="11" width="9" height="9" fill="#ffb900" />
    </svg>
  );
}

/**
 * Auth landing — centred card, two stacked fields, blue submit. Doubles as
 * the registration screen via a small toggle below the form. We honour
 * an optional ``?return=`` query param so a deep-link to ``/f/:token`` can
 * round-trip the user back to the shared analysis after sign-in.
 */
export default function LoginRoute() {
  const { isAuthenticated, loading, handleLogin } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [mode, setMode] = useState<Mode>('login');
  const [email, setEmail] = useState<string>('');
  const [password, setPassword] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [ssoEnabled, setSsoEnabled] = useState<boolean>(false);

  // Post-login destination: the same ``?return=`` deep-link the password
  // form honours. Kept same-site (must start with '/') and defaults to '/'.
  const returnParam = params.get('return');
  const returnTarget = returnParam && returnParam.startsWith('/') ? returnParam : '/';
  // The OIDC callback bounces back with ``?sso_error=1`` when federated
  // sign-in failed; surface a small inline notice in that case.
  const ssoError = params.get('sso_error') === '1';

  // Ask the backend (once, on mount) whether SSO is on. In mock mode the
  // client returns ``sso_enabled:false`` so the mock UI is unchanged. Any
  // failure leaves the button hidden — the password form still works.
  useEffect(() => {
    let cancelled = false;
    fetchAuthConfig()
      .then((cfg) => {
        if (!cancelled) setSsoEnabled(cfg.sso_enabled);
      })
      .catch(() => {
        if (!cancelled) setSsoEnabled(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleMicrosoftLogin = () => {
    window.location.assign(
      '/api/auth/oidc/login?return=' + encodeURIComponent(returnTarget),
    );
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <Loader2 className="w-5 h-5 animate-spin text-slate-300" aria-label="Laden" />
      </div>
    );
  }

  if (isAuthenticated) {
    return <Navigate to={returnTarget} replace />;
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) return;
    if (!email.trim() || !password) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = mode === 'login' ? await login(email.trim(), password) : await register(email.trim(), password);
      handleLogin(response);
      navigate(returnTarget, { replace: true });
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? typeof err.detail === 'string'
            ? err.detail
            : err.message
          : 'Inloggen mislukt. Controleer je gegevens en probeer het opnieuw.';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  const eyebrow = mode === 'login' ? 'Inloggen' : 'Registreren';

  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-4">
      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ type: 'spring', damping: 30, stiffness: 200 }}
        className="max-w-md w-full bg-white rounded-2xl border border-slate-100 artifact-shadow p-10"
      >
        <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">{eyebrow}</p>
        <h1 className="mt-2 text-3xl font-bold text-slate-900 tracking-tight">DataKompas</h1>
        <p className="mt-2 text-sm text-slate-500">Gemeentelijke data-analyse</p>

        <form onSubmit={handleSubmit} className="mt-8 space-y-5">
          <div>
            <label
              htmlFor="login-email"
              className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2"
            >
              E-mailadres
            </label>
            <input
              id="login-email"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="naam@capelle.nl"
              className="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-300 transition-all placeholder:text-slate-300"
            />
          </div>
          <div>
            <label
              htmlFor="login-password"
              className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2"
            >
              Wachtwoord
            </label>
            <input
              id="login-password"
              type="password"
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
              required
              minLength={6}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Wachtwoord (min. 6 tekens)"
              className="w-full bg-white border border-slate-200 rounded-lg px-4 py-3 text-sm focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-300 transition-all placeholder:text-slate-300"
            />
          </div>

          {error ? <p className="text-[11px] text-red-600">{error}</p> : null}

          <button
            type="submit"
            disabled={submitting || !email.trim() || !password}
            className="w-full inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg py-3 text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {submitting ? (
              <Loader2 className="w-4 h-4 animate-spin" aria-label="Bezig" />
            ) : mode === 'login' ? (
              'Inloggen'
            ) : (
              'Registreren'
            )}
          </button>
        </form>

        {ssoEnabled ? (
          <div className="mt-6">
            <div className="flex items-center gap-3" aria-hidden="true">
              <span className="h-px flex-1 bg-slate-200" />
              <span className="text-[11px] uppercase tracking-wider font-bold text-slate-400">
                of
              </span>
              <span className="h-px flex-1 bg-slate-200" />
            </div>

            {ssoError ? (
              <p className="mt-4 text-[11px] text-red-600" role="alert">
                Inloggen met Microsoft mislukt, probeer opnieuw.
              </p>
            ) : null}

            <button
              type="button"
              onClick={handleMicrosoftLogin}
              className="mt-4 w-full inline-flex items-center justify-center gap-3 bg-white hover:bg-slate-50 text-slate-700 border border-slate-200 rounded-lg py-3 text-sm font-semibold transition-all"
            >
              <MicrosoftLogo />
              Aanmelden met Microsoft
            </button>
          </div>
        ) : null}

        <p className="mt-6 text-xs text-slate-500 text-center">
          {mode === 'login' ? 'Nog geen account? ' : 'Al een account? '}
          <button
            type="button"
            onClick={() => {
              setMode(mode === 'login' ? 'register' : 'login');
              setError(null);
            }}
            className="text-blue-700 underline decoration-blue-200 hover:decoration-blue-400 transition-colors"
          >
            {mode === 'login' ? 'Registreren' : 'Inloggen'}
          </button>
        </p>
      </motion.div>
    </div>
  );
}
