import { describe, it, expect, beforeAll, vi } from 'vitest';
import { render } from '@testing-library/react';
import ReportArtifact from './ReportArtifact';
import ChartRenderer from './ChartRenderer';
import type { AnalysisResult, ChartSpec } from '../../types';
import realBlockReport from '../../test/fixtures/real-block-report.json';
import sessionD1a378 from '../../test/fixtures/session-d1a378.json';

// recharts' ResponsiveContainer needs ResizeObserver, absent in jsdom.
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver =
      class {
        observe() {}
        unobserve() {}
        disconnect() {}
      };
  }
});

vi.mock('../../api/client', () => ({
  createForkLink: vi.fn(),
  recordEvent: vi.fn(),
}));

const noop = () => {};

function renderArtifact(analysis: AnalysisResult) {
  return render(
    <ReportArtifact
      analysis={analysis}
      sessionId="s1"
      totalAnalyses={1}
      currentIndex={0}
      onPrev={noop}
      onNext={noop}
      onClose={noop}
      onAskFollowUp={noop}
    />,
  );
}

describe('ReportArtifact resilience to real / imperfect agent reports', () => {
  it('renders a real v2 block report with no `sections` field', () => {
    expect(() =>
      renderArtifact(realBlockReport as unknown as AnalysisResult),
    ).not.toThrow();
  });

  it('renders the captured session d1a378 report (empty-data chart + tables)', () => {
    expect(() =>
      renderArtifact(sessionD1a378 as unknown as AnalysisResult),
    ).not.toThrow();
  });

  it('renders a block report whose chart block is missing `data`', () => {
    const analysis = {
      schema_version: 2,
      id: 'x',
      timestamp: '2026-01-01T00:00:00Z',
      query: 'q',
      summary: 's',
      citations: [],
      // chart spec with NO data / NO columns — must not crash, falls back to table
      blocks: [
        { type: 'chart', spec: { kind: 'line', x: 'a', y: 'b' } },
      ],
    } as unknown as AnalysisResult;
    expect(() => renderArtifact(analysis)).not.toThrow();
  });

  it('renders a block report whose table block is missing `columns`/`data`', () => {
    const analysis = {
      schema_version: 2,
      id: 'x',
      timestamp: '2026-01-01T00:00:00Z',
      query: 'q',
      summary: 's',
      citations: [],
      blocks: [{ type: 'table', caption: 'x' }],
    } as unknown as AnalysisResult;
    expect(() => renderArtifact(analysis)).not.toThrow();
  });
});

describe('ReportArtifact report title', () => {
  const base = {
    schema_version: 2,
    id: 'x',
    timestamp: '2026-01-01T00:00:00Z',
    query: 'wat zijn de jeugdzorgkosten?',
    summary: 's',
    citations: [],
    blocks: [],
  };

  it('renders the agent title, not the query, when title is present', () => {
    const { getAllByText, queryByText } = renderArtifact({
      ...base,
      title: 'Jeugdzorgkosten Capelle 2020-2026',
    } as unknown as AnalysisResult);
    // appears in both the top bar and the <h1>
    expect(getAllByText('Jeugdzorgkosten Capelle 2020-2026').length).toBeGreaterThan(0);
    expect(queryByText('wat zijn de jeugdzorgkosten?')).toBeNull();
  });

  it('falls back to the query when title is absent', () => {
    const { getAllByText } = renderArtifact(base as unknown as AnalysisResult);
    expect(getAllByText('wat zijn de jeugdzorgkosten?').length).toBeGreaterThan(0);
  });

  it('no longer renders the static "Beleidsanalyse" eyebrow', () => {
    const { queryByText } = renderArtifact(base as unknown as AnalysisResult);
    expect(queryByText('Beleidsanalyse')).toBeNull();
  });
});

describe('ChartRenderer does not throw on a spec missing data', () => {
  it('falls back to a table instead of reading .length of undefined', () => {
    const spec = { kind: 'bar', x: 'a', y: 'b' } as unknown as ChartSpec;
    expect(() => render(<ChartRenderer spec={spec} />)).not.toThrow();
  });
});
