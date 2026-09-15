import { describe, it, expect, vi } from 'vitest';
import { fireEvent, render } from '@testing-library/react';
import NodePanel from './NodePanel';
import type { GraphNode } from '../../types';

function graphNode(overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id: 'n1',
    node_type: 'finding',
    claim: 'De jeugdzorgkosten stijgen sneller dan het landelijk gemiddelde',
    blocks: [
      { type: 'heading', level: 2, text: 'Kostengroei 2020-2026' },
      {
        type: 'prose',
        markdown: 'De uitgaven groeiden met **42%** in zes jaar.',
        citations: ['c1'],
      },
      { type: 'kpi', value: '42', label: 'Groei', unit: '%' },
    ],
    citations: [
      {
        id: 'c1',
        source: 'budget',
        doc_type: 'Taakveld',
        document: 'Iv3 taakveld 6.72 Maatwerkdienstverlening 18-',
        year: '2026',
      },
    ],
    confidence: 'high',
    status: 'supported',
    agent_label: 'budget-agent',
    message_id: 'm1',
    created_at: '2026-06-10T00:00:00Z',
    trace_id: null,
    ...overrides,
  };
}

describe('NodePanel', () => {
  it('renders claim, badges and provenance', () => {
    const { getByText } = render(
      <NodePanel node={graphNode()} onClose={() => {}} onVerdiep={() => {}} />,
    );
    expect(
      getByText('De jeugdzorgkosten stijgen sneller dan het landelijk gemiddelde'),
    ).toBeTruthy();
    expect(getByText('Bevinding')).toBeTruthy();
    expect(getByText('vertrouwen: hoog')).toBeTruthy();
    expect(getByText('onderbouwd')).toBeTruthy();
    expect(getByText('door budget-agent')).toBeTruthy();
  });

  it("renders the node's evidence blocks via the existing BlockRenderer", () => {
    const { getByText } = render(
      <NodePanel node={graphNode()} onClose={() => {}} onVerdiep={() => {}} />,
    );
    // heading block
    expect(getByText('Kostengroei 2020-2026')).toBeTruthy();
    // prose block (markdown bold split into its own element)
    expect(getByText('42%')).toBeTruthy();
    // kpi block
    expect(getByText('Groei')).toBeTruthy();
  });

  it('renders the citations via the existing ReferencesList', () => {
    const { getByText } = render(
      <NodePanel node={graphNode()} onClose={() => {}} onVerdiep={() => {}} />,
    );
    expect(getByText('Geraadpleegde Bronnen')).toBeTruthy();
    expect(getByText(/Iv3 taakveld 6\.72/)).toBeTruthy();
  });

  it('shows a friendly empty state when a node carries no blocks', () => {
    const { getByText } = render(
      <NodePanel
        node={graphNode({ node_type: 'hypothesis', blocks: [], citations: [] })}
        onClose={() => {}}
        onVerdiep={() => {}}
      />,
    );
    expect(getByText('Geen onderbouwende blokken bij deze node.')).toBeTruthy();
  });

  it('"Verdiep dit" hands the full node to the callback', () => {
    const onVerdiep = vi.fn();
    const node = graphNode();
    const { getByText } = render(
      <NodePanel node={node} onClose={() => {}} onVerdiep={onVerdiep} />,
    );
    fireEvent.click(getByText('Verdiep dit'));
    expect(onVerdiep).toHaveBeenCalledWith(node);
  });

  it('an already-scoped node shows the remove label and still toggles via onVerdiep', () => {
    const onVerdiep = vi.fn();
    const node = graphNode();
    const { getByText, queryByText } = render(
      <NodePanel node={node} onClose={() => {}} onVerdiep={onVerdiep} isScoped />,
    );
    expect(queryByText('Verdiep dit')).toBeNull();
    fireEvent.click(getByText('Verwijder uit verdieping'));
    expect(onVerdiep).toHaveBeenCalledWith(node);
  });

  it('a full scope disables adding an unscoped node (max 5)', () => {
    const { getByText } = render(
      <NodePanel node={graphNode()} onClose={() => {}} onVerdiep={() => {}} scopeFull />,
    );
    expect((getByText('Verdiep dit') as HTMLButtonElement).disabled).toBe(true);
    expect(getByText('Maximaal 5 nodes per verdieping.')).toBeTruthy();
  });

  it('a full scope still allows REMOVING a node that is in it', () => {
    const onVerdiep = vi.fn();
    const node = graphNode();
    const { getByText } = render(
      <NodePanel node={node} onClose={() => {}} onVerdiep={onVerdiep} isScoped scopeFull />,
    );
    const button = getByText('Verwijder uit verdieping') as HTMLButtonElement;
    expect(button.disabled).toBe(false);
    fireEvent.click(button);
    expect(onVerdiep).toHaveBeenCalledWith(node);
  });

  it('close button fires onClose', () => {
    const onClose = vi.fn();
    const { getByLabelText } = render(
      <NodePanel node={graphNode()} onClose={onClose} onVerdiep={() => {}} />,
    );
    fireEvent.click(getByLabelText('Sluit detailpaneel'));
    expect(onClose).toHaveBeenCalled();
  });
});
