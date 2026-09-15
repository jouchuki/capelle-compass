/**
 * URL → mock Response router.
 *
 * Called by `apiFetch` in `src/api/client.ts` when `isMockMode()` is true.
 * Every endpoint that the real backend exposes should have a mock branch here.
 *
 * Adding a new endpoint:
 *   - Add a case below matching the path (use the `match()` helper for path params).
 *   - Return `jsonResponse(body, status?)` or `noContentResponse()`.
 *   - For mock-only side effects (creating sessions, marking quota), mutate
 *     the in-memory fixtures directly.
 *
 * Errors are signaled by returning a non-2xx Response — `handleResponse` in
 * client.ts will throw an ApiError just like for the real backend.
 */

import { jsonResponse, noContentResponse, mockDelay } from './index';
import {
  MOCK_USER,
  MOCK_SESSIONS,
  MOCK_MESSAGES,
  MOCK_ANALYSIS_VEILIGHEID,
  MOCK_QUOTA,
  MOCK_ADMIN_OVERVIEW,
  MOCK_ADMIN_USERS,
  MOCK_ADMIN_QUESTIONS,
  MOCK_ADMIN_FEEDBACK,
  MOCK_ADMIN_TOKENS,
  MOCK_ADMIN_ENGAGEMENT,
  MOCK_CODEX_HEALTH,
  MOCK_CODEX_FAILURES_PER_HOUR,
  MOCK_CODEX_REFRESH_EVENTS,
} from './fixtures';
import type { ChatSession, ChatMessage } from '../types';

function readBody(init: RequestInit): Record<string, unknown> | null {
  if (!init.body) return null;
  if (typeof init.body !== 'string') return null;
  try {
    return JSON.parse(init.body) as Record<string, unknown>;
  } catch {
    return null;
  }
}

function match(path: string, pattern: RegExp): RegExpMatchArray | null {
  const head = path.split('?')[0] ?? path;
  return head.match(pattern);
}

function captured(m: RegExpMatchArray, idx: number): string {
  const value = m[idx];
  if (value === undefined) throw new Error(`mock router: capture group ${idx} missing`);
  return value;
}

let _quotaSnapshot = { ...MOCK_QUOTA };

