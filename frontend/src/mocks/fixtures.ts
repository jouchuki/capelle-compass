/**
 * Hard-coded mock data for the DataKompas UI.
 *
 * Shapes mirror `src/types/index.ts`. Used by `mocks/router.ts` to answer
 * api/client.ts calls without a backend. Edit freely — this is mock world.
 */

import type {
  ChatSession,
  ChatMessage,
  AnalysisResult,
  AnalysisSection,
  ToolOutput,
} from '../types';

export const MOCK_USER = {
  user_id: 'u-mock-001',
  email: 'beleid@capelle.nl',
};

/* ─── Sessions (~10 mock conversations grouped by topic) ─── */

const now = new Date('2026-05-12T11:30:00Z');
const iso = (offsetMin: number): string =>
  new Date(now.getTime() - offsetMin * 60_000).toISOString();

export const MOCK_SESSIONS: ChatSession[] = [
  { id: 's-001', user_id: MOCK_USER.user_id, title: 'Veiligheid en leefbaarheid Schollevaar 2023', topic: 'Veiligheid', created_at: iso(60), updated_at: iso(15) },
  { id: 's-002', user_id: MOCK_USER.user_id, title: 'Jeugdzorg uitgaven 2019–2025', topic: 'Sociaal domein', created_at: iso(1440), updated_at: iso(1380) },
  { id: 's-003', user_id: MOCK_USER.user_id, title: 'Woningbouwplan Fascinatio', topic: 'Wonen', created_at: iso(2880), updated_at: iso(2820) },
  { id: 's-004', user_id: MOCK_USER.user_id, title: 'Vergelijking Capelle met Nieuwegein op sociaal-economische positie', topic: 'Vergelijking', created_at: iso(4320), updated_at: iso(4260) },
  { id: 's-005', user_id: MOCK_USER.user_id, title: 'BuitenBeter meldingen Schenkel afgelopen kwartaal', topic: 'Openbare ruimte', created_at: iso(5760), updated_at: iso(5700) },
  { id: 's-006', user_id: MOCK_USER.user_id, title: 'Energietransitie woningvoorraad Capelle', topic: 'Klimaat', created_at: iso(7200), updated_at: iso(7140) },
  { id: 's-007', user_id: MOCK_USER.user_id, title: 'Schoolverlaten in vergelijking met groeikernen', topic: 'Onderwijs', created_at: iso(10080), updated_at: iso(10020) },
  { id: 's-008', user_id: MOCK_USER.user_id, title: 'Ondersteuning alleenstaande moeders', topic: 'Sociaal domein', created_at: iso(11520), updated_at: iso(11460) },
];

/* ─── A reference analysis (matches AnalysisResult schema) ─── */

const veiligheidToolOutput: ToolOutput = {
  tool: 'cbs',
  query: 'misdrijven Schollevaar 2021-2023',
  result_type: 'time_series',
  data: [
    { jaar: 2021, misdrijven: 412, woninginbraken: 38 },
    { jaar: 2022, misdrijven: 387, woninginbraken: 31 },
    { jaar: 2023, misdrijven: 395, woninginbraken: 29 },
  ],
  columns: [
    { key: 'jaar', label: 'Jaar', type: 'year' },
    { key: 'misdrijven', label: 'Totaal misdrijven', type: 'number' },
    { key: 'woninginbraken', label: 'Woninginbraken', type: 'number' },
  ],
  chart_hints: [
    { type: 'line', x: 'jaar', y: ['misdrijven', 'woninginbraken'], title: 'Geregistreerde misdrijven Schollevaar' },
  ],
};

const buitenBeterOutput: ToolOutput = {
  tool: 'buitenbeter',
  query: 'meldingen Schollevaar 2023',
  result_type: 'table',
  data: [
    { categorie: 'Bestrating', meldingen: 87 },
    { categorie: 'Verlichting', meldingen: 64 },
    { categorie: 'Groen', meldingen: 52 },
    { categorie: 'Vuilnis/afval', meldingen: 41 },
    { categorie: 'Overlast', meldingen: 28 },
  ],
  columns: [
    { key: 'categorie', label: 'Categorie', type: 'string' },
    { key: 'meldingen', label: 'Meldingen', type: 'number' },
  ],
};

