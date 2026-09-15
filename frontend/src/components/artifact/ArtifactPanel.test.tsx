import { describe, it, expect, beforeAll, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import ArtifactPanel from './ArtifactPanel';
import ReportArtifact from './ReportArtifact';
import type { AnalysisResult, FindingGraph, GraphEdge, GraphNode } from '../../types';

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

// Stub the xyflow canvas — it needs real layout/measurement APIs jsdom lacks.
// The label/colour exports stay real (NodePanel imports them). Selection
// switching against the REAL canvas is covered in GraphView.test.tsx.
vi.mock('../graph/GraphView', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../graph/GraphView')>();
  return {
    ...actual,
    default: ({
      graph,
      scopedNodeIds,
      onSelectNode,
      onSelectEdge,
      onClearSelection,
    }: {
      graph: FindingGraph;
      scopedNodeIds?: readonly string[];
      onSelectNode: (node: GraphNode) => void;
      onSelectEdge?: (edge: GraphEdge) => void;
      onClearSelection?: () => void;
    }) => (
      <div data-testid="graph-view-stub">
        {graph.nodes.map((n) => (
          <button key={n.id} type="button" onClick={() => onSelectNode(n)}>
            {n.claim}
          </button>
        ))}
        {graph.edges.map((e) => (
          <button
            key={e.id}
            type="button"
            data-testid={`edge-stub-${e.id}`}
            onClick={() => onSelectEdge?.(e)}
          >
            edge {e.id}
          </button>
        ))}
        <button type="button" data-testid="pane-stub" onClick={onClearSelection}>
          pane
        </button>
        <div data-testid="scoped-ids-stub">{(scopedNodeIds ?? []).join(',')}</div>
      </div>
    ),
  };
});

const noop = () => {};

const ANALYSIS: AnalysisResult = {
  id: 'a1',
  timestamp: '2026-06-10T00:00:00Z',
  query: 'hoe ontwikkelt de bevolking zich?',
  title: 'Bevolkingsontwikkeling Capelle',
  summary: 'De bevolking groeit gestaag.',
  schema_version: 2,
  blocks: [{ type: 'prose', markdown: 'Groei zet door.' }],
  citations: [],
  sections: [],
} as unknown as AnalysisResult;

function node(id: string, claim: string): GraphNode {
  return {
    id,
    node_type: 'finding',
    claim,
    blocks: [{ type: 'prose', markdown: `Bewijs voor ${id}.` }],
    citations: [],
    confidence: 'medium',
    status: 'proposed',
    agent_label: 'demografie',
    message_id: 'm1',
    created_at: '2026-06-10T00:00:00Z',
    trace_id: null,
  };
}

const GRAPH: FindingGraph = {
  session_id: 's1',
  nodes: [node('n1', 'Groei concentreert zich in Fascinatio'), node('n2', 'Vergrijzing in Schenkel')],
  edges: [],
  summary: 'Twee wijken drijven de demografische verschuiving.',
  version: 2,
};

const EDGE: GraphEdge = {
  id: 'e1',
  source_id: 'n1',
  target_id: 'n2',
  edge_type: 'causes',
  rationale: 'Jonge gezinnen trekken naar Fascinatio waardoor Schenkel relatief vergrijst.',
  created_at: '2026-06-10T00:00:00Z',
};

const GRAPH_WITH_EDGE: FindingGraph = { ...GRAPH, edges: [EDGE] };

function renderPanel(overrides: Partial<Parameters<typeof ArtifactPanel>[0]> = {}) {
  return render(
    <ArtifactPanel
      analysis={ANALYSIS}
      sessionId="s1"
      totalAnalyses={1}
      currentIndex={0}
      onPrev={noop}
      onNext={noop}
      onClose={noop}
      onAskFollowUp={noop}
      graph={null}
      tab="rapport"
      onTabChange={noop}
      onVerdiep={noop}
      {...overrides}
    />,
  );
}

describe('ArtifactPanel — legacy regression (no graph)', () => {
  it('renders EXACTLY the same DOM as a bare ReportArtifact', () => {
    const viaPanel = renderPanel({ graph: null });
    const direct = render(
      <ReportArtifact
        analysis={ANALYSIS}
        sessionId="s1"
        totalAnalyses={1}
        currentIndex={0}
        onPrev={noop}
        onNext={noop}
        onClose={noop}
        onAskFollowUp={noop}
      />,
    );
    expect(viaPanel.container.innerHTML).toBe(direct.container.innerHTML);
  });

  it('shows no tab bar and no graph affordance', () => {
    const { queryByTestId, queryByText } = renderPanel({ graph: null });
    expect(queryByTestId('artifact-tabs')).toBeNull();
    expect(queryByText('Graaf')).toBeNull();
  });

  it('treats an empty graph (no nodes) as no graph', () => {
    const empty: FindingGraph = { session_id: 's1', nodes: [], edges: [], summary: null, version: 0 };
    const { queryByTestId } = renderPanel({ graph: empty });
    expect(queryByTestId('artifact-tabs')).toBeNull();
  });
});

