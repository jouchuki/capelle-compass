/**
 * Live finding-graph canvas (@xyflow/react + dagre auto-layout).
 *
 * Rendering rules:
 *   - node colour keyed on `node_type` (bevinding / context / hypothese /
 *     verificatie all visually distinct), confidence badge on every card;
 *   - `status === 'pruned'` nodes stay visible but render DIMMED (reduced
 *     opacity + dashed border) — hiding them would silently rewrite the
 *     investigation's history;
 *   - edges carry their `edge_type` as label; `tensions_with` is red and
 *     dashed so contradictions jump out;
 *   - edges are CLICKABLE (they carry a `rationale` the host shows in the
 *     EdgePanel): a generous `interactionWidth` widens the invisible hit
 *     area, and a hover/selected stroke (see index.css + inline style)
 *     advertises the affordance; selecting an edge and selecting a node are
 *     mutually exclusive — the host clears one when the other is picked;
 *   - xyflow diffs nodes/edges by id, so live `graph_delta` updates extend
 *     the canvas without a full re-mount;
 *   - node cards carry xyflow's `nopan` class. Without it a mousedown on a
 *     non-draggable node starts a d3-zoom PAN gesture, and >1px of mouse
 *     drift before mouseup makes d3-zoom suppress the resulting click
 *     (stopImmediatePropagation) — onNodeClick never fires and the user
 *     can't switch the selection to another node;
 *   - nodes scoped for a follow-up ("Verdiep dit") render a persistent
 *     amber ring + badge so the user can see what the next question covers.
 */

import { useCallback, useMemo } from 'react';
import {
  Background,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
} from '@xyflow/react';
import type { Edge, Node, NodeProps, NodeTypes } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import type {
  Confidence,
  FindingGraph,
  GraphEdge,
  GraphEdgeType,
  GraphNode,
  GraphNodeType,
} from '../../types';
import { GRAPH_NODE_HEIGHT, GRAPH_NODE_WIDTH, layoutPositions } from './layout';

// ─── Visual vocabulary ────────────────────────────────────────────────────

export const NODE_TYPE_LABEL: Record<GraphNodeType, string> = {
  finding: 'Bevinding',
  context: 'Context',
  hypothesis: 'Hypothese',
  verification: 'Verificatie',
};

const NODE_TYPE_CARD: Record<GraphNodeType, string> = {
  finding: 'bg-blue-50 border-blue-300',
  context: 'bg-slate-50 border-slate-300',
  hypothesis: 'bg-amber-50 border-amber-300',
  verification: 'bg-emerald-50 border-emerald-300',
};

const NODE_TYPE_EYEBROW: Record<GraphNodeType, string> = {
  finding: 'text-blue-600',
  context: 'text-slate-500',
  hypothesis: 'text-amber-600',
  verification: 'text-emerald-600',
};

export const CONFIDENCE_LABEL: Record<Confidence, string> = {
  low: 'laag',
  medium: 'middel',
  high: 'hoog',
};

const CONFIDENCE_BADGE: Record<Confidence, string> = {
  low: 'bg-slate-100 text-slate-500',
  medium: 'bg-amber-100 text-amber-700',
  high: 'bg-emerald-100 text-emerald-700',
};

export const EDGE_TYPE_LABEL: Record<GraphEdgeType, string> = {
  causes: 'veroorzaakt',
  explains: 'verklaart',
  controls: 'beheerst',
  tensions_with: 'spanning',
  depends_on: 'hangt af van',
  decomposes_into: 'valt uiteen in',
};

const DEFAULT_EDGE_COLOR = '#94a3b8'; // slate-400
const TENSION_EDGE_COLOR = '#dc2626'; // red-600
const TENSION_DASH_PATTERN = '6 4';

/**
 * Invisible click-target width (px) around each edge path. The visible
 * stroke is 1-2px — without this, hitting an edge demands pixel-perfect
 * aim and the rationale behind it stays effectively undiscoverable.
 */
const EDGE_INTERACTION_WIDTH = 12;

/** Stroke width (px) of the currently selected edge (default is ~1px). */
const SELECTED_EDGE_STROKE_WIDTH = 2.5;

/**
 * Tolerance (px) between mousedown and mouseup before d3-zoom treats a
 * background click as a pan and suppresses the click event. The default of
 * 1px makes "click background to deselect" fail on the slightest hand
 * wobble; 4px matches typical OS double-click slop.
 */
const PANE_CLICK_DISTANCE = 4;

// ─── Custom node card ─────────────────────────────────────────────────────

type FindingFlowNode = Node<{ graphNode: GraphNode; scoped: boolean }, 'finding'>;

