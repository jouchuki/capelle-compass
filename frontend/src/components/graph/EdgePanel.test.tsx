import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import EdgePanel, { truncateClaim } from './EdgePanel';
import type { GraphEdge, GraphNode } from '../../types';

function graphNode(id: string, claim: string): GraphNode {
  return {
    id,
    node_type: 'finding',
    claim,
    blocks: [],
    citations: [],
    confidence: 'medium',
    status: 'supported',
    agent_label: 'budget-agent',
    message_id: 'm1',
    created_at: '2026-06-10T00:00:00Z',
    trace_id: null,
  };
}

function graphEdge(overrides: Partial<GraphEdge> = {}): GraphEdge {
  return {
    id: 'e1',
    source_id: 'n1',
    target_id: 'n2',
    edge_type: 'explains',
    rationale:
      'De instroom in jeugdhulp groeit sneller dan het budget omdat de verwijsroute via huisartsen buiten de gemeentelijke toegang om loopt. Daardoor stuurt de gemeente op een steeds kleiner deel van de instroom.',
    created_at: '2026-06-10T00:00:00Z',
    ...overrides,
  };
}

const SOURCE = graphNode('n1', 'Verwijzingen via de huisarts stijgen');
const TARGET = graphNode('n2', 'Het jeugdzorgbudget wordt jaarlijks overschreden');

function renderPanel(overrides: {
  edge?: GraphEdge;
  sourceNode?: GraphNode | null;
  targetNode?: GraphNode | null;
  onClose?: () => void;
  onSelectNode?: (node: GraphNode) => void;
} = {}) {
  return render(
    <EdgePanel
      edge={overrides.edge ?? graphEdge()}
      sourceNode={overrides.sourceNode === undefined ? SOURCE : overrides.sourceNode}
      targetNode={overrides.targetNode === undefined ? TARGET : overrides.targetNode}
      onClose={overrides.onClose ?? (() => {})}
      onSelectNode={overrides.onSelectNode ?? (() => {})}
    />,
  );
}

describe('EdgePanel', () => {
  it('renders the edge-type gloss, the rationale and both endpoint claims', () => {
    const { getByText, getByTestId } = renderPanel();
    // Dutch gloss for `explains` from the shared EDGE_TYPE_LABEL map.
    expect(getByText('verklaart')).toBeTruthy();
    expect(getByTestId('edge-rationale').textContent).toContain(
      'buiten de gemeentelijke toegang om',
    );
    expect(getByText('VAN')).toBeTruthy();
    expect(getByText('Verwijzingen via de huisarts stijgen')).toBeTruthy();
    expect(getByText('NAAR')).toBeTruthy();
    expect(getByText('Het jeugdzorgbudget wordt jaarlijks overschreden')).toBeTruthy();
  });

  it('truncates a long endpoint claim to ~80 characters with an ellipsis', () => {
    const longClaim =
      'De gemeente Capelle aan den IJssel ziet de kosten voor maatwerkdienstverlening aan jongeren onder de achttien jaar al zes jaar op rij sneller stijgen dan begroot';
    const { getByTestId } = renderPanel({ sourceNode: graphNode('n1', longClaim) });
    const sourceText = getByTestId('edge-panel-source').textContent ?? '';
    expect(sourceText).toContain('…');
    expect(sourceText).not.toContain('sneller stijgen dan begroot');
    expect(sourceText).toContain(longClaim.slice(0, 40));
  });

  it('truncateClaim leaves short claims untouched', () => {
    expect(truncateClaim('korte claim')).toBe('korte claim');
  });

  it('clicking the VAN claim hands the source node to onSelectNode', () => {
    const onSelectNode = vi.fn();
    const { getByTestId } = renderPanel({ onSelectNode });
    fireEvent.click(getByTestId('edge-panel-source'));
    expect(onSelectNode).toHaveBeenCalledWith(SOURCE);
  });

  it('clicking the NAAR claim hands the target node to onSelectNode', () => {
    const onSelectNode = vi.fn();
    const { getByTestId } = renderPanel({ onSelectNode });
    fireEvent.click(getByTestId('edge-panel-target'));
    expect(onSelectNode).toHaveBeenCalledWith(TARGET);
  });

  it('shows a muted fallback when the rationale is empty', () => {
    const { getByTestId, queryByTestId } = renderPanel({
      edge: graphEdge({ rationale: '' }),
    });
    expect(getByTestId('edge-rationale-empty').textContent).toBe('Geen toelichting vastgelegd.');
    expect(queryByTestId('edge-rationale')).toBeNull();
  });

  it('treats a whitespace-only rationale as empty', () => {
    const { getByTestId } = renderPanel({ edge: graphEdge({ rationale: '   \n ' }) });
    expect(getByTestId('edge-rationale-empty')).toBeTruthy();
  });

  it('a `causes` edge carries the "counterfactual vereist" eyebrow; others do not', () => {
    const causal = renderPanel({ edge: graphEdge({ edge_type: 'causes' }) });
    expect(causal.getByTestId('edge-panel-counterfactual').textContent).toBe(
      'counterfactual vereist',
    );
    expect(causal.getByText('veroorzaakt')).toBeTruthy();
    causal.unmount();

    const nonCausal = renderPanel();
    expect(nonCausal.queryByTestId('edge-panel-counterfactual')).toBeNull();
  });

  it('renders a non-clickable placeholder for an endpoint missing from the graph', () => {
    const onSelectNode = vi.fn();
    const { getByTestId } = renderPanel({ sourceNode: null, onSelectNode });
    const placeholder = getByTestId('edge-panel-source');
    expect(placeholder.textContent).toContain('Node niet gevonden.');
    fireEvent.click(placeholder);
    expect(onSelectNode).not.toHaveBeenCalled();
  });

  it('close button fires onClose', () => {
    const onClose = vi.fn();
    const { getByLabelText } = renderPanel({ onClose });
    fireEvent.click(getByLabelText('Sluit relatiepaneel'));
    expect(onClose).toHaveBeenCalled();
  });
});