const sections: AnalysisSection[] = [
  {
    heading: 'Huidige stand',
    source: 'cbs',
    content:
      'In 2023 telde Schollevaar 395 geregistreerde misdrijven (CBS), een lichte stijging ten opzichte van 2022 maar lager dan 2021. Woninginbraken namen verder af van 38 (2021) naar 29 (2023).',
    tool_output: veiligheidToolOutput,
  },
  {
    heading: 'Operationele signalen',
    source: 'buitenbeter',
    content:
      'BuitenBeter-meldingen in Schollevaar concentreren zich rond bestrating en verlichting, vooral langs de wandelroute tussen het treinstation en het park. Dit komt overeen met de subjectieve veiligheidssignalen uit de bewonersenquête.',
    tool_output: buitenBeterOutput,
  },
  {
    heading: 'Beleidscontext',
    source: 'beleid',
    content:
      'Het meerjarenprogramma Veiligheid 2022–2026 zet specifiek in op verlichting en sociale samenhang in Schollevaar. Het programmaplan noemt het station en het park als prioritaire locaties.',
  },
  {
    heading: 'Budgettaire context',
    source: 'budget',
    content:
      'Uitgaven aan openbare ruimte en veiligheid in Capelle stijgen gestaag (Iv3 taakveld 1.2 en 5.7), met een toename van €1,2 mln op het taakveld leefbaarheid sinds 2021.',
  },
  {
    heading: 'Resident perceptie',
    source: 'bewonersenquete',
    content:
      'Bewonersenquête 2023: subjectieve onveiligheid op de schaal van 10 stijgt in Schollevaar met 1,2 punt, ondanks dalende objectieve criminaliteit — een klassieke divergentie tussen registratie en beleving.',
  },
];

export const MOCK_ANALYSIS_VEILIGHEID: AnalysisResult = {
  id: 'a-001',
  timestamp: iso(15),
  query: 'Hoe ontwikkelt de leefbaarheid in Schollevaar zich?',
  summary:
    'Capelle Schollevaar laat in 2021–2023 een **mixed signal** zien: objectieve criminaliteit daalt licht, maar de subjectieve onveiligheid stijgt. Operationele meldingen wijzen op infrastructuur rond station en park; beleid en budget reageren daar al deels op.',
  sections,
  data_gaps: [
    'Niet alle Veiligheidsmonitor-indicatoren zijn op wijkniveau beschikbaar (Capelle ~67k inwoners, net onder de drempel).',
    '3DBAG/EP-Online-koppeling voor verlichtingsdichtheid per pand ontbreekt nog.',
  ],
  follow_up: [
    'Wil je deze analyse uitsplitsen per straat-buurt binnen Schollevaar?',
    'Vergelijken met Zoetermeer-Centrum (vergelijkbare groeikern-buurt)?',
    'Trendanalyse 2010–2023 voor langere context?',
  ],
};

/* ─── Messages for the active demo session ─── */

export const MOCK_MESSAGES: Record<string, ChatMessage[]> = {
  's-001': [
    {
      id: 'm-001',
      session_id: 's-001',
      role: 'user',
      content: 'Hoe ontwikkelt de leefbaarheid in Schollevaar zich?',
      status: 'complete',
      metadata: null,
      created_at: iso(45),
    },
    {
      id: 'm-002',
      session_id: 's-001',
      role: 'assistant',
      content:
        'Ik heb een analyse opgesteld op basis van CBS-criminaliteit, BuitenBeter-meldingen, beleidsdocumenten, Iv3-budget en de bewonersenquête. De volledige rapportage kun je rechts inzien.',
      status: 'complete',
      metadata: { analysis: MOCK_ANALYSIS_VEILIGHEID },
      created_at: iso(15),
    },
  ],
  's-002': [],
  's-003': [],
  's-004': [],
  's-005': [],
  's-006': [],
  's-007': [],
  's-008': [],
};

/* ─── Quota ─── */

export const MOCK_QUOTA = {
  used: 2,
  limit: 5,
  resets_at: new Date(now.getTime() + 12 * 3600_000).toISOString(),
};

/* ─── Admin fixtures (used by /admin) ─── */

