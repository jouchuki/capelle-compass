import { describe, it, expect } from 'vitest';
import { validateChartSpec } from './chartContracts';
import type { ChartSpec } from '../types';

const cols = [
  { key: 'wijk', label: 'Wijk', type: 'string' as const },
  { key: 'score', label: 'Score', type: 'number' as const },
  { key: 'bevolking', label: 'Bevolking', type: 'number' as const },
];
const data = [
  { wijk: 'Schollevaar', score: 6.2, bevolking: 1200 },
  { wijk: 'Fascinatio', score: 7.1, bevolking: 800 },
];

function spec(over: Partial<ChartSpec>): ChartSpec {
  return { kind: 'bar', data, columns: cols, x: 'wijk', y: 'score', ...over };
}

describe('validateChartSpec', () => {
  it('accepts a well-formed bar spec', () => {
    expect(validateChartSpec(spec({ kind: 'bar' })).ok).toBe(true);
  });

  it('rejects a bar spec missing x', () => {
    const r = validateChartSpec(spec({ kind: 'bar', x: undefined }));
    expect(r.ok).toBe(false);
    expect(r.reason).toMatch(/x/i);
  });

  it('rejects a bar spec whose y column is absent from data', () => {
    const r = validateChartSpec(spec({ kind: 'bar', y: 'missing' }));
    expect(r.ok).toBe(false);
  });

  it('rejects empty data', () => {
    const r = validateChartSpec(spec({ data: [] }));
    expect(r.ok).toBe(false);
  });

  it('requires >=2 numeric y series for grouped_bar', () => {
    expect(validateChartSpec(spec({ kind: 'grouped_bar', y: 'score' })).ok).toBe(false);
    expect(validateChartSpec(spec({ kind: 'grouped_bar', y: ['score', 'bevolking'] })).ok).toBe(true);
    expect(validateChartSpec(spec({ kind: 'grouped_bar', y: ['score', 'wijk'] })).ok).toBe(false);
  });

  it('requires a single numeric value column for histogram', () => {
    expect(validateChartSpec(spec({ kind: 'histogram', x: undefined, y: 'score' })).ok).toBe(true);
    expect(validateChartSpec(spec({ kind: 'histogram', x: undefined, y: undefined })).ok).toBe(false);
  });

  it('requires x and y for scatter', () => {
    expect(validateChartSpec(spec({ kind: 'scatter', x: 'score', y: 'score' })).ok).toBe(true);
    expect(validateChartSpec(spec({ kind: 'scatter', x: undefined })).ok).toBe(false);
  });

  it('boxplot/violin require a numeric value column', () => {
    expect(validateChartSpec(spec({ kind: 'boxplot', y: 'score' })).ok).toBe(true);
    expect(validateChartSpec(spec({ kind: 'violin', y: 'score' })).ok).toBe(true);
    expect(validateChartSpec(spec({ kind: 'violin', y: 'wijk' })).ok).toBe(false);
  });

  it('flags an unknown kind', () => {
    // @ts-expect-error deliberately invalid kind
    expect(validateChartSpec(spec({ kind: 'sankey' })).ok).toBe(false);
  });
});
