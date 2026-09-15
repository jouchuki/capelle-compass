/**
 * Pure merge logic for live `graph_delta` WebSocket events.
 *
 * Kept free of React so the reducer behaviour is unit-testable in isolation
 * (same pattern as `chatReducer`). The chat reducer delegates here.
 *
 * Merge rules (mirror the server's ordering guarantees — or lack thereof):
 *   - node    → upsert by `id` (a re-broadcast or a status/confidence update
 *               replaces the stored node).
 *   - edge    → appended only when BOTH endpoints already exist as nodes;
 *               otherwise parked in `pendingEdges` and retried after every
 *               subsequent node arrival. WS frames can outrun each other, so
 *               an edge may legitimately arrive before its endpoints.
 *   - summary → replaced wholesale (the lead re-derives it as the graph grows).
 *   - deltas for a session other than the active one are ignored.
 */

import type {
  FindingGraph,
  GraphDeltaEvent,
  GraphEdge,
  GraphNode,
} from '../types';

/** Graph + the edges still waiting for their endpoints to arrive. */
export interface GraphSyncState {
  graph: FindingGraph | null;
  pendingEdges: GraphEdge[];
}

export const EMPTY_GRAPH_SYNC: GraphSyncState = {
  graph: null,
  pendingEdges: [],
};

/** True when the session has a graph worth showing (at least one node). */
export function isGraphNonEmpty(graph: FindingGraph | null): graph is FindingGraph {
  return graph !== null && graph.nodes.length > 0;
}

function emptyGraphFor(sessionId: string): FindingGraph {
  return { session_id: sessionId, nodes: [], edges: [], summary: null, version: 0 };
}

function upsertNode(nodes: GraphNode[], node: GraphNode): GraphNode[] {
  const idx = nodes.findIndex((n) => n.id === node.id);
  if (idx === -1) return [...nodes, node];
  return nodes.map((n, i) => (i === idx ? node : n));
}

function endpointsExist(edge: GraphEdge, nodes: GraphNode[]): boolean {
  const ids = new Set(nodes.map((n) => n.id));
  return ids.has(edge.source_id) && ids.has(edge.target_id);
}

/**
 * Apply one `graph_delta` event to the current sync state.
 *
 * Pure: returns the SAME state object when the event is a no-op (other
 * session, duplicate edge), so reducers can bail out without re-rendering.
 */
export function applyGraphDelta(
  state: GraphSyncState,
  event: GraphDeltaEvent,
  activeSessionId: string | null,
): GraphSyncState {
  if (!activeSessionId || event.session_id !== activeSessionId) {
    return state;
  }

  const graph = state.graph ?? emptyGraphFor(event.session_id);

  switch (event.kind) {
    case 'node': {
      const node = event.data as GraphNode;
      const nodes = upsertNode(graph.nodes, node);

      // Retry the buffer: any parked edge whose endpoints both exist now is
      // promoted into the graph (skipping ids the graph already carries).
      const knownEdgeIds = new Set(graph.edges.map((e) => e.id));
      const promoted: GraphEdge[] = [];
      const stillPending: GraphEdge[] = [];
      for (const edge of state.pendingEdges) {
        if (endpointsExist(edge, nodes) && !knownEdgeIds.has(edge.id)) {
          promoted.push(edge);
        } else {
          stillPending.push(edge);
        }
      }
      return {
        graph: { ...graph, nodes, edges: [...graph.edges, ...promoted] },
        pendingEdges: stillPending,
      };
    }
    case 'edge': {
      const edge = event.data as GraphEdge;
      const isDuplicate =
        graph.edges.some((e) => e.id === edge.id) ||
        state.pendingEdges.some((e) => e.id === edge.id);
      if (isDuplicate) return state;
      if (endpointsExist(edge, graph.nodes)) {
        return {
          graph: { ...graph, edges: [...graph.edges, edge] },
          pendingEdges: state.pendingEdges,
        };
      }
      return { graph: state.graph, pendingEdges: [...state.pendingEdges, edge] };
    }
    case 'summary': {
      const summary = event.data as string;
      return { graph: { ...graph, summary }, pendingEdges: state.pendingEdges };
    }
    default:
      return state;
  }
}