describe('ArtifactPanel — graph present', () => {
  it('shows the [Graaf] [Rapport] toggle with the report still rendered', () => {
    const { getByTestId, getByRole, getAllByText } = renderPanel({
      graph: GRAPH,
      tab: 'rapport',
    });
    expect(getByTestId('artifact-tabs')).toBeTruthy();
    expect(getByRole('tab', { name: 'Graaf' })).toBeTruthy();
    expect(getByRole('tab', { name: 'Rapport' })).toBeTruthy();
    // Report content unchanged underneath.
    expect(getAllByText('Bevolkingsontwikkeling Capelle').length).toBeGreaterThan(0);
  });

  it('tab switch callback fires when Graaf is clicked', () => {
    const onTabChange = vi.fn();
    const { getByRole } = renderPanel({ graph: GRAPH, tab: 'rapport', onTabChange });
    fireEvent.click(getByRole('tab', { name: 'Graaf' }));
    expect(onTabChange).toHaveBeenCalledWith('graaf');
  });

  it('graaf tab renders the live summary and the graph canvas', () => {
    const { getByTestId, getByText } = renderPanel({ graph: GRAPH, tab: 'graaf' });
    expect(getByTestId('graph-view-stub')).toBeTruthy();
    expect(getByText('Twee wijken drijven de demografische verschuiving.')).toBeTruthy();
    expect(getByText('2 nodes · 0 relaties')).toBeTruthy();
  });

  it('clicking a node opens the NodePanel; Verdiep dit reaches the callback', () => {
    const onVerdiep = vi.fn();
    const { getByText, getByTestId } = renderPanel({ graph: GRAPH, tab: 'graaf', onVerdiep });
    fireEvent.click(getByText('Groei concentreert zich in Fascinatio'));
    expect(getByTestId('node-panel')).toBeTruthy();
    fireEvent.click(getByText('Verdiep dit'));
    expect(onVerdiep).toHaveBeenCalledWith(
      expect.objectContaining({ id: 'n1', claim: 'Groei concentreert zich in Fascinatio' }),
    );
  });

  it('clicking a SECOND node re-points the NodePanel at it', () => {
    const { getByText, getByTestId } = renderPanel({ graph: GRAPH, tab: 'graaf' });
    fireEvent.click(getByText('Groei concentreert zich in Fascinatio'));
    expect(getByTestId('node-panel').querySelector('h2')?.textContent).toBe(
      'Groei concentreert zich in Fascinatio',
    );
    fireEvent.click(getByText('Vergrijzing in Schenkel'));
    expect(getByTestId('node-panel').querySelector('h2')?.textContent).toBe(
      'Vergrijzing in Schenkel',
    );
  });

  it('a background (pane) click closes the NodePanel', () => {
    const { getByText, getByTestId, queryByTestId } = renderPanel({ graph: GRAPH, tab: 'graaf' });
    fireEvent.click(getByText('Groei concentreert zich in Fascinatio'));
    expect(getByTestId('node-panel')).toBeTruthy();
    fireEvent.click(getByTestId('pane-stub'));
    expect(queryByTestId('node-panel')).toBeNull();
  });

  it('forwards the scoped ids to the canvas and flags a scoped node in its panel', () => {
    const { getByText, getByTestId } = renderPanel({
      graph: GRAPH,
      tab: 'graaf',
      scopedNodeIds: ['n2'],
    });
    expect(getByTestId('scoped-ids-stub').textContent).toBe('n2');
    fireEvent.click(getByText('Vergrijzing in Schenkel'));
    expect(getByText('Verwijder uit verdieping')).toBeTruthy();
  });

  it('scopeFull disables adding an unscoped node from its panel', () => {
    const { getByText } = renderPanel({
      graph: GRAPH,
      tab: 'graaf',
      scopedNodeIds: ['n2'],
      scopeFull: true,
    });
    fireEvent.click(getByText('Groei concentreert zich in Fascinatio'));
    expect((getByText('Verdiep dit') as HTMLButtonElement).disabled).toBe(true);
  });

  it('falls back to the graph (Rapport disabled) when no analysis exists yet', () => {
    const { getByRole, getByTestId } = renderPanel({
      graph: GRAPH,
      analysis: null,
      tab: 'rapport', // requested rapport, but none exists → graaf wins
    });
    expect(getByTestId('graph-view-stub')).toBeTruthy();
    const rapportTab = getByRole('tab', { name: 'Rapport' }) as HTMLButtonElement;
    expect(rapportTab.disabled).toBe(true);
  });
});

