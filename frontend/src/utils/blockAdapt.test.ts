import { describe, it, expect } from 'vitest';
import { sectionsToBlocks, toPieData, toHistogramData } from './blockAdapt';
import type { AnalysisResult, ChartSpec } from '../types';

const cols = [
  { key: 'wijk', label: 'Wijk', type: 'string' as const },
  { key: 'score', label: 'Score', type: 'number' as const },
];
const data = [
  { wijk: 'A', score: 6 },
  { wijk: 'B', score: 8 },
];

describe('toPieData', () => {
  it('maps x->name, y->value', () => {
    const spec: ChartSpec = { kind: 'pie', data, columns: cols, x: 'wijk', y: 'score' };
    expect(toPieData(spec)).toEqual([
      { name: 'A', value: 6 },
      { name: 'B', value: 8 },
    ]);
  });
});

describe('toHistogramData', () => {
  it('buckets numeric values into bins', () => {
    const spec: ChartSpec = {
      kind: 'histogram',
      data: [{ v: 1 }, { v: 2 }, { v: 2 }, { v: 9 }],
      columns: [{ key: 'v', label: 'V', type: 'number' }],
      y: 'v',
    };
    const bins = toHistogramData(spec, 3);
    expect(bins).toHaveLength(3);
    expect(bins.reduce((a, b) => a + b.value, 0)).toBe(4); // all values counted
  });
});

describe('sectionsToBlocks', () => {
  const legacy: AnalysisResult = {
    id: 'x',
    timestamp: '2026-01-01T00:00:00Z',
    query: 'q',
    summary: 'samenvatting',
    sections: [
      {
        heading: 'Veiligheid',
        source: 'cbs',
        content: 'tekst',
        tool_output: {
          tool: 'cbs',
          query: 'cbs get 47018NED',
          result_type: 'table',
          data,
          columns: cols,
          chart_hints: [{ type: 'bar', x: 'wijk', y: 'score', title: 'Score' }],
        },
      },
    ],
  };

  it('emits a heading + prose + chart block for a charted section', () => {
    const blocks = sectionsToBlocks(legacy);
    const b0 = blocks[0];
    const b1 = blocks[1];
    const b2 = blocks[2];
    expect(b0).toMatchObject({ type: 'heading', text: 'Veiligheid' });
    expect(b1).toMatchObject({ type: 'prose', markdown: 'tekst' });
    expect(b2).toMatchObject({ type: 'chart' });
    if (b2 !== undefined && b2.type === 'chart') {
      expect(b2.spec.kind).toBe('bar');
    }
  });

  it('emits a table block when a section has tool_output but no chart_hints', () => {
    const origOutput = legacy.sections[0]!.tool_output!;
    const noHint: AnalysisResult = {
      ...legacy,
      sections: [{
        heading: 'Veiligheid',
        source: 'cbs',
        content: 'tekst',
        tool_output: { ...origOutput, chart_hints: undefined },
      }],
    };
    const blocks = sectionsToBlocks(noHint);
    expect(blocks.some((b) => b.type === 'table')).toBe(true);
  });

  it('emits only heading + prose for a text-only section', () => {
    const textOnly: AnalysisResult = {
      ...legacy,
      sections: [{ heading: 'H', source: 'platform', content: 'alleen tekst' }],
    };
    const blocks = sectionsToBlocks(textOnly);
    expect(blocks.map((b) => b.type)).toEqual(['heading', 'prose']);
  });
});
