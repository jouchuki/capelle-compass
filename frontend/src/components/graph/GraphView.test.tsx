import { describe, it, expect, beforeAll, vi } from 'vitest';
import { useState } from 'react';
import { act, fireEvent, render } from '@testing-library/react';
import GraphView from './GraphView';
import type { FindingGraph, GraphEdge, GraphNode } from '../../types';

// ─── jsdom shims for @xyflow/react (per the xyflow testing guide) ─────────
beforeAll(() => {
  if (!('ResizeObserver' in globalThis)) {
    (globalThis as unknown as { ResizeObserver: unknown }).ResizeObserver = class {
      private readonly cb: ResizeObserverCallback;
      constructor(cb: ResizeObserverCallback) {
        this.cb = cb;
      }
      observe(el: Element) {
        // Fire ASYNC: a synchronous callback runs inside the NodeWrapper's
        // child effect, before ReactFlow's own effect has stored `domNode` —
        // updateNodeInternals then drops the measurement, nodes never get
        // handleBounds and EDGES NEVER RENDER. Deferring one tick lets the
        // store initialise first (tests `settle()` past this anyway).
        setTimeout(() => {
          this.cb(
            [{ target: el, contentRect: { width: 800, height: 600 } } as ResizeObserverEntry],
            this as unknown as ResizeObserver,
          );
        }, 0);
      }
      unobserve() {}
      disconnect() {}
    };
  }
  if (!('DOMMatrixReadOnly' in globalThis)) {
    (globalThis as unknown as { DOMMatrixReadOnly: unknown }).DOMMatrixReadOnly = class {
      readonly m22: number = 1;
    };
  }
  Object.defineProperties(HTMLElement.prototype, {
    offsetHeight: { get: () => 600, configurable: true },
    offsetWidth: { get: () => 800, configurable: true },
  });
  (SVGElement.prototype as unknown as { getBBox: () => DOMRect }).getBBox = () =>
    ({ x: 0, y: 0, width: 0, height: 0 }) as DOMRect;
});

function node(id: string, claim: string): GraphNode {
  return {
    id,
    node_type: 'finding',
    claim,
    blocks: [],
    citations: [],
    confidence: 'medium',
    status: 'proposed',
    agent_label: 'demografie',
    message_id: 'm1',
    created_at: '2026-06-10T00:00:00Z',
    trace_id: null,
  };
}

const EDGE: GraphEdge = {
  id: 'e1',
  source_id: 'n1',
  target_id: 'n2',
  edge_type: 'explains',
  rationale: 'Claim een verklaart claim twee via een gedeeld mechanisme.',
  created_at: '2026-06-10T00:00:00Z',
};

const GRAPH: FindingGraph = {
  session_id: 's1',
  nodes: [node('n1', 'claim een'), node('n2', 'claim twee')],
  edges: [EDGE],
  summary: null,
  version: 2,
};

/** Minimal host mirroring ArtifactPanel's selection wiring (node and edge
 *  selection mutually exclusive, pane click clears both). */
function Harness({
  onSelect,
  onSelectEdge,
  scopedNodeIds,
}: {
  onSelect?: (n: GraphNode) => void;
  onSelectEdge?: (e: GraphEdge) => void;
  scopedNodeIds?: string[];
}) {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);
  return (
    <div style={{ width: 800, height: 600 }}>
      <div data-testid="selected-probe">{selectedId ?? 'none'}</div>
      <div data-testid="selected-edge-probe">{selectedEdgeId ?? 'none'}</div>
      <GraphView
        graph={GRAPH}
        selectedNodeId={selectedId}
        selectedEdgeId={selectedEdgeId}
        scopedNodeIds={scopedNodeIds}
        onSelectNode={(n) => {
          setSelectedEdgeId(null);
          setSelectedId(n.id);
          onSelect?.(n);
        }}
        onSelectEdge={(e) => {
          setSelectedId(null);
          setSelectedEdgeId(e.id);
          onSelectEdge?.(e);
        }}
        onClearSelection={() => {
          setSelectedId(null);
          setSelectedEdgeId(null);
        }}
      />
    </div>
  );
}

