/**
 * Pure mappers from the real backend ``ToolOutput`` / ``ChartHint`` shapes
 * to recharts-friendly data structures.
 *
 * Recharts wants flat arrays of records with predictable keys; the agent
 * returns column-defined tabular data where the X/Y axes are pointed at
 * by ``ChartHint.x`` / ``.y``. Translating once in a single utility keeps
 * the chart components dumb and gives us a unit-testable seam.
 */

import type {
  ChartHint,
  ColumnDef,
  ToolOutput,
} from '../types';

/** Coerce an unknown cell value to a number, falling back to 0. */
function toNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]/g, ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

/** Coerce an unknown cell value to a display string. */
function toLabel(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

/** First Y column to use for ChartHint.y which may be string or string[]. */
function firstYKey(y: string | string[]): string {
  if (Array.isArray(y)) return y[0] ?? '';
  return y;
}

/** All Y columns as an array. */
function yKeys(y: string | string[]): string[] {
  return Array.isArray(y) ? y : [y];
}

export function toBarChartData(
  output: ToolOutput,
  hint: ChartHint,
): Array<{ name: string; value: number }> {
  const yKey = firstYKey(hint.y);
  return output.data.map((row) => ({
    name: toLabel(row[hint.x]),
    value: toNumber(row[yKey]),
  }));
}

export function toLineChartData(
  output: ToolOutput,
  hint: ChartHint,
): Array<Record<string, string | number>> {
  const ys = yKeys(hint.y);
  return output.data.map((row) => {
    const out: Record<string, string | number> = { name: toLabel(row[hint.x]) };
    for (const key of ys) {
      out[key] = toNumber(row[key]);
    }
    return out;
  });
}

export function toTreemapData(
  output: ToolOutput,
  hint: ChartHint,
): Array<{ name: string; size: number }> {
  const yKey = firstYKey(hint.y);
  return output.data.map((row) => ({
    name: toLabel(row[hint.x]),
    size: toNumber(row[yKey]),
  }));
}

/**
 * Pivot long-format forecast rows (one row per period × dimension_value)
 * into recharts wide-format: one row per period with three keys per group
 * (`<group>_pred`, `<group>_lower`, `<group>_upper`). The chart layer then
 * draws one Line + one banded Area per group without re-pivoting.
 */
export function toForecastChartData(
  output: ToolOutput,
  hint: ChartHint,
): {
  rows: Array<Record<string, string | number | null>>;
  groups: string[];
} {
  const xKey = hint.x;
  const predKey = firstYKey(hint.y);
  const lowerKey = hint.lower ?? 'lower';
  const upperKey = hint.upper ?? 'upper';
  const groupKey = hint.group_by ?? '';

  // Bucket rows by period (X axis).
  const byPeriod = new Map<string, Record<string, string | number | null>>();
  const groups = new Set<string>();

  for (const row of output.data) {
    const periodLabel = toLabel(row[xKey]);
    const group = groupKey ? toLabel(row[groupKey]) : 'value';
    groups.add(group);

    let bucket = byPeriod.get(periodLabel);
    if (!bucket) {
      bucket = { name: periodLabel };
      byPeriod.set(periodLabel, bucket);
    }
    bucket[`${group}_pred`] = toNumber(row[predKey]);
    // lower / upper allowed to coincide (historical observations have
    // zero-width CIs) — we still emit them so the Area renderer is happy.
    bucket[`${group}_lower`] = toNumber(row[lowerKey]);
    bucket[`${group}_upper`] = toNumber(row[upperKey]);
  }

  // Sort numerically by period (year). Falls back to lexicographic when
  // the X axis isn't a year (rare for forecasts, but safe).
  const rows = Array.from(byPeriod.values()).sort((a, b) => {
    const an = toNumber(a.name);
    const bn = toNumber(b.name);
    if (an && bn) return an - bn;
    return String(a.name).localeCompare(String(b.name));
  });

  return { rows, groups: Array.from(groups) };
}

export function toTableRows(
  output: ToolOutput,
): Array<Record<string, string | number>> {
  return output.data.map((row) => {
    const out: Record<string, string | number> = {};
    for (const col of output.columns) {
      const raw = row[col.key];
      if (col.type === 'number' || col.type === 'year') {
        out[col.key] = toNumber(raw);
      } else {
        out[col.key] = toLabel(raw);
      }
    }
    return out;
  });
}

/** Dutch label for a source — used by SourceBadge and the bronnen-footer. */
export function sourceLabel(s: string): string {
  switch (s) {
    case 'cbs':
      return 'CBS';
    case 'beleid':
      return 'Beleid';
    case 'budget':
      return 'Budget';
    case 'buitenbeter':
      return 'BuitenBeter';
    case 'bewonersenquete':
      return 'Bewonersenquête';
    case 'platform':
      return 'Platform';
    case 'jeugdzorg':
      return 'Jeugdzorg';
    case 'regelgeving':
      return 'Regelgeving';
    default:
      return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Bron';
  }
}

/** Tailwind class for the matching source chip palette. */
export function sourceChipClass(s: string): string {
  return `source-chip-${s}`;
}

/** Pretty-print a number with a unit suffix, if a ColumnDef carries one. */
export function formatCell(
  value: string | number,
  col: ColumnDef | undefined,
): string {
  // Years are identifiers, not quantities — never apply a thousands separator
  // (otherwise 2025 renders as "2.025" under nl-NL grouping).
  if (col?.type === 'year' && typeof value === 'number') {
    return String(Math.trunc(value));
  }
  if (col && col.type === 'number' && typeof value === 'number') {
    if (col.unit) return `${value.toLocaleString('nl-NL')} ${col.unit}`;
    return value.toLocaleString('nl-NL');
  }
  return String(value);
}