export const MOCK_ADMIN_OVERVIEW = {
  total_accounts: 78,
  new_accounts_24h: 3,
  new_accounts_30d: 19,
  new_feedback: 4,
  tokens_last_30d: 18_420_000,
  analyses_per_day: [
    { date: '2026-05-05', value: 22 },
    { date: '2026-05-06', value: 31 },
    { date: '2026-05-07', value: 28 },
    { date: '2026-05-08', value: 35 },
    { date: '2026-05-09', value: 41 },
    { date: '2026-05-10', value: 26 },
    { date: '2026-05-11', value: 33 },
    { date: '2026-05-12', value: 18 },
  ],
  questions_per_day: [
    { date: '2026-05-05', value: 47 },
    { date: '2026-05-06', value: 62 },
    { date: '2026-05-07', value: 51 },
    { date: '2026-05-08', value: 78 },
    { date: '2026-05-09', value: 85 },
    { date: '2026-05-10', value: 49 },
    { date: '2026-05-11', value: 66 },
    { date: '2026-05-12', value: 38 },
  ],
  tokens_per_day: [
    { date: '2026-05-05', input: 412_000, output: 198_000, cache: 1_120_000 },
    { date: '2026-05-06', input: 521_000, output: 234_000, cache: 1_360_000 },
    { date: '2026-05-07', input: 488_000, output: 211_000, cache: 1_290_000 },
    { date: '2026-05-08', input: 612_000, output: 267_000, cache: 1_540_000 },
    { date: '2026-05-09', input: 705_000, output: 298_000, cache: 1_820_000 },
    { date: '2026-05-10', input: 419_000, output: 184_000, cache: 1_180_000 },
    { date: '2026-05-11', input: 568_000, output: 245_000, cache: 1_440_000 },
    { date: '2026-05-12', input: 287_000, output: 124_000, cache: 720_000 },
  ],
  quota_limit_hits_per_day: [
    { date: '2026-05-05', value: 1 },
    { date: '2026-05-06', value: 0 },
    { date: '2026-05-07', value: 2 },
    { date: '2026-05-08', value: 4 },
    { date: '2026-05-09', value: 3 },
    { date: '2026-05-10', value: 1 },
    { date: '2026-05-11', value: 2 },
    { date: '2026-05-12', value: 1 },
  ],
  share_funnel: { minted: 18, opened: 14, adopted: 6 },
};

export const MOCK_ADMIN_USERS = Array.from({ length: 24 }, (_, i) => ({
  user_id: `u-${String(i + 1).padStart(3, '0')}`,
  email: `gebruiker${i + 1}@capelle.nl`,
  is_admin: i < 2,
  created_at: iso(60 * 24 * (i + 3)),
  last_active: iso(60 * (i + 1)),
  sessions_count: 12 - (i % 11),
  messages_count: 87 - i * 2,
  analyses_completed: 23 - (i % 18),
  daily_limit_override: i % 5 === 0 ? null : i % 5 === 1 ? 0 : 10,
}));

export const MOCK_ADMIN_QUESTIONS = Array.from({ length: 30 }, (_, i) => ({
  id: `q-${String(i + 1).padStart(3, '0')}`,
  created_at: iso(i * 37),
  user_email: `gebruiker${(i % 24) + 1}@capelle.nl`,
  topic: ['Veiligheid', 'Wonen', 'Sociaal domein', 'Klimaat', 'Onderwijs', 'Vergelijking'][i % 6],
  question: [
    'Hoe ontwikkelt de leefbaarheid in Schollevaar zich?',
    'Welke ondersteuning is er voor alleenstaande moeders in Capelle?',
    'Hoe scoort Capelle op veiligheid t.o.v. vergelijkbare groeikernen?',
    'Welke BuitenBeter-meldingen komen het meest voor in Fascinatio?',
    'Hoe ontwikkelt de jeugdzorg-uitgaven zich 2019–2025?',
    'Wat is de impact van de energietransitie op de woningvoorraad?',
  ][i % 6],
}));

export const MOCK_ADMIN_FEEDBACK = Array.from({ length: 18 }, (_, i) => ({
  id: `f-${String(i + 1).padStart(3, '0')}`,
  created_at: iso(i * 200),
  user_email: `gebruiker${(i % 24) + 1}@capelle.nl`,
  topic: (['bug', 'suggestion', 'more_usage', 'other'] as const)[i % 4],
  content: [
    'Graag meer detail op buurtniveau voor BuitenBeter-meldingen.',
    'PDF-export werkt niet als de analyse meer dan 3 tabellen bevat.',
    'Kunnen we Capelle vergelijken met andere groeikernen in één rapport?',
    'Het dagelijkse limiet van 5 analyses is in piekperiode te laag.',
  ][i % 4],
  status: ['new', 'reviewed', 'resolved'][i % 3],
}));