const settle = () => act(async () => new Promise((r) => setTimeout(r, 30)));

describe('GraphView — selection switching', () => {
  it('clicking node A then node B switches the selection to B', async () => {
    const onSelect = vi.fn();
    const { getByTestId } = render(<Harness onSelect={onSelect} />);
    await settle();

    fireEvent.click(getByTestId('graph-node-n1'));
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('n1');

    fireEvent.click(getByTestId('graph-node-n2'));
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('n2');
    expect(onSelect.mock.calls.map((c) => (c[0] as GraphNode).id)).toEqual(['n1', 'n2']);
  });

  it('clicking the canvas background clears the selection', async () => {
    const { getByTestId, container } = render(<Harness />);
    await settle();

    fireEvent.click(getByTestId('graph-node-n1'));
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('n1');

    const pane = container.querySelector('.react-flow__pane');
    expect(pane).toBeTruthy();
    fireEvent.click(pane!);
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('none');
  });

  it('node cards carry the `nopan` class so d3-zoom cannot swallow slightly-sloppy clicks', async () => {
    // Root-cause regression guard: without `nopan`, a mousedown on a
    // NON-draggable node starts a canvas-pan gesture and >1px of mouse
    // drift makes d3-zoom suppress the click — onNodeClick never fires
    // and the selection can't be switched to another node.
    const { getByTestId } = render(<Harness />);
    await settle();

    expect(getByTestId('graph-node-n1').className).toContain('nopan');
    expect(getByTestId('graph-node-n2').className).toContain('nopan');
  });
});

describe('GraphView — edge selection', () => {
  it('clicking an edge hands the full GraphEdge (incl. rationale) to onSelectEdge', async () => {
    const onSelectEdge = vi.fn();
    const { getByTestId, container } = render(<Harness onSelectEdge={onSelectEdge} />);
    await settle();

    const edge = container.querySelector('.react-flow__edge');
    expect(edge).toBeTruthy();
    fireEvent.click(edge!);
    await settle();

    expect(onSelectEdge).toHaveBeenCalledTimes(1);
    expect(onSelectEdge).toHaveBeenCalledWith(EDGE);
    expect(getByTestId('selected-edge-probe').textContent).toBe('e1');
  });

  it('selecting an edge clears a node selection and vice versa', async () => {
    const { getByTestId, container } = render(<Harness />);
    await settle();

    fireEvent.click(getByTestId('graph-node-n1'));
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('n1');

    fireEvent.click(container.querySelector('.react-flow__edge')!);
    await settle();
    expect(getByTestId('selected-edge-probe').textContent).toBe('e1');
    expect(getByTestId('selected-probe').textContent).toBe('none');

    fireEvent.click(getByTestId('graph-node-n2'));
    await settle();
    expect(getByTestId('selected-probe').textContent).toBe('n2');
    expect(getByTestId('selected-edge-probe').textContent).toBe('none');
  });

  it('edges get a generous interaction width so the click target is forgiving', async () => {
    const { container } = render(<Harness />);
    await settle();

    // xyflow renders `interactionWidth` as the invisible interaction path's
    // stroke-width — this is what makes a 1px line clickable.
    const interactionPath = container.querySelector('.react-flow__edge-interaction');
    expect(interactionPath).toBeTruthy();
    expect(interactionPath!.getAttribute('stroke-width')).toBe('12');
  });
});

describe('GraphView — follow-up scope cue', () => {
  it('scoped nodes render a persistent verdieping badge; others do not', async () => {
    const { getByTestId, queryByTestId } = render(<Harness scopedNodeIds={['n2']} />);
    await settle();

    expect(getByTestId('graph-node-scoped-n2')).toBeTruthy();
    expect(getByTestId('graph-node-scoped-n2').textContent).toBe('verdieping');
    expect(queryByTestId('graph-node-scoped-n1')).toBeNull();
  });
});
