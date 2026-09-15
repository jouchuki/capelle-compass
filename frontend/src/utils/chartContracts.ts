import type { ChartKind, ChartSpec, ColumnDef } from '../types';

export interface ContractResult {
  ok: boolean;
  reason?: string;
}

/** True when `key` is a column present in the spec's data + columns. */
function hasColumn(spec: ChartSpec, key: string | undefined): key is string {
  if (!key) return false;
  if (!spec.columns.some((c) => c.key === key)) return false;
  if (spec.data.length === 0) return false;
  const first = spec.data[0];
  return first !== undefined && key in first;
}

function isNumeric(spec: ChartSpec, key: string): boolean {
  const col: ColumnDef | undefined = spec.columns.find((c) => c.key === key);
  return col?.type === 'number' || col?.type === 'year';
}

function yList(y: ChartSpec['y']): string[] {
  if (y === undefined) return [];
  return Array.isArray(y) ? y : [y];
}

type Validator = (spec: ChartSpec) => ContractResult;

const ok: ContractResult = { ok: true };
const fail = (reason: string): ContractResult => ({ ok: false, reason });

/** x + a single numeric y present in the data. */
const categoryValue: Validator = (s) => {
  if (!hasColumn(s, s.x)) return fail('x column missing from data');
  const ys = yList(s.y);
  if (ys.length === 0) return fail('y column required');
  if (!hasColumn(s, ys[0])) return fail(`y column "${ys[0]}" missing from data`);
  if (!isNumeric(s, ys[0])) return fail(`y column "${ys[0]}" is not numeric`);
  return ok;
};

/** x + >=2 numeric y series. */
const multiSeries: Validator = (s) => {
  if (!hasColumn(s, s.x)) return fail('x column missing from data');
  const ys = yList(s.y);
  if (ys.length < 2) return fail('needs >=2 y series');
  for (const y of ys) {
    if (!hasColumn(s, y)) return fail(`y column "${y}" missing from data`);
    if (!isNumeric(s, y)) return fail(`y column "${y}" is not numeric`);
  }
  return ok;
};

/** A single numeric value column (no x needed). */
const numericValue: Validator = (s) => {
  const ys = yList(s.y);
  if (ys.length === 0) return fail('numeric value column required');
  if (!hasColumn(s, ys[0])) return fail(`value column "${ys[0]}" missing from data`);
  if (!isNumeric(s, ys[0])) return fail(`value column "${ys[0]}" is not numeric`);
  return ok;
};

/** Numeric x and numeric y. */
const xyNumeric: Validator = (s) => {
  if (!hasColumn(s, s.x) || !isNumeric(s, s.x as string)) return fail('numeric x required');
  const ys = yList(s.y);
  if (ys.length === 0 || !hasColumn(s, ys[0]) || !isNumeric(s, ys[0])) {
    return fail('numeric y required');
  }
  return ok;
};

export const CHART_CONTRACTS: Record<ChartKind, Validator> = {
  bar: categoryValue,
  line: categoryValue,
  area: categoryValue,
  pie: categoryValue,
  donut: categoryValue,
  treemap: categoryValue,
  grouped_bar: multiSeries,
  stacked_bar: multiSeries,
  histogram: numericValue,
  boxplot: numericValue,
  violin: numericValue,
  scatter: xyNumeric,
};

/** Validate a ChartSpec against its kind's data-shape contract. */
export function validateChartSpec(spec: ChartSpec): ContractResult {
  // Agent-produced specs are semi-trusted: a chart block may omit data or
  // columns entirely. Treat any non-conforming shape as a contract failure
  // so the renderer falls back to a table instead of throwing.
  if (!Array.isArray(spec.data) || spec.data.length === 0) return fail('no data');
  if (!Array.isArray(spec.columns)) return fail('no columns');
  const validator = CHART_CONTRACTS[spec.kind];
  if (!validator) return fail(`unknown chart kind "${spec.kind}"`);
  return validator(spec);
}