export const MOCK_ADMIN_TOKENS = MOCK_ADMIN_USERS.slice(0, 15).map((u, i) => ({
  user_id: u.user_id,
  email: u.email,
  analyses: u.analyses_completed,
  input_tokens: 120_000 + i * 9_300,
  output_tokens: 48_000 + i * 4_200,
  cache_tokens: 320_000 + i * 18_500,
  total_tokens: 488_000 + i * 32_000,
}));

/* ─── Codex auth health (host-level) ─── */

/**
 * Rolling 24-hour series of Codex-token refresh failures, hourly buckets.
 * Mostly zeros with two small spikes so the chart has something to render.
 *
 * The labels match the ``"HH:MM"`` shape the chart's tick formatter accepts.
 */
function buildCodexFailuresPerHour(): Array<{ hour: string; value: number }> {
  const series: Array<{ hour: string; value: number }> = [];
  // 24 buckets ending at the current "now" hour.
  for (let i = 23; i >= 0; i--) {
    const bucket = new Date(now.getTime() - i * 3600_000);
    const hour = `${String(bucket.getHours()).padStart(2, '0')}:00`;
    series.push({ hour, value: 0 });
  }
  // Inject two interesting spikes.
  const eight = series[8];
  if (eight) eight.value = 2;
  const seventeen = series[17];
  if (seventeen) seventeen.value = 3;
  const twenty = series[20];
  if (twenty) twenty.value = 1;
  return series;
}

export const MOCK_CODEX_FAILURES_PER_HOUR = buildCodexFailuresPerHour();

export const MOCK_CODEX_HEALTH = [
  {
    hostname: 'yuta-okkotsu',
    status: 'in_sync' as const,
    last_success: iso(12),
    hours_to_expiry: 162.4,
    access_tail: 'a3f1c92e',
  },
  {
    hostname: 'yuta-maki-zenin',
    status: 'near_expiry' as const,
    last_success: iso(55),
    hours_to_expiry: 4.2,
    access_tail: '7b2d44ff',
  },
];

export const MOCK_CODEX_REFRESH_EVENTS = Array.from({ length: 28 }, (_, i) => {
  // Distribute events across the 24h window, alternating hostnames and statuses.
  const cycle = i % 6;
  const status =
    cycle === 0
      ? ('in_sync' as const)
      : cycle === 1
        ? ('rotated' as const)
        : cycle === 2
          ? ('in_sync' as const)
          : cycle === 3
            ? ('near_expiry' as const)
            : cycle === 4
              ? ('in_sync' as const)
              : ('failed' as const);
  const hostname = i % 2 === 0 ? 'yuta-okkotsu' : 'yuta-maki-zenin';
  const accessTail = i % 3 === 0 ? 'a3f1c92e' : i % 3 === 1 ? '7b2d44ff' : '92ce10a4';
  const message =
    status === 'in_sync'
      ? `in_sync access_tail=${accessTail}`
      : status === 'rotated'
        ? `rotated access_tail=OLD8->${accessTail} service_restarted=yes`
        : status === 'near_expiry'
          ? 'WARN access_token_near_expiry hours_remaining=4.2 — run: codex login --device-auth'
          : 'FATAL token_parse_failed';
  return {
    id: `cx-${String(i + 1).padStart(3, '0')}`,
    created_at: iso(i * 47 + 6),
    hostname,
    status,
    access_tail: accessTail,
    message,
  };
});

export const MOCK_ADMIN_ENGAGEMENT = MOCK_ADMIN_USERS.map((u) => ({
  user_id: u.user_id,
  email: u.email,
  sessions_created: u.sessions_count,
  messages_sent: u.messages_count,
  analyses_completed: u.analyses_completed,
  analyses_failed: Math.max(0, Math.floor(u.analyses_completed * 0.08)),
  share_links_created: Math.floor(u.analyses_completed * 0.2),
  feedback_submitted: u.is_admin ? 0 : (u.sessions_count > 6 ? 2 : 0),
  quota_limit_hits: u.sessions_count > 8 ? 3 : 0,
  first_seen: u.created_at,
  last_active: u.last_active,
}));
