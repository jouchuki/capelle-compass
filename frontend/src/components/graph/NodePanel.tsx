/**
 * Side panel for a selected finding-graph node.
 *
 * Shows the claim as title, type/confidence/status badges, the node's
 * evidence `blocks` rendered via the EXISTING BlockRenderer (same component
 * the report artifact uses), the citation list via ReferencesList, and the
 * "Verdiep dit" button that ADDS the node to the composer's follow-up scope.
 * The button toggles: when the node is already scoped it reads "Verwijder
 * uit verdieping" and clicking it removes the node again. When the scope is
 * full (MAX_FOCUS_NODES) and this node isn't part of it, adding is disabled.
 */

import { X } from 'lucide-react';
import type { CitationRef, GraphNode, GraphNodeStatus } from '../../types';
import BlockRenderer from '../artifact/BlockRenderer';
import type { CitationLookup } from '../artifact/BlockRenderer';
import ReferencesList from '../artifact/ReferencesList';
import IconButton from '../ui/IconButton';
import { CONFIDENCE_LABEL, NODE_TYPE_LABEL } from './GraphView';

export const NODE_STATUS_LABEL: Record<GraphNodeStatus, string> = {
  proposed: 'voorgesteld',
  supported: 'onderbouwd',
  verified: 'geverifieerd',
  pruned: 'gesnoeid',
};

interface NodePanelProps {
  node: GraphNode;
  onClose: () => void;
  /**
   * "Verdiep dit" — TOGGLE this node in the composer's follow-up scope.
   * The host adds the node when it isn't scoped yet and removes it when it
   * is (the button label tracks {@link NodePanelProps.isScoped}).
   */
  onVerdiep: (node: GraphNode) => void;
  /** Whether this node is already part of the follow-up scope. */
  isScoped?: boolean;
  /** Whether the scope is at MAX_FOCUS_NODES — blocks adding (not removing). */
  scopeFull?: boolean;
}

export default function NodePanel({
  node,
  onClose,
  onVerdiep,
  isScoped = false,
  scopeFull = false,
}: NodePanelProps) {
  const citationList: CitationRef[] = node.citations ?? [];
  const citationMap: CitationLookup = new Map(
    citationList.map((c, i) => [c.id, { ref: c, num: i + 1 }]),
  );
  const blocks = node.blocks ?? [];

  return (
    <aside
      className="h-full w-full bg-white border-l border-slate-100 flex flex-col overflow-hidden"
      data-testid="node-panel"
      aria-label="Details van bevinding"
    >
      <div className="flex items-start justify-between gap-3 px-5 py-4 border-b border-slate-100 flex-shrink-0">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="text-[10px] font-bold uppercase tracking-wider text-blue-600">
              {NODE_TYPE_LABEL[node.node_type]}
            </span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600">
              vertrouwen: {CONFIDENCE_LABEL[node.confidence]}
            </span>
            <span className="text-[10px] font-bold px-1.5 py-0.5 rounded-full bg-slate-100 text-slate-600">
              {NODE_STATUS_LABEL[node.status]}
            </span>
          </div>
          <h2 className="text-sm font-bold text-slate-900 leading-snug">{node.claim}</h2>
          {node.agent_label ? (
            <p className="mt-1 text-[11px] text-slate-400">door {node.agent_label}</p>
          ) : null}
        </div>
        <IconButton label="Sluit detailpaneel" onClick={onClose}>
          <X className="w-4 h-4" />
        </IconButton>
      </div>

      <div className="flex-1 overflow-y-auto px-5 py-5 custom-scrollbar">
        {blocks.length > 0 ? (
          blocks.map((block, idx) => (
            <BlockRenderer key={idx} block={block} citations={citationMap} />
          ))
        ) : (
          <p className="text-xs text-slate-400">Geen onderbouwende blokken bij deze node.</p>
        )}
        {citationList.length > 0 ? <ReferencesList citations={citationList} /> : null}
      </div>

      <div className="px-5 py-4 border-t border-slate-100 flex-shrink-0">
        <button
          type="button"
          onClick={() => onVerdiep(node)}
          disabled={!isScoped && scopeFull}
          className={`w-full text-sm font-semibold py-2.5 rounded-xl transition-colors active:scale-[0.99] disabled:opacity-40 disabled:cursor-not-allowed ${
            isScoped
              ? 'bg-amber-500 hover:bg-amber-600 text-white'
              : 'bg-blue-600 hover:bg-blue-700 text-white'
          }`}
        >
          {isScoped ? 'Verwijder uit verdieping' : 'Verdiep dit'}
        </button>
        {!isScoped && scopeFull ? (
          <p className="mt-2 text-[11px] text-slate-400 text-center">
            Maximaal 5 nodes per verdieping.
          </p>
        ) : null}
      </div>
    </aside>
  );
}
