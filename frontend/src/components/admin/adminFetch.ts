/**
 * Admin-surface data layer.
 *
 * Translates between Agent B's flat row shapes (used by the tab
 * components) and the real backend's nested/offset-based shapes
 * exposed by ``src/api/client.ts``. In mock mode we still hit the
 * mock router directly (which speaks the flat shape). In real mode
 * we route through ``api/client.ts`` admin helpers and adapt the
 * response on the way back.
 *
 * Codex endpoints (health / failures / events) don't have a real
 * backend implementation yet — when ``isMockMode()`` is false they
 * gracefully fall back to empty / disabled payloads so the dashboard
 * still renders instead of crashing on a 404 HTML body.
 */

import { isMockMode } from '../../mocks';
import { mockResponseFor } from '../../mocks/router';
import {
  ApiError,
  adminEngagement,
  adminFeedback,
  adminOverview,
  adminQuestions,
  adminTokenSpend,
  adminUsers,
  setUserDailyLimit,
  type AdminEngagementRow as RealEngagementRow,
  type AdminFeedbackItem as RealFeedbackItem,
  type AdminOverview as RealAdminOverview,
  type AdminQuestionRow as RealQuestionRow,
  type AdminTokenRow as RealTokenRow,
  type AdminUserRow as RealUserRow,
} from '../../api/client';

/* ───────── Shapes (match mocks/fixtures.ts — flat shape Agent B's UI uses) ───────── */

export interface AdminOverviewData {
  total_accounts: number;
  new_accounts_24h: number;
  new_accounts_30d: number;
  new_feedback: number;
  tokens_last_30d: number;
  analyses_per_day: Array<{ date: string; value: number }>;
  questions_per_day: Array<{ date: string; value: number }>;
  tokens_per_day: Array<{ date: string; input: number; output: number; cache: number }>;
  quota_limit_hits_per_day: Array<{ date: string; value: number }>;
  share_funnel: { minted: number; opened: number; adopted: number };
}

export interface AdminUserRow {
  user_id: string;
  email: string;
  is_admin: boolean;
  created_at: string;
  last_active: string | null;
  sessions_count: number;
  messages_count: number;
  analyses_completed: number;
  /**
   * ``null`` → user falls back to the global default limit.
   * ``0``    → explicitly unlimited.
   * ``N``    → per-user cap of N analyses per day.
   */
  daily_limit_override: number | null;
}

export interface AdminQuestionRow {
  id: string;
  created_at: string;
  user_email: string;
  topic: string;
  question: string;
}

export type AdminFeedbackStatus = 'new' | 'reviewed' | 'resolved';
export type AdminFeedbackTopic = 'bug' | 'suggestion' | 'more_usage' | 'other';

export interface AdminFeedbackRow {
  id: string;
  created_at: string;
  user_email: string;
  topic: AdminFeedbackTopic;
  content: string;
  status: AdminFeedbackStatus;
}

export interface AdminTokenRow {
  user_id: string;
  email: string;
  analyses: number;
  input_tokens: number;
  output_tokens: number;
  cache_tokens: number;
  total_tokens: number;
}

export interface AdminEngagementRow {
  user_id: string;
  email: string;
  sessions_created: number;
  messages_sent: number;
  analyses_completed: number;
  analyses_failed: number;
  share_links_created: number;
  feedback_submitted: number;
  quota_limit_hits: number;
  first_seen: string;
  last_active: string | null;
}

export interface AdminPagedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

/* ───────── Codex auth health (backend endpoint coming) ───────── */

export type CodexHealthStatus =
  | 'in_sync'
  | 'rotated'
  | 'near_expiry'
  | 'failed'
  | 'auth_json_missing';

export interface CodexHealthRow {
  hostname: string;
  status: CodexHealthStatus;
  last_success: string | null;
  hours_to_expiry: number | null;
  access_tail: string | null;
}

export interface CodexFailuresPerHourPoint {
  hour: string;
  value: number;
}

