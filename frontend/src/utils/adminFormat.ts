/**
 * Number, token and date formatters used by the /admin dashboard.
 *
 * Kept admin-local so the surface stays self-contained and the chat /
 * artifact tracks can evolve their own formatters without coupling.
 */

const NL = 'nl-NL';

const intFormatter = new Intl.NumberFormat(NL);

/** Render an integer (counts, analyses, …) using Dutch grouping. */
export function formatInt(n: number): string {
  return intFormatter.format(n);
}

/**
 * Render a token count as a compact label.
 *   18_420_000 → "18,4M"
 *   1_240_000  → "1,2M"
 *   123_400    → "123k"
 *   870        → "870"
 */
export function formatTokensCompact(n: number): string {
  if (!Number.isFinite(n)) return '—';
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    return `${m.toFixed(m >= 10 ? 0 : 1).replace('.', ',')}M`;
  }
  if (n >= 1_000) {
    return `${Math.round(n / 1_000)}k`;
  }
  return String(n);
}

/**
 * Friendly Dutch absolute date (used for "Aangemaakt" / "Datum" cells).
 * Falls back to the raw string on parse failure so we never render
 * "Invalid Date" in a table cell.
 */
export function formatDateNL(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString(NL, { day: '2-digit', month: 'short', year: 'numeric' });
}

/** "12 mei, 14:32" — compact date+time for activity columns. */
export function formatDateTimeNL(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const date = d.toLocaleDateString(NL, { day: '2-digit', month: 'short' });
  const time = d.toLocaleTimeString(NL, { hour: '2-digit', minute: '2-digit' });
  return `${date}, ${time}`;
}

/** "MM-DD" tick label for chart axes. */
export function formatChartTick(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  return `${mm}-${dd}`;
}
