import type { TokenResponse, ChatSession, ChatMessage, FindingGraph, Mode } from '../types';
import { isMockMode } from '../mocks';
import { mockResponseFor } from '../mocks/router';

const BASE = '';

// All API calls run with credentials:include so the browser attaches
// the HttpOnly auth cookie. We intentionally no longer store the JWT
// in localStorage — XSS on any frontend dependency would otherwise
// exfiltrate it.
const JSON_HEADERS: Record<string, string> = { 'Content-Type': 'application/json' };

/**
 * Typed error thrown by every API helper on a non-2xx response.
 *
 * Carries the original status, the server's parsed ``detail`` field
 * (which can be a string or an object — the quota endpoint returns a
 * dict of ``{error, message, used, limit, resets_at}``), and the raw
 * Response so call sites can read headers like ``X-Quota-Reset-At``.
 */
export class ApiError extends Error {
  readonly status: number;
  readonly detail: unknown;
  readonly response: Response;

  constructor(status: number, detail: unknown, response: Response) {
    const message =
      typeof detail === 'string'
        ? detail
        : detail && typeof detail === 'object' && 'message' in detail
          ? String((detail as { message: unknown }).message)
          : `HTTP ${status}`;
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
    this.response = response;
  }
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail: unknown;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail, res);
  }
  return res.json() as Promise<T>;
}

function apiFetch(path: string, init: RequestInit = {}): Promise<Response> {
  if (isMockMode()) {
    return mockResponseFor(path, init);
  }
  return fetch(`${BASE}${path}`, {
    credentials: 'include',
    ...init,
    headers: {
      ...JSON_HEADERS,
      ...(init.headers ?? {}),
    },
  });
}

// --- Auth ---