export async function mockResponseFor(path: string, init: RequestInit): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  await mockDelay();

  // --- Auth ---
  if (path === '/api/auth/login' && method === 'POST') {
    return jsonResponse({
      access_token: 'mock-token',
      token_type: 'bearer',
      user_id: MOCK_USER.user_id,
      email: MOCK_USER.email,
    });
  }
  if (path === '/api/auth/register' && method === 'POST') {
    const body = readBody(init) ?? {};
    return jsonResponse({
      access_token: 'mock-token',
      token_type: 'bearer',
      user_id: MOCK_USER.user_id,
      email: (body.email as string) ?? MOCK_USER.email,
    });
  }
  if (path === '/api/auth/logout' && method === 'POST') {
    return noContentResponse();
  }
  if (path === '/api/auth/me' && method === 'GET') {
    return jsonResponse(MOCK_USER);
  }

  // --- Sessions ---
  if (path === '/api/chat/sessions' && method === 'GET') {
    return jsonResponse({ sessions: MOCK_SESSIONS, total: MOCK_SESSIONS.length });
  }
  if (path === '/api/chat/sessions' && method === 'POST') {
    const body = readBody(init) ?? {};
    const id = `s-${Date.now()}`;
    const now = new Date().toISOString();
    const session: ChatSession = {
      id,
      user_id: MOCK_USER.user_id,
      title: (body.title as string) ?? 'Nieuwe sessie',
      topic: null,
      created_at: now,
      updated_at: now,
    };
    MOCK_SESSIONS.unshift(session);
    MOCK_MESSAGES[id] = [];
    return jsonResponse(session);
  }
  const sessionGet = match(path, /^\/api\/chat\/sessions\/([^/]+)$/);
  if (sessionGet && method === 'GET') {
    const id = captured(sessionGet, 1);
    const session = MOCK_SESSIONS.find((s) => s.id === id);
    if (!session) return jsonResponse({ detail: 'not found' }, 404);
    return jsonResponse({ ...session, messages: MOCK_MESSAGES[id] ?? [] });
  }
  if (sessionGet && method === 'DELETE') {
    const id = captured(sessionGet, 1);
    const idx = MOCK_SESSIONS.findIndex((s) => s.id === id);
    if (idx >= 0) MOCK_SESSIONS.splice(idx, 1);
    delete MOCK_MESSAGES[id];
    return noContentResponse();
  }

  // --- Messages (send + receive — assistant response is the canned veiligheid analysis) ---
  const sendMsg = match(path, /^\/api\/chat\/sessions\/([^/]+)\/messages$/);
  if (sendMsg && method === 'POST') {
    const sessionId = captured(sendMsg, 1);
    const body = readBody(init) ?? {};
    const userMsg: ChatMessage = {
      id: `m-${Date.now()}-u`,
      session_id: sessionId,
      role: 'user',
      content: (body.content as string) ?? '',
      status: 'complete',
      metadata: null,
      created_at: new Date().toISOString(),
    };
    const assistantMsg: ChatMessage = {
      id: `m-${Date.now()}-a`,
      session_id: sessionId,
      role: 'assistant',
      content: '',
      status: 'thinking',
      metadata: null,
      created_at: new Date().toISOString(),
    };
    MOCK_MESSAGES[sessionId] = [...(MOCK_MESSAGES[sessionId] ?? []), userMsg, assistantMsg];
    _quotaSnapshot = { ..._quotaSnapshot, used: _quotaSnapshot.used + 1 };
    return jsonResponse({ user_message: userMsg, assistant_message: assistantMsg });
  }

  // Search — backend returns { messages: ChatMessage[], total }
  if (path.startsWith('/api/chat/search')) {
    const q = new URLSearchParams(path.split('?')[1] ?? '').get('q')?.toLowerCase() ?? '';
    const hits: ChatMessage[] = [];
    for (const session of MOCK_SESSIONS) {
      const msgs = MOCK_MESSAGES[session.id] ?? [];
      for (const m of msgs) {
        if (m.content.toLowerCase().includes(q) || session.title.toLowerCase().includes(q)) {
          hits.push(m);
        }
      }
    }
    return jsonResponse({ messages: hits, total: hits.length });
  }

  // --- Quota ---
  if (path === '/api/usage' && method === 'GET') {
    return jsonResponse(_quotaSnapshot);
  }
  if (path === '/api/events' && method === 'POST') {
    return noContentResponse();
  }

  // --- Fork ---
  const fork = match(path, /^\/api\/chat\/sessions\/([^/]+)\/fork-link$/);
  if (fork && method === 'POST') {
    return jsonResponse({
      token: 'mock-fork-token',
      url: `${window.location.origin}/f/mock-fork-token`,
    });
  }
  if (path === '/api/chat/fork/mock-fork-token' && method === 'GET') {
    const first = MOCK_SESSIONS[0];
    return jsonResponse({
      token: 'mock-fork-token',
      session: first,
      messages: MOCK_MESSAGES['s-001'] ?? [],
      analysis: MOCK_ANALYSIS_VEILIGHEID,
    });
  }
  const adopt = match(path, /^\/api\/chat\/fork\/([^/]+)\/adopt$/);
  if (adopt && method === 'POST') {
    const id = `s-adopted-${Date.now()}`;
    const first = MOCK_SESSIONS[0];
    const session: ChatSession = {
      id,
      user_id: MOCK_USER.user_id,
      title: first ? `Overgenomen: ${first.title}` : 'Overgenomen sessie',
      topic: first?.topic ?? null,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    };
    MOCK_SESSIONS.unshift(session);
    MOCK_MESSAGES[id] = MOCK_MESSAGES['s-001'] ?? [];
    return jsonResponse({ session_id: id });
  }

  // --- Feedback ---
  if (path === '/api/feedback' && method === 'POST') {
    return jsonResponse({ id: `f-${Date.now()}`, status: 'received' });
  }

  // --- Admin ---
  if (path === '/api/admin/check' && method === 'GET') {
    return jsonResponse({ is_admin: true });
  }
  if (path === '/api/admin/overview' && method === 'GET') {
    return jsonResponse(MOCK_ADMIN_OVERVIEW);
  }
  if (path === '/api/admin/codex/health' && method === 'GET') {
    return jsonResponse(MOCK_CODEX_HEALTH);
  }
  if (path === '/api/admin/stats/codex-failures' && method === 'GET') {
    return jsonResponse({ hourly: MOCK_CODEX_FAILURES_PER_HOUR });
  }
  if (path.startsWith('/api/admin/codex/events')) {
    const search = new URLSearchParams(path.split('?')[1] ?? '');
    const page = Math.max(1, Number(search.get('page') ?? '1') || 1);
    const pageSize = Math.max(1, Number(search.get('page_size') ?? '50') || 50);
    const start = (page - 1) * pageSize;
    const slice = MOCK_CODEX_REFRESH_EVENTS.slice(start, start + pageSize);
    return jsonResponse({
      items: slice,
      total: MOCK_CODEX_REFRESH_EVENTS.length,
      page,
      page_size: pageSize,
    });
  }
  if (path.startsWith('/api/admin/users')) {
    return jsonResponse({ items: MOCK_ADMIN_USERS, total: MOCK_ADMIN_USERS.length, page: 1, page_size: 50 });
  }
  if (path.startsWith('/api/admin/questions')) {
    return jsonResponse({ items: MOCK_ADMIN_QUESTIONS, total: MOCK_ADMIN_QUESTIONS.length, page: 1, page_size: 50 });
  }
  if (path.startsWith('/api/admin/feedback')) {
    return jsonResponse({ items: MOCK_ADMIN_FEEDBACK, total: MOCK_ADMIN_FEEDBACK.length, page: 1, page_size: 50 });
  }
  if (path.startsWith('/api/admin/tokens')) {
    return jsonResponse({ items: MOCK_ADMIN_TOKENS, total: MOCK_ADMIN_TOKENS.length, page: 1, page_size: 50 });
  }
  if (path.startsWith('/api/admin/engagement')) {
    return jsonResponse({ items: MOCK_ADMIN_ENGAGEMENT, total: MOCK_ADMIN_ENGAGEMENT.length, page: 1, page_size: 50 });
  }
  if (path.startsWith('/api/admin/timeseries')) {
    return jsonResponse({ series: MOCK_ADMIN_OVERVIEW.analyses_per_day });
  }
  const userLimit = match(path, /^\/api\/admin\/users\/([^/]+)\/daily-limit$/);
  if (userLimit && method === 'PUT') {
    const id = userLimit[1];
    const body = readBody(init) ?? {};
    const u = MOCK_ADMIN_USERS.find((u) => u.user_id === id);
    if (u) u.daily_limit_override = (body.daily_limit_override as number | null) ?? null;
    return jsonResponse({ ok: true });
  }

  // Default: 404 so call sites surface unimplemented mocks cleanly.
  return jsonResponse({ detail: `mock: no handler for ${method} ${path}` }, 404);
}
