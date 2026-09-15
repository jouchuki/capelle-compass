import { describe, it, expect } from 'vitest';
import {
  applyGraphDelta,
  EMPTY_GRAPH_SYNC,
  isGraphNonEmpty,
} from './graphMerge';
import type { GraphSyncState } from './graphMerge';
import type {
  FindingGraph,
  GraphDeltaEvent,
  GraphEdge,
  GraphNode,
} from '../types';

const SESSION = 's-actief';
const OTHER_SESSION = 's-anders';

function node(id: string, overrides: Partial<GraphNode> = {}): GraphNode {
  return {
    id,
    node_type: 'finding',
    claim: `claim ${id}`,
    blocks: [],
    citations: [],
    confidence: 'medium',
    status: 'proposed',
    agent_label: 'demografie',
    message_id: 'm1',
    created_at: '2026-06-10T00:00:00Z',
    trace_id: null,
    ...overrides,
  };
}

function edge(id: string, source: string, target: string): GraphEdge {
  return {
    id,
    source_id: source,
    target_id: target,
    edge_type: 'explains',
    rationale: 'omdat',
    created_at: '2026-06-10T00:00:00Z',
  };
}

function delta(
  kind: GraphDeltaEvent['kind'],
  data: GraphDeltaEvent['data'],
  sessionId = SESSION,
): GraphDeltaEvent {
  return { type: 'graph_delta', session_id: sessionId, message_id: 'm1', kind, data };
}

describe('applyGraphDelta — node upsert', () => {
  it('inserts a new node, creating the graph when none was loaded yet', () => {
    const next = applyGraphDelta(EMPTY_GRAPH_SYNC, delta('node', node('n1')), SESSION);
    expect(next.graph?.nodes.map((n) => n.id)).toEqual(['n1']);
    expect(next.graph?.session_id).toBe(SESSION);
  });

  it('replaces an existing node by id (status/confidence update)', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(
      state,
      delta('node', node('n1', { status: 'verified', confidence: 'high' })),
      SESSION,
    );
    expect(state.graph?.nodes).toHaveLength(1);
    expect(state.graph?.nodes[0]).toMatchObject({ status: 'verified', confidence: 'high' });
  });

  it('preserves other nodes when one is updated', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('node', node('n2')), SESSION);
    state = applyGraphDelta(state, delta('node', node('n1', { status: 'pruned' })), SESSION);
    expect(state.graph?.nodes.map((n) => n.id)).toEqual(['n1', 'n2']);
    expect(state.graph?.nodes[0]?.status).toBe('pruned');
  });
});

describe('applyGraphDelta — edge-before-node buffering', () => {
  it('parks an edge whose endpoints are not both present yet', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('edge', edge('e1', 'n1', 'n2')), SESSION);
    expect(state.graph?.edges).toEqual([]);
    expect(state.pendingEdges.map((e) => e.id)).toEqual(['e1']);
  });

  it('promotes a parked edge once the missing endpoint arrives', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('edge', edge('e1', 'n1', 'n2')), SESSION);
    state = applyGraphDelta(state, delta('node', node('n2')), SESSION);
    expect(state.graph?.edges.map((e) => e.id)).toEqual(['e1']);
    expect(state.pendingEdges).toEqual([]);
  });

  it('appends an edge immediately when both endpoints exist', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('node', node('n2')), SESSION);
    state = applyGraphDelta(state, delta('edge', edge('e1', 'n1', 'n2')), SESSION);
    expect(state.graph?.edges.map((e) => e.id)).toEqual(['e1']);
    expect(state.pendingEdges).toEqual([]);
  });

  it('dedups a re-broadcast edge id (stored or pending)', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('node', node('n2')), SESSION);
    state = applyGraphDelta(state, delta('edge', edge('e1', 'n1', 'n2')), SESSION);
    const afterDup = applyGraphDelta(state, delta('edge', edge('e1', 'n1', 'n2')), SESSION);
    expect(afterDup).toBe(state); // identity → reducer can bail out
    expect(afterDup.graph?.edges).toHaveLength(1);
  });
});

describe('applyGraphDelta — summary replace', () => {
  it('replaces the summary wholesale', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    state = applyGraphDelta(state, delta('summary', 'eerste samenvatting'), SESSION);
    expect(state.graph?.summary).toBe('eerste samenvatting');
    state = applyGraphDelta(state, delta('summary', 'herziene samenvatting'), SESSION);
    expect(state.graph?.summary).toBe('herziene samenvatting');
    expect(state.graph?.nodes).toHaveLength(1); // nodes untouched
  });
});

describe('applyGraphDelta — session scoping', () => {
  it('ignores deltas for another session (state identity preserved)', () => {
    let state: GraphSyncState = EMPTY_GRAPH_SYNC;
    state = applyGraphDelta(state, delta('node', node('n1')), SESSION);
    const next = applyGraphDelta(
      state,
      delta('node', node('vreemd'), OTHER_SESSION),
      SESSION,
    );
    expect(next).toBe(state);
    expect(next.graph?.nodes.map((n) => n.id)).toEqual(['n1']);
  });

  it('ignores every delta when no session is active', () => {
    const next = applyGraphDelta(EMPTY_GRAPH_SYNC, delta('node', node('n1')), null);
    expect(next).toBe(EMPTY_GRAPH_SYNC);
  });
});

describe('isGraphNonEmpty', () => {
  it('is false for null and for a graph without nodes', () => {
    expect(isGraphNonEmpty(null)).toBe(false);
    const empty: FindingGraph = {
      session_id: SESSION,
      nodes: [],
      edges: [],
      summary: null,
      version: 0,
    };
    expect(isGraphNonEmpty(empty)).toBe(false);
  });

  it('is true once at least one node exists', () => {
    const state = applyGraphDelta(EMPTY_GRAPH_SYNC, delta('node', node('n1')), SESSION);
    expect(isGraphNonEmpty(state.graph)).toBe(true);
  });
});