describe('ArtifactPanel — edge content panel', () => {
  it('clicking an edge opens the EdgePanel with gloss, rationale and endpoints', () => {
    const { getByTestId, getByText } = renderPanel({ graph: GRAPH_WITH_EDGE, tab: 'graaf' });
    fireEvent.click(getByTestId('edge-stub-e1'));
    expect(getByTestId('edge-panel')).toBeTruthy();
    expect(getByText('veroorzaakt')).toBeTruthy();
    expect(getByTestId('edge-rationale').textContent).toContain(
      'Jonge gezinnen trekken naar Fascinatio',
    );
    expect(getByTestId('edge-panel-source').textContent).toContain(
      'Groei concentreert zich in Fascinatio',
    );
    expect(getByTestId('edge-panel-target').textContent).toContain('Vergrijzing in Schenkel');
  });

  it('edge and node selection are mutually exclusive in the panel slot', () => {
    const { getByTestId, getByText, getByRole, queryByTestId } = renderPanel({
      graph: GRAPH_WITH_EDGE,
      tab: 'graaf',
    });
    // Node first → NodePanel only.
    fireEvent.click(getByText('Groei concentreert zich in Fascinatio'));
    expect(getByTestId('node-panel')).toBeTruthy();
    expect(queryByTestId('edge-panel')).toBeNull();
    // Edge replaces it → EdgePanel only.
    fireEvent.click(getByTestId('edge-stub-e1'));
    expect(getByTestId('edge-panel')).toBeTruthy();
    expect(queryByTestId('node-panel')).toBeNull();
    // Node again → back to NodePanel only. (Exact accessible name: the
    // open EdgePanel's NAAR card also contains this claim text.)
    fireEvent.click(getByRole('button', { name: 'Vergrijzing in Schenkel' }));
    expect(getByTestId('node-panel')).toBeTruthy();
    expect(queryByTestId('edge-panel')).toBeNull();
  });

  it('clicking the VAN endpoint in the EdgePanel re-points selection at that node', () => {
    const { getByTestId, queryByTestId } = renderPanel({ graph: GRAPH_WITH_EDGE, tab: 'graaf' });
    fireEvent.click(getByTestId('edge-stub-e1'));
    fireEvent.click(getByTestId('edge-panel-source'));
    expect(queryByTestId('edge-panel')).toBeNull();
    expect(getByTestId('node-panel').querySelector('h2')?.textContent).toBe(
      'Groei concentreert zich in Fascinatio',
    );
  });

  it('clicking the NAAR endpoint in the EdgePanel re-points selection at that node', () => {
    const { getByTestId, queryByTestId } = renderPanel({ graph: GRAPH_WITH_EDGE, tab: 'graaf' });
    fireEvent.click(getByTestId('edge-stub-e1'));
    fireEvent.click(getByTestId('edge-panel-target'));
    expect(queryByTestId('edge-panel')).toBeNull();
    expect(getByTestId('node-panel').querySelector('h2')?.textContent).toBe(
      'Vergrijzing in Schenkel',
    );
  });

  it('a background (pane) click closes the EdgePanel', () => {
    const { getByTestId, queryByTestId } = renderPanel({ graph: GRAPH_WITH_EDGE, tab: 'graaf' });
    fireEvent.click(getByTestId('edge-stub-e1'));
    expect(getByTestId('edge-panel')).toBeTruthy();
    fireEvent.click(getByTestId('pane-stub'));
    expect(queryByTestId('edge-panel')).toBeNull();
  });

  it("the EdgePanel's close button clears the edge selection", () => {
    const { getByTestId, getByLabelText, queryByTestId } = renderPanel({
      graph: GRAPH_WITH_EDGE,
      tab: 'graaf',
    });
    fireEvent.click(getByTestId('edge-stub-e1'));
    fireEvent.click(getByLabelText('Sluit relatiepaneel'));
    expect(queryByTestId('edge-panel')).toBeNull();
  });
});