export interface CodexFailuresResponse {
  hourly: CodexFailuresPerHourPoint[];
}

export interface CodexRefreshEvent {
  id: string;
  created_at: string;
  hostname: string;
  status: CodexHealthStatus;
  access_tail: string | null;
  message: string;
}

/* ───────── Mock-only helpers (used when isMockMode() is true) ───────── */

async function mockGet<T>(path: string): Promise<T> {
  const res = await mockResponseFor(path, { method: 'GET' });
  if (!res.ok) {
    let detail: unknown;
    try {
      const body = await res.json();
      detail = (body as { detail?: unknown }).detail ?? body;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail, res);
  }
  return res.json() as Promise<T>;
}

function pageQueryString(page: number, pageSize: number): string {
  return `?page=${page}&page_size=${pageSize}`;
}

/* ───────── Adapters (real backend shape → flat shape Agent B's UI consumes) ───────── */

function adaptOverview(r: RealAdminOverview): AdminOverviewData {
  const series = r.series ?? {};

  // Real backend (handlers/admin.py::overview) keys: ``analyses_completed``,
  // ``messages_sent``, ``quota_limit_hit``, ``tokens_per_day``,
  // ``codex_refresh_per_hour``, ``share_link_{minted,opened,adopted}``.
  // We keep ``analyses_per_day`` / ``questions_per_day`` etc. as fallback
  // keys so a future mock-only response (or older backend) still renders.
  const tokenSeries = series['tokens_per_day'] ?? series['tokens_total_per_day'] ?? [];
  const tokensLast30d = tokenSeries.reduce((sum, p) => sum + (p.count ?? 0), 0);

  const toDayValueSeries = (key: string): Array<{ date: string; value: number }> =>
    (series[key] ?? [])
      .filter((p): p is { day: string; count: number } => typeof p.day === 'string')
      .map((p) => ({ date: p.day, value: p.count ?? 0 }));

  // Prefer the per-flavor split series (``tokens_by_type_per_day``) when
  // the backend emits it — each point carries explicit input/output/cache
  // counts. Fall back to the flat ``tokens_per_day`` and stuff the total
  // into ``input`` so older backends still render something instead of
  // an empty chart.
  const tokensByType = series['tokens_by_type_per_day'] ?? [];
  const tokensPerDay = tokensByType.length > 0
    ? tokensByType
        .filter((p): p is { day: string; input: number; output: number; cache: number } =>
          typeof p.day === 'string',
        )
        .map((p) => ({
          date: p.day,
          input: p.input ?? 0,
          output: p.output ?? 0,
          cache: p.cache ?? 0,
        }))
    : tokenSeries
        .filter((p): p is { day: string; count: number } => typeof p.day === 'string')
        .map((p) => ({ date: p.day, input: p.count ?? 0, output: 0, cache: 0 }));

  const analysesPerDay = toDayValueSeries('analyses_completed');
  const questionsPerDay = toDayValueSeries('messages_sent');
  const quotaHitsPerDay = toDayValueSeries('quota_limit_hit');

  return {
    total_accounts: r.accounts?.total ?? 0,
    new_accounts_24h: r.accounts?.last_24h ?? 0,
    new_accounts_30d: r.accounts?.last_30d ?? 0,
    new_feedback: r.feedback_new ?? 0,
    tokens_last_30d: tokensLast30d,
    analyses_per_day:
      analysesPerDay.length > 0 ? analysesPerDay : toDayValueSeries('analyses_per_day'),
    questions_per_day:
      questionsPerDay.length > 0 ? questionsPerDay : toDayValueSeries('questions_per_day'),
    tokens_per_day: tokensPerDay,
    quota_limit_hits_per_day:
      quotaHitsPerDay.length > 0 ? quotaHitsPerDay : toDayValueSeries('quota_limit_hits_per_day'),
    share_funnel: r.funnels?.share ?? { minted: 0, opened: 0, adopted: 0 },
  };
}

