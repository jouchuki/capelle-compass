/**
 * Dagre-based auto-layout for the finding-graph.
 *
 * Pure: GraphNode/GraphEdge in, `id -> {x, y}` out. Dagre's layered layout
 * is deterministic for a given input, so nodes added by live deltas extend
 * the picture without scrambling what's already on screen.
 */

import dagre from '@dagrejs/dagre';
import type { GraphEdge, GraphNode } from '../../types';

/** Card footprint used both for layout spacing and the node component CSS. */
export const GRAPH_NODE_WIDTH = 248;
export const GRAPH_NODE_HEIGHT = 96;

const NODE_SEPARATION = 48;
const RANK_SEPARATION = 80;

export interface NodePosition {
  x: number;
  y: number;
}

export function layoutPositions(
  nodes: GraphNode[],
  edges: GraphEdge[],
): Map<string, NodePosition> {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'TB', nodesep: NODE_SEPARATION, ranksep: RANK_SEPARATION });
  g.setDefaultEdgeLabel(() => ({}));

  for (const node of nodes) {
    g.setNode(node.id, { width: GRAPH_NODE_WIDTH, height: GRAPH_NODE_HEIGHT });
  }
  const known = new Set(nodes.map((n) => n.id));
  for (const edge of edges) {
    // Defensive: the merge layer only promotes edges whose endpoints exist,
    // but layout must never throw on a stray reference.
    if (known.has(edge.source_id) && known.has(edge.target_id)) {
      g.setEdge(edge.source_id, edge.target_id);
    }
  }

  dagre.layout(g);

  const positions = new Map<string, NodePosition>();
  for (const node of nodes) {
    const placed = g.node(node.id);
    if (!placed) continue;
    // Dagre returns center coordinates; xyflow expects top-left.
    positions.set(node.id, {
      x: placed.x - GRAPH_NODE_WIDTH / 2,
      y: placed.y - GRAPH_NODE_HEIGHT / 2,
    });
  }
  return positions;
}