function FindingNodeCard({ data, selected }: NodeProps<FindingFlowNode>) {
  const node = data.graphNode;
  const pruned = node.status === 'pruned';
  return (
    <div
      style={{ width: GRAPH_NODE_WIDTH, minHeight: GRAPH_NODE_HEIGHT }}
      // `nopan` keeps d3-zoom's pan gesture off the card so a slightly
      // sloppy click still reaches onNodeClick (see module docstring).
      className={[
        'nopan rounded-xl border px-4 py-3 shadow-sm transition-opacity relative',
        NODE_TYPE_CARD[node.node_type],
        pruned ? 'opacity-40 border-dashed' : '',
        selected ? 'ring-2 ring-blue-400' : data.scoped ? 'ring-2 ring-amber-400' : '',
      ].join(' ')}
      data-testid={`graph-node-${node.id}`}
    >
      {data.scoped ? (
        <span
          className="absolute -top-2 -right-2 rounded-full bg-amber-400 text-white text-[9px] font-bold uppercase tracking-wider px-2 py-0.5 shadow-sm"
          data-testid={`graph-node-scoped-${node.id}`}
        >
          verdieping
        </span>
      ) : null}
      <Handle type="target" position={Position.Top} className="!bg-slate-300" />
      <div className="flex items-center justify-between gap-2 mb-1.5">
        <span
          className={`text-[10px] font-bold uppercase tracking-wider ${NODE_TYPE_EYEBROW[node.node_type]}`}
        >
          {NODE_TYPE_LABEL[node.node_type]}
        </span>
        <span
          className={`text-[10px] font-bold px-1.5 py-0.5 rounded-full ${CONFIDENCE_BADGE[node.confidence]}`}
        >
          {CONFIDENCE_LABEL[node.confidence]}
        </span>
      </div>
      <p className="text-xs font-medium text-slate-800 leading-snug line-clamp-3">
        {node.claim}
      </p>
      {pruned ? (
        <span className="mt-1 inline-block text-[10px] font-bold uppercase tracking-wider text-slate-400">
          gesnoeid
        </span>
      ) : null}
      <Handle type="source" position={Position.Bottom} className="!bg-slate-300" />
    </div>
  );
}

const NODE_TYPES: NodeTypes = { finding: FindingNodeCard };

/** Flow edge carrying its source GraphEdge so click handlers can hand the
 *  full domain object (incl. rationale) back to the host. */
type FindingFlowEdge = Edge<{ graphEdge: GraphEdge }>;

// ─── Canvas ───────────────────────────────────────────────────────────────

interface GraphViewProps {
  graph: FindingGraph;
  selectedNodeId: string | null;
  /** Currently selected edge (mutually exclusive with the node selection). */
  selectedEdgeId?: string | null;
  /** Nodes currently scoped for a follow-up — rendered with a persistent cue. */
  scopedNodeIds?: readonly string[];
  onSelectNode: (node: GraphNode) => void;
  /** Edge click — the host opens the EdgePanel (and deselects any node). */
  onSelectEdge?: (edge: GraphEdge) => void;
  /** Background (pane) click — the host clears the selection / closes the panel. */
  onClearSelection?: () => void;
}

export default function GraphView({
  graph,
  selectedNodeId,
  selectedEdgeId = null,
  scopedNodeIds,
  onSelectNode,
  onSelectEdge,
  onClearSelection,
}: GraphViewProps) {
  const flowNodes = useMemo<FindingFlowNode[]>(() => {
    const positions = layoutPositions(graph.nodes, graph.edges);
    const scoped = new Set(scopedNodeIds ?? []);
    return graph.nodes.map((node) => ({
      id: node.id,
      type: 'finding' as const,
      position: positions.get(node.id) ?? { x: 0, y: 0 },
      selected: node.id === selectedNodeId,
      data: { graphNode: node, scoped: scoped.has(node.id) },
    }));
  }, [graph.nodes, graph.edges, selectedNodeId, scopedNodeIds]);

  const flowEdges = useMemo<FindingFlowEdge[]>(() => {
    return graph.edges.map((edge) => {
      const isTension = edge.edge_type === 'tensions_with';
      const isSelected = edge.id === selectedEdgeId;
      const color = isTension ? TENSION_EDGE_COLOR : DEFAULT_EDGE_COLOR;
      return {
        id: edge.id,
        source: edge.source_id,
        target: edge.target_id,
        label: EDGE_TYPE_LABEL[edge.edge_type],
        animated: isTension,
        selected: isSelected,
        // Generous invisible hit area so the rationale is discoverable.
        interactionWidth: EDGE_INTERACTION_WIDTH,
        data: { graphEdge: edge },
        style: {
          stroke: color,
          ...(isSelected ? { strokeWidth: SELECTED_EDGE_STROKE_WIDTH } : {}),
          ...(isTension ? { strokeDasharray: TENSION_DASH_PATTERN } : {}),
        },
        labelStyle: { fontSize: 10, fontWeight: 700, fill: isTension ? color : '#64748b' },
        labelBgStyle: { fill: '#ffffff', fillOpacity: 0.85 },
        markerEnd: { type: MarkerType.ArrowClosed, color },
      };
    });
  }, [graph.edges, selectedEdgeId]);

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, flowNode: FindingFlowNode) => {
      onSelectNode(flowNode.data.graphNode);
    },
    [onSelectNode],
  );

  const handleEdgeClick = useCallback(
    (_event: React.MouseEvent, flowEdge: FindingFlowEdge) => {
      const graphEdge = flowEdge.data?.graphEdge;
      if (graphEdge) onSelectEdge?.(graphEdge);
    },
    [onSelectEdge],
  );

  const handlePaneClick = useCallback(() => {
    onClearSelection?.();
  }, [onClearSelection]);

  return (
    <div className="h-full w-full" data-testid="graph-view">
      <ReactFlow
        nodes={flowNodes}
        edges={flowEdges}
        nodeTypes={NODE_TYPES}
        onNodeClick={handleNodeClick}
        onEdgeClick={handleEdgeClick}
        onPaneClick={handlePaneClick}
        paneClickDistance={PANE_CLICK_DISTANCE}
        fitView
        minZoom={0.2}
        nodesDraggable={false}
        nodesConnectable={false}
        deleteKeyCode={null}
      >
        <Background gap={24} color="#e2e8f0" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