/**
 * Pull the hourly Codex refresh-failure series out of the same overview
 * payload. The backend already buckets ``codex_refresh_failed`` events
 * into ``series.codex_refresh_per_hour`` — we just adapt the field names.
 */
function adaptCodexFailures(r: RealAdminOverview): CodexFailuresResponse {
  const points = r.series?.['codex_refresh_per_hour'] ?? [];
  return {
    hourly: points
      .filter((p): p is { hour: string; count: number } => typeof p.hour === 'string')
      .map((p) => ({ hour: p.hour, value: p.count ?? 0 })),
  };
}

function adaptUser(u: RealUserRow): AdminUserRow {
  return {
    user_id: u.user_id,
    email: u.email,
    is_admin: u.is_admin,
    created_at: u.created_at,
    last_active: u.last_active,
    sessions_count: u.sessions ?? 0,
    messages_count: u.messages_sent ?? 0,
    analyses_completed: u.analyses_completed ?? 0,
    daily_limit_override: u.daily_analysis_limit_override,
  };
}

function adaptQuestion(q: RealQuestionRow): AdminQuestionRow {
  return {
    id: q.message_id,
    created_at: q.created_at,
    user_email: q.user_email,
    topic: q.session_topic ?? '—',
    question: q.content,
  };
}

function adaptFeedback(f: RealFeedbackItem): AdminFeedbackRow {
  // Backend status enum uses 'seen' where the UI expects 'reviewed'.
  const status: AdminFeedbackStatus =
    f.status === 'seen' ? 'reviewed' : (f.status as AdminFeedbackStatus);
  return {
    id: f.id,
    created_at: f.created_at,
    user_email: f.email,
    topic: f.topic,
    content: f.content,
    status,
  };
}

function adaptToken(t: RealTokenRow): AdminTokenRow {
  return {
    user_id: t.user_id,
    email: t.email,
    analyses: t.analyses,
    input_tokens: t.input_tokens,
    output_tokens: t.output_tokens,
    cache_tokens: (t.cache_creation_input_tokens ?? 0) + (t.cache_read_input_tokens ?? 0),
    total_tokens: t.total_tokens,
  };
}

function adaptEngagement(e: RealEngagementRow): AdminEngagementRow {
  return {
    user_id: e.user_id,
    email: e.email,
    sessions_created: e.sessions_created,
    messages_sent: e.messages_sent,
    analyses_completed: e.analyses_completed,
    analyses_failed: e.analyses_failed,
    share_links_created: e.share_links_minted ?? 0,
    feedback_submitted: e.feedback_submitted,
    quota_limit_hits: e.quota_limit_hit ?? 0,
    first_seen: e.first_seen,
    last_active: e.last_active,
  };
}

function pageToOffset(page: number, pageSize: number): { offset: number; limit: number } {
  return { offset: Math.max(0, (page - 1) * pageSize), limit: pageSize };
}

function adaptPaged<TIn, TOut>(
  resp: { items: TIn[]; count: number; offset: number; limit: number },
  adapter: (item: TIn) => TOut,
): AdminPagedResponse<TOut> {
  const pageSize = resp.limit || 50;
  return {
    items: resp.items.map(adapter),
    total: resp.count,
    page: Math.floor(resp.offset / pageSize) + 1,
    page_size: pageSize,
  };
}

/* ───────── Public fetchers — mock vs real switch lives here ───────── */

export async function fetchAdminOverview(): Promise<AdminOverviewData> {
  if (isMockMode()) {
    return mockGet<AdminOverviewData>('/api/admin/overview');
  }
  return adaptOverview(await adminOverview());
}

