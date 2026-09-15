/**
 * Side panel for a selected finding-graph EDGE.
 *
 * Edges carry substantive content: `rationale` is a 2-4 sentence Dutch
 * analytical explanation of the mechanism behind the relation. This panel
 * makes that visible — the canvas itself only shows the edge-type label.
 *
 * Layout mirrors NodePanel (same 380px slot in ArtifactPanel; selecting an
 * edge deselects any node and vice versa):
 *   - header: the edge type with its Dutch gloss (reuses EDGE_TYPE_LABEL);
 *     `causes` edges get a "counterfactual vereist" eyebrow because the
 *     prompt mandates counterfactual support for causal claims;
 *   - VAN → NAAR endpoint cards showing the source/target claims
 *     (truncated); both are buttons that re-point the selection at that
 *     node, so the user can hop from a relation to its endpoints;
 *   - body: the full rationale, or a muted "Geen toelichting vastgelegd."
 *     fallback when the edge predates the substance mandate.
 */

import { ArrowDown, X } from 'lucide-react';
import type { GraphEdge, GraphNode } from '../../types';
import IconButton from '../ui/IconButton';
import { EDGE_TYPE_LABEL } from './GraphView';

/** Max characters of an endpoint claim shown on the VAN/NAAR cards. */
const CLAIM_PREVIEW_MAX_CHARS = 80;

/** Truncate an endpoint claim for the VAN/NAAR cards (ellipsis past the cap). */
export function truncateClaim(claim: string): string {
  if (claim.length <= CLAIM_PREVIEW_MAX_CHARS) return claim;
  return `${claim.slice(0, CLAIM_PREVIEW_MAX_CHARS).trimEnd()}…`;
}

interface EndpointCardProps {
  /** "VAN" (source) or "NAAR" (target). */
  role: 'VAN' | 'NAAR';
  node: GraphNode | null;
  testId: string;
  onSelectNode: (node: GraphNode) => void;
}

/** One clickable endpoint (source or target) of the selected edge. */
function EndpointCard({ role, node, testId, onSelectNode }: EndpointCardProps) {
  if (!node) {
    // Defensive: a delta stream can momentarily reference a node we have
    // not received yet. Render a muted placeholder instead of crashing.
    return (
      <div
        className="w-full rounded-xl border border-dashed border-slate-200 px-3 py-2.5 text-left"
        data-testid={testId}
      >
        <span className="block text-[10px] font-bold uppercase tracking-wider text-slate-400">
          {role}
        </span>
        <span className="block mt-0.5 text-xs text-slate-400">Node niet gevonden.</span>
      </div>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onSelectNode(node)}
      className="w-full rounded-xl border border-slate-200 bg-slate-50 px-3 py-2.5 text-left transition-colors hover:border-blue-300 hover:bg-blue-50 active:scale-[0.99]"
      data-testid={testId}
    >
      <span className="block text-[10px] font-bold uppercase tracking-wider text-slate-400">
        {role}
      </span>
      <span className="block mt-0.5 text-xs font-medium text-slate-800 leading-snug">
        {truncateClaim(node.claim)}
      </span>
    </button>
  );
}

interface EdgePanelProps {
  edge: GraphEdge;
  /** Resolved endpoints; null when the node is (not yet) in the graph. */
  sourceNode: GraphNode | null;
  targetNode: GraphNode | null;
  onClose: () => void;
  /** Clicking VAN/NAAR re-points the selection at that node's NodePanel. */
  onSelectNode: (node: GraphNode) => void;
}

export default function EdgePanel({
  edge,
  sourceNode,
  targetNode,
  onClose,
  onSelectNode,
}: EdgePanelProps) {
  const rationale = edge.rationale.trim();

  return (
    <aside
      className="h-full w-full bg-white border-l border-slate-100 flex flex-col overflow-hidden"
      data-testid="edge-panel"
      aria-label="Details van relatie"
    >
      <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-100 flex-shrink-0">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-slate-400">
              Relatie
            </span>
            {edge.edge_type === 'causes' ? (
              <span
                className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded-full bg-amber-100 text-amber-700"
                data-testid="edge-panel-counterfactual"
              >
                counterfactual vereist
              </span>
            ) : null}
          </div>
          <h2 className="text-sm font-bold text-slate-900 leading-snug">
            {EDGE_TYPE_LABEL[edge.edge_type]}
          </h2>
        </div>
        <IconButton label="Sluit relatiepaneel" onClick={onClose}>
          <X className="w-4 h-4" />
        </IconButton>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5 custom-scrollbar">
        <div className="flex flex-col items-center gap-1.5">
          <EndpointCard
            role="VAN"
            node={sourceNode}
            testId="edge-panel-source"
            onSelectNode={onSelectNode}
          />
          <ArrowDown className="w-4 h-4 text-slate-300 flex-shrink-0" aria-hidden="true" />
          <EndpointCard
            role="NAAR"
            node={targetNode}
            testId="edge-panel-target"
            onSelectNode={onSelectNode}
          />
        </div>

        <h3 className="mt-5 mb-1.5 text-[10px] font-bold uppercase tracking-wider text-slate-400">
          Toelichting
        </h3>
        {rationale ? (
          <p className="text-[13px] text-slate-700 leading-relaxed" data-testid="edge-rationale">
            {rationale}
          </p>
        ) : (
          <p className="text-xs text-slate-400" data-testid="edge-rationale-empty">
            Geen toelichting vastgelegd.
          </p>
        )}
      </div>
    </aside>
  );
}
