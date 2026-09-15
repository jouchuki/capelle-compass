/**
 * Mock mode infrastructure.
 *
 * Enabled when:
 *   - import.meta.env.VITE_MOCK_MODE === '1', OR
 *   - URL contains `?mock=1` (persisted to sessionStorage so internal
 *     navigations stay in mock mode without re-adding the query string).
 *
 * When enabled:
 *   - `apiFetch` in `src/api/client.ts` short-circuits to `mockResponseFor(path, init)`
 *     instead of hitting the network.
 *   - `useWebSocket` returns a canned event stream from `wsMock.ts`.
 *   - `useAuth` accepts any credentials and resolves to `MOCK_USER`.
 *
 * Adding a new mock endpoint:
 *   1. Add the fixture in `fixtures.ts`.
 *   2. Add a route handler in `router.ts`.
 *   That's it — every existing api/client.ts function gets mock support for free.
 */

const STORAGE_KEY = 'capelle.mockMode';

function readQueryFlag(): boolean {
  if (typeof window === 'undefined') return false;
  const params = new URLSearchParams(window.location.search);
  if (params.get('mock') === '1') {
    sessionStorage.setItem(STORAGE_KEY, '1');
    return true;
  }
  if (params.get('mock') === '0') {
    sessionStorage.removeItem(STORAGE_KEY);
    return false;
  }
  return sessionStorage.getItem(STORAGE_KEY) === '1';
}

let _cached: boolean | null = null;

export function isMockMode(): boolean {
  if (_cached !== null) return _cached;
  const envFlag = import.meta.env.VITE_MOCK_MODE === '1';
  const queryFlag = readQueryFlag();
  _cached = envFlag || queryFlag;
  return _cached;
}

/** Simulated network latency for mock responses. Keeps the UI honest. */
export function mockDelay(min = 80, max = 220): Promise<void> {
  const ms = min + Math.random() * (max - min);
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/** Build a Response-like object so `handleResponse<T>(res)` works unchanged. */
export function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

/** 204 No Content (used for DELETE-style endpoints). */
export function noContentResponse(): Response {
  return new Response(null, { status: 204 });
}