export async function fetchAdminUsers(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<AdminUserRow>> {
  if (isMockMode()) {
    return mockGet<AdminPagedResponse<AdminUserRow>>(
      `/api/admin/users${pageQueryString(page, pageSize)}`,
    );
  }
  const { offset, limit } = pageToOffset(page, pageSize);
  const r = await adminUsers(offset, limit);
  return adaptPaged(r, adaptUser);
}

export async function fetchAdminQuestions(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<AdminQuestionRow>> {
  if (isMockMode()) {
    return mockGet<AdminPagedResponse<AdminQuestionRow>>(
      `/api/admin/questions${pageQueryString(page, pageSize)}`,
    );
  }
  const { offset, limit } = pageToOffset(page, pageSize);
  const r = await adminQuestions(offset, limit);
  return adaptPaged(r, adaptQuestion);
}

export async function fetchAdminFeedback(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<AdminFeedbackRow>> {
  if (isMockMode()) {
    return mockGet<AdminPagedResponse<AdminFeedbackRow>>(
      `/api/admin/feedback${pageQueryString(page, pageSize)}`,
    );
  }
  const { offset, limit } = pageToOffset(page, pageSize);
  const r = await adminFeedback(offset, limit);
  return adaptPaged(r, adaptFeedback);
}

export async function fetchAdminTokens(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<AdminTokenRow>> {
  if (isMockMode()) {
    return mockGet<AdminPagedResponse<AdminTokenRow>>(
      `/api/admin/tokens${pageQueryString(page, pageSize)}`,
    );
  }
  const { offset, limit } = pageToOffset(page, pageSize);
  const r = await adminTokenSpend(offset, limit);
  return adaptPaged(r, adaptToken);
}

export async function fetchAdminEngagement(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<AdminEngagementRow>> {
  if (isMockMode()) {
    return mockGet<AdminPagedResponse<AdminEngagementRow>>(
      `/api/admin/engagement${pageQueryString(page, pageSize)}`,
    );
  }
  const { offset, limit } = pageToOffset(page, pageSize);
  const r = await adminEngagement(offset, limit);
  return adaptPaged(r, adaptEngagement);
}

/* Codex endpoints — health + events still have no real backend route; in
 * real mode they return an empty payload rather than 404-ing through to
 * the SPA index. Failures-per-hour is now wired through ``adminOverview``,
 * which buckets the ``codex_refresh_failed`` event into
 * ``series.codex_refresh_per_hour``. */

const EMPTY_CODEX_HEALTH: CodexHealthRow[] = [];
const EMPTY_CODEX_EVENTS: AdminPagedResponse<CodexRefreshEvent> = {
  items: [],
  total: 0,
  page: 1,
  page_size: 50,
};

export async function fetchCodexHealth(): Promise<CodexHealthRow[]> {
  if (!isMockMode()) return EMPTY_CODEX_HEALTH;
  return mockGet<CodexHealthRow[]>('/api/admin/codex/health');
}

export async function fetchCodexFailures(): Promise<CodexFailuresResponse> {
  if (isMockMode()) {
    return mockGet<CodexFailuresResponse>('/api/admin/stats/codex-failures');
  }
  return adaptCodexFailures(await adminOverview());
}

export async function fetchCodexEvents(
  page = 1,
  pageSize = 50,
): Promise<AdminPagedResponse<CodexRefreshEvent>> {
  if (!isMockMode()) return EMPTY_CODEX_EVENTS;
  return mockGet<AdminPagedResponse<CodexRefreshEvent>>(
    `/api/admin/codex/events${pageQueryString(page, pageSize)}`,
  );
}

export async function updateUserDailyLimit(userId: string, limit: number | null): Promise<void> {
  if (isMockMode()) {
    const res = await mockResponseFor(
      `/api/admin/users/${encodeURIComponent(userId)}/daily-limit`,
      {
        method: 'PUT',
        body: JSON.stringify({ daily_limit_override: limit }),
      },
    );
    if (!res.ok && res.status !== 204) {
      let detail: unknown;
      try {
        const body = await res.json();
        detail = (body as { detail?: unknown }).detail ?? body;
      } catch {
        detail = res.statusText;
      }
      throw new ApiError(res.status, detail, res);
    }
    return;
  }
  // Real backend: api/client.ts handles the POST + {limit} body shape.
  await setUserDailyLimit(userId, limit);
}
