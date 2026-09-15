import type {
  AnalysisResult,
  Block,
  ChartKind,
  ChartSpec,
  ChartType,
  ToolOutput,
} from '../types';

function toNumber(value: unknown): number {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string') {
    const parsed = Number(value.replace(/[, ]/g, ''));
    return Number.isFinite(parsed) ? parsed : 0;
  }
  return 0;
}

function toLabel(value: unknown): string {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  return '';
}

function firstY(y: ChartSpec['y']): string {
  if (y === undefined) return '';
  if (Array.isArray(y)) return y.length > 0 ? (y[0] as string) : '';
  return y;
}

/** Pie/donut data: {name,value} from x + first y. */
export function toPieData(spec: ChartSpec): Array<{ name: string; value: number }> {
  const yKey = firstY(spec.y);
  return spec.data.map((row) => ({
    name: toLabel(row[spec.x ?? '']),
    value: toNumber(row[yKey]),
  }));
}

/** Scatter data: {x,y} numeric pairs. */
export function toScatterData(spec: ChartSpec): Array<{ x: number; y: number }> {
  const yKey = firstY(spec.y);
  return spec.data.map((row) => ({
    x: toNumber(row[spec.x ?? '']),
    y: toNumber(row[yKey]),
  }));
}

/** Histogram: bucket the value column into `binCount` equal-width bins. */
export function toHistogramData(
  spec: ChartSpec,
  binCount = 10,
): Array<{ name: string; value: number }> {
  const yKey = firstY(spec.y);
  const values = spec.data.map((row) => toNumber(row[yKey]));
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const width = span / binCount;
  const bins = Array.from({ length: binCount }, (_, i) => ({
    name: `${(min + i * width).toFixed(1)}–${(min + (i + 1) * width).toFixed(1)}`,
    value: 0,
  }));
  for (const v of values) {
    const idx = Math.min(binCount - 1, Math.floor((v - min) / width));
    bins[idx]!.value += 1;
  }
  return bins;
}

/** Extract the numeric value column for distribution charts (boxplot/violin). */
export function toDistributionValues(spec: ChartSpec): number[] {
  const yKey = firstY(spec.y);
  return spec.data.map((row) => toNumber(row[yKey]));
}

/** Map a legacy ChartType (chart_hints) to the v2 ChartKind catalog. */
function chartTypeToKind(t: ChartType): ChartKind {
  switch (t) {
    case 'bar':
      return 'bar';
    case 'line':
    case 'forecast':
      return 'line';
    case 'treemap':
      return 'treemap';
    case 'map':
    case 'table':
    default:
      return 'bar';
  }
}

function toolOutputToChartSpec(output: ToolOutput): ChartSpec | null {
  const hint = output.chart_hints?.[0];
  if (!hint || hint.type === 'table' || hint.type === 'map') return null;
  return {
    kind: chartTypeToKind(hint.type),
    data: output.data,
    columns: output.columns,
    x: hint.x,
    y: hint.y,
    group_by: hint.group_by,
    title: hint.title,
  };
}

/**
 * Adapter: render a legacy `sections[]` report through the block path.
 * Each section becomes heading + prose + (chart | table | nothing).
 */
export function sectionsToBlocks(analysis: AnalysisResult): Block[] {
  const blocks: Block[] = [];
  for (const section of analysis.sections ?? []) {
    blocks.push({ type: 'heading', level: 2, text: section.heading });
    if (section.content) {
      blocks.push({ type: 'prose', markdown: section.content });
    }
    const output = section.tool_output;
    // Guard: a v1 fallback section can carry a tool_output stub with no
    // data array (or no columns). Accessing output.data.length on such a
    // stub throws and crashes the whole report — skip it instead.
    if (output && Array.isArray(output.data) && output.data.length > 0) {
      const spec = toolOutputToChartSpec(output);
      if (spec) {
        blocks.push({ type: 'chart', spec, caption: output.query });
      } else {
        blocks.push({
          type: 'table',
          columns: Array.isArray(output.columns) ? output.columns : [],
          data: output.data,
          caption: output.query,
        });
      }
    }
  }
  return blocks;
}