export async function register(email: string, password: string): Promise<TokenResponse> {
  const res = await apiFetch('/api/auth/register', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  return handleResponse<TokenResponse>(res);
}

export async function login(email: string, password: string): Promise<TokenResponse> {
  const res = await apiFetch('/api/auth/login', {
    method: 'POST',
    body: JSON.stringify({ email, password }),
  });
  return handleResponse<TokenResponse>(res);
}

export async function logout(): Promise<void> {
  await apiFetch('/api/auth/logout', { method: 'POST' });
}

export interface MeResponse {
  user_id: string;
  email: string;
}

export async function fetchMe(): Promise<MeResponse | null> {
  const res = await apiFetch('/api/auth/me');
  if (res.status === 401) return null;
  return handleResponse<MeResponse>(res);
}

/**
 * Single-sign-on advertisement from the backend.
 *
 * ``sso_enabled`` gates whether the login screen renders an external
 * identity-provider button; ``sso_provider`` names which one (currently
 * only ``"microsoft"`` via Entra OIDC, or ``null`` when SSO is off).
 */
export interface AuthConfig {
  sso_enabled: boolean;
  sso_provider: string | null;
}

/**
 * Probe whether federated sign-in is available on this deployment.
 *
 * Runs same-origin with ``credentials:'include'`` like every other
 * helper. In mock mode we short-circuit to ``sso_enabled:false`` so the
 * mock UI stays byte-identical to before (no SSO button, no network).
 */
export async function fetchAuthConfig(): Promise<AuthConfig> {
  if (isMockMode()) {
    return { sso_enabled: false, sso_provider: null };
  }
  const res = await apiFetch('/api/auth/config');
  return handleResponse<AuthConfig>(res);
}

// --- Sessions ---

export async function createSession(
  title?: string,
  mode?: Mode,
): Promise<ChatSession> {
  // Mode is optional on the wire — backend defaults to 'groeikern' if the
  // client omits it, so legacy callers keep working without change.
  const body: { title: string; mode?: Mode } = {
    title: title ?? 'Nieuwe sessie',
  };
  if (mode) body.mode = mode;
  const res = await apiFetch('/api/chat/sessions', {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return handleResponse<ChatSession>(res);
}

export async function listSessions(): Promise<{ sessions: ChatSession[]; total: number }> {
  const res = await apiFetch('/api/chat/sessions');
  return handleResponse<{ sessions: ChatSession[]; total: number }>(res);
}

export async function getSession(id: string): Promise<ChatSession & { messages: ChatMessage[] }> {
  const res = await apiFetch(`/api/chat/sessions/${id}`);
  return handleResponse<ChatSession & { messages: ChatMessage[] }>(res);
}

export async function deleteSession(id: string): Promise<void> {
  const res = await apiFetch(`/api/chat/sessions/${id}`, { method: 'DELETE' });
  if (!res.ok && res.status !== 204) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new ApiError(res.status, body.detail ?? body, res);
  }
}

// --- Finding-graph ---

/**
 * Fetch the session's finding-graph.
 *
 * Returns ``null`` on 404 — that covers "no graph yet for this session"
 * AND "feature flag off" (the backend deliberately 404s in both cases),
 * so legacy sessions render exactly as before without special-casing.
 */
export async function getSessionGraph(sessionId: string): Promise<FindingGraph | null> {
  const res = await apiFetch(`/api/chat/sessions/${sessionId}/graph`);
  if (res.status === 404) return null;
  return handleResponse<FindingGraph>(res);
}

// --- Messages ---

export async function sendMessage(
  sessionId: string,
  content: string,
  nodeIds?: string[],
): Promise<{
  user_message_id: string;
  assistant_message_id: string;
  status: string;
}> {
  // Focus-node fields are additive: omitted entirely for normal messages so
  // the wire payload (and any strict backend) stays byte-identical to before.
  //
  // Exactly one scoped node still goes out as the singular `node_id` — the
  // currently-deployed backend predates `node_ids`, so single-node deepening
  // must keep working before the next backend deploy. Two or more nodes use
  // the plural `node_ids` (which supersedes `node_id` server-side; the server
  // caps the list at MAX_FOCUS_NODES = 5).
  const body: { content: string; node_id?: string; node_ids?: string[] } = { content };
  if (nodeIds && nodeIds.length === 1 && nodeIds[0]) {
    body.node_id = nodeIds[0];
  } else if (nodeIds && nodeIds.length > 1) {
    body.node_ids = nodeIds;
  }
  const res = await apiFetch(`/api/chat/sessions/${sessionId}/messages`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
  return handleResponse<{ user_message_id: string; assistant_message_id: string; status: string }>(res);
}

// --- Search ---

export async function searchMessages(
  q: string,
): Promise<{ messages: ChatMessage[]; total: number }> {
  const res = await apiFetch(`/api/chat/search?q=${encodeURIComponent(q)}`);
  return handleResponse<{ messages: ChatMessage[]; total: number }>(res);
}

// --- Fork links ---

export interface ForkLinkResponse {
  url: string;
  token: string;
  expires_at: string;
}

export interface ForkViewResponse {
  session: ChatSession;
  messages: ChatMessage[];
}

export interface AdoptForkResponse {
  session_id: string;
  session: ChatSession;
}

export async function createForkLink(sessionId: string): Promise<ForkLinkResponse> {
  const res = await apiFetch(`/api/chat/sessions/${sessionId}/fork-link`, {
    method: 'POST',
  });
  return handleResponse<ForkLinkResponse>(res);
}

// No auth — the token IS the capability. Backend enforces expiry.
export async function getForkView(token: string): Promise<ForkViewResponse> {
  const res = await apiFetch(`/api/chat/fork/${encodeURIComponent(token)}`);
  return handleResponse<ForkViewResponse>(res);
}

export async function adoptFork(token: string): Promise<AdoptForkResponse> {
  const res = await apiFetch(`/api/chat/fork/${encodeURIComponent(token)}/adopt`, {
    method: 'POST',
  });
  return handleResponse<AdoptForkResponse>(res);
}

// --- Quota / Telemetry / Feedback ---

export interface QuotaSnapshot {
  used: number;
  limit: number;
  remaining: number;
  resets_at: string;
}

export async function fetchUsage(): Promise<QuotaSnapshot> {
  const res = await apiFetch('/api/usage/me');
  return handleResponse<QuotaSnapshot>(res);
}

export type TelemetryEventType =
  | 'session_created'
  | 'session_deleted'
  | 'message_sent'
  | 'analysis_completed'
  | 'analysis_failed'
  | 'quota_limit_hit'
  | 'share_link_minted'
  | 'share_link_opened'
  | 'share_link_adopted'
  | 'share_popup_shown'
  | 'share_popup_dismissed'
  | 'share_popup_clicked'
  | 'mailto_opened'
  | 'onboarding_shown'
  | 'example_query_clicked'
  | 'feedback_submitted'
  | 'report_printed'
  | 'codex_refresh_failed'
  | 'user_daily_limit_changed';

export async function recordEvent(
  eventType: TelemetryEventType,
  properties: Record<string, unknown> = {},
  sessionId?: string,
): Promise<void> {
  try {
    await apiFetch('/api/telemetry/event', {
      method: 'POST',
      body: JSON.stringify({
        event_type: eventType,
        session_id: sessionId ?? null,
        properties,
      }),
    });
  } catch {
    // Telemetry is fire-and-forget; a failed beacon must never
    // break the surrounding user interaction.
  }
}

export type FeedbackTopic = 'bug' | 'suggestion' | 'more_usage' | 'other';

export interface FeedbackSubmitResponse {
  id: string;
  support_email: string;
}

export async function submitFeedback(
  topic: FeedbackTopic,
  content: string,
): Promise<FeedbackSubmitResponse> {
  const res = await apiFetch('/api/feedback', {
    method: 'POST',
    body: JSON.stringify({ topic, content }),
  });
  return handleResponse<FeedbackSubmitResponse>(res);
}

// --- Admin (gated server-side; 403 for non-admins) ---

export interface AdminCheckResponse {
  is_admin: boolean;
  email: string;
}

/**
 * Outcome of the admin-allowlist probe.
 *
 * ``"unauth"`` — no valid session cookie (401). Consumer should
 *   redirect to /login so the user can sign in and try again.
 * ``"denied"`` — authenticated but not on the admin allowlist (403),
 *   or the backend explicitly returned ``is_admin: false``. Consumer
 *   should render a 404 / "not found" (no signal that admin exists).
 * ``{kind: "admin", email}`` — full access.
 */
export type AdminCheckResult =
  | { kind: 'admin'; email: string }
  | { kind: 'denied' }
  | { kind: 'unauth' };

export async function adminCheck(): Promise<AdminCheckResult> {
  const res = await apiFetch('/api/admin/check');
  if (res.status === 401) return { kind: 'unauth' };
  if (res.status === 403) return { kind: 'denied' };
  if (!res.ok) return { kind: 'denied' };
  try {
    const data = (await res.json()) as AdminCheckResponse;
    return data.is_admin ? { kind: 'admin', email: data.email } : { kind: 'denied' };
  } catch {
    return { kind: 'denied' };
  }
}

/**
 * Bucket label for a single point on an admin spark series.
 *
 * The day-bucketed series (``analyses_completed``, ``messages_sent``,
 * ``quota_limit_hit``, ``tokens_per_day``, ``share_link_*``) carry a
 * ``day: "YYYY-MM-DD"`` field. The hour-bucketed ``codex_refresh_per_hour``
 * series carries a ``hour: "YYYY-MM-DDTHH:00:00+00:00"`` field instead.
 * Both shapes share ``count``; both fields are optional so consumers can
 * narrow on whichever is present.
 */
export interface AdminSeriesPoint {
  day?: string;
  hour?: string;
  count?: number;
  /** Token flavour-split series only — present on ``tokens_by_type_per_day``. */
  input?: number;
  output?: number;
  cache?: number;
}

export interface AdminOverview {
  accounts: {
    total: number;
    last_24h: number;
    last_30d: number;
    /**
     * Global default daily-analysis limit (``CAPELLE_DAILY_ANALYSIS_LIMIT``)
     * the backend resolves when a user has no per-user override. Optional
     * because older backends predate the admin daily-limit override
     * feature — the frontend falls back to a hard-coded sentinel.
     */
    default_daily_limit?: number;
  };
  series_days: number;
  series: Record<string, AdminSeriesPoint[]>;
  funnels: { share: { minted: number; opened: number; adopted: number } };
  feedback_new: number;
}

export async function adminOverview(): Promise<AdminOverview> {
  const res = await apiFetch('/api/admin/stats/overview');
  return handleResponse<AdminOverview>(res);
}

export interface AdminFeedbackItem {
  id: string;
  user_id: string;
  email: string;
  topic: FeedbackTopic;
  content: string;
  status: 'new' | 'seen' | 'resolved';
  created_at: string;
}

export async function adminFeedback(
  offset = 0,
  limit = 50,
): Promise<AdminPagedResponse<AdminFeedbackItem>> {
  const res = await apiFetch(
    `/api/admin/feedback?offset=${offset}&limit=${limit}`,
  );
  return handleResponse<AdminPagedResponse<AdminFeedbackItem>>(res);
}

export interface AdminPagedResponse<T> {
  items: T[];
  count: number;
  offset: number;
  limit: number;
  has_more: boolean;
}

export interface AdminUserRow {
  user_id: string;
  email: string;
  created_at: string;
  is_admin: boolean;
  sessions: number;
  messages_sent: number;
  analyses_completed: number;
  last_active: string | null;
  /**
   * Per-user override for the daily analysis limit.
   *
   * ``null`` → user falls back to the global default
   * (``CAPELLE_DAILY_ANALYSIS_LIMIT``); ``0`` → explicitly unlimited
   * (no quota); any other ``N`` → user-specific cap of N analyses per
   * day. Contract is shared 1:1 with the backend.
   */
  daily_analysis_limit_override: number | null;
}

/**
 * Set (or clear) a user's per-user daily-analysis-limit override.
 *
 * Pass ``null`` to clear the override (user reverts to the global
 * default), ``0`` to grant unlimited analyses, or any other ``N`` to
 * pin the user to N analyses per day. Resolves on 204 No Content;
 * any non-2xx raises ``ApiError`` so the caller can surface the
 * server's reason in the UI.
 */
export async function setUserDailyLimit(
  userId: string,
  limit: number | null,
): Promise<void> {
  const res = await apiFetch(
    `/api/admin/users/${encodeURIComponent(userId)}/daily-limit`,
    {
      method: 'POST',
      body: JSON.stringify({ limit }),
    },
  );
  if (!res.ok && res.status !== 204) {
    let detail: unknown;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail, res);
  }
}

export interface AdminQuestionRow {
  message_id: string;
  session_id: string;
  session_title: string;
  session_topic: string | null;
  user_id: string;
  user_email: string;
  content: string;
  created_at: string;
}

export async function adminQuestions(
  offset = 0,
  limit = 50,
): Promise<AdminPagedResponse<AdminQuestionRow>> {
  const res = await apiFetch(
    `/api/admin/questions?offset=${offset}&limit=${limit}`,
  );
  return handleResponse<AdminPagedResponse<AdminQuestionRow>>(res);
}

export async function adminUsers(
  offset = 0,
  limit = 50,
): Promise<AdminPagedResponse<AdminUserRow>> {
  const res = await apiFetch(
    `/api/admin/users?offset=${offset}&limit=${limit}`,
  );
  return handleResponse<AdminPagedResponse<AdminUserRow>>(res);
}

export interface AdminEngagementRow {
  user_id: string;
  email: string;
  sessions_created: number;
  messages_sent: number;
  analyses_completed: number;
  analyses_failed: number;
  share_links_minted: number;
  share_links_opened: number;
  feedback_submitted: number;
  quota_limit_hit: number;
  first_seen: string;
  last_active: string | null;
}

export async function adminEngagement(
  offset = 0,
  limit = 50,
  days = 30,
): Promise<AdminPagedResponse<AdminEngagementRow> & { days: number }> {
  const res = await apiFetch(
    `/api/admin/stats/engagement?days=${days}&offset=${offset}&limit=${limit}`,
  );
  return handleResponse<
    AdminPagedResponse<AdminEngagementRow> & { days: number }
  >(res);
}

export interface AdminTokenRow {
  user_id: string;
  email: string;
  analyses: number;
  input_tokens: number;
  output_tokens: number;
  cache_creation_input_tokens: number;
  cache_read_input_tokens: number;
  total_tokens: number;
}

export async function adminTokenSpend(
  offset = 0,
  limit = 50,
  days = 30,
): Promise<
  AdminPagedResponse<AdminTokenRow> & {
    days: number;
    grand_total: number;
  }
> {
  const res = await apiFetch(
    `/api/admin/stats/tokens?days=${days}&offset=${offset}&limit=${limit}`,
  );
  return handleResponse<
    AdminPagedResponse<AdminTokenRow> & {
      days: number;
      grand_total: number;
    }
  >(res);
}

export async function adminTimeseries(
  eventType: TelemetryEventType,
  days = 30,
): Promise<{ event_type: string; days: number; series: { day: string; count: number }[] }> {
  const res = await apiFetch(
    `/api/admin/stats/timeseries?event_type=${encodeURIComponent(eventType)}&days=${days}`,
  );
  return handleResponse<{
    event_type: string;
    days: number;
    series: { day: string; count: number }[];
  }>(res);
}

// --- Elicitation ---

/**
 * Submit the user's answer to a mid-run agent elicitation prompt.
 *
 * POSTs to /api/analyses/answer with the question_id and the chosen or
 * typed answer. Uses the same credentials:include cookie pattern as all
 * other API helpers.
 */
export async function postElicitationAnswer(
  questionId: string,
  answer: string,
): Promise<void> {
  const res = await apiFetch('/api/analyses/answer', {
    method: 'POST',
    body: JSON.stringify({ question_id: questionId, answer }),
  });
  if (!res.ok && res.status !== 204) {
    let detail: unknown;
    try {
      const body = await res.json();
      detail = body.detail ?? body;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail, res);
  }
}

// --- WebSocket ---

// The browser attaches the auth cookie to the WS upgrade request, so
// we no longer need to pass a token in the query string. Non-browser
// clients can still authenticate via ?token=<jwt>.
export function createWebSocket(): WebSocket {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const url = `${protocol}//${window.location.host}/api/ws`;
  return new WebSocket(url);
}
