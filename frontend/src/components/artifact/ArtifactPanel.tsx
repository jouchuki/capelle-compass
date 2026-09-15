/**
 * Artifact-area host: `[Graaf] [Rapport]` toggle around the existing report.
 *
 * Backward compatibility is structural, not stylistic: when the session has
 * NO finding-graph this component returns the EXISTING `ReportArtifact`
 * directly — no wrapper element, no tab bar — so legacy sessions render
 * byte-for-byte as today (regression-tested by innerHTML equality).
 *
 * With a non-empty graph it renders the tab bar; `Rapport` is the unchanged
 * `ReportArtifact`, `Graaf` is the live GraphView + detail-panel pair. The
 * detail slot hosts EITHER the NodePanel (node selected) OR the EdgePanel
 * (edge selected) — selecting one kind clears the other, so the slot never
 * shows both. The Rapport tab is disabled while the session has no analysis
 * yet (live run whose first report hasn't landed).
 */

import { useState } from 'react';
import { Waypoints, X } from 'lucide-react';
import type { AnalysisResult, FindingGraph, GraphEdge, GraphNode } from '../../types';
import { isGraphNonEmpty } from '../../utils/graphMerge';
import EdgePanel from '../graph/EdgePanel';
import GraphView from '../graph/GraphView';
import NodePanel from '../graph/NodePanel';
import IconButton from '../ui/IconButton';
import ReportArtifact from './ReportArtifact';

export type ArtifactTab = 'graaf' | 'rapport';

interface ArtifactPanelProps {
  /** Active analysis, or null when the session has no report yet. */
  analysis: AnalysisResult | null;
  sessionId: string | null;
  totalAnalyses: number;
  currentIndex: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onAskFollowUp: (question: string) => void;
  /** Session finding-graph; null/empty hides every graph affordance. */
  graph: FindingGraph | null;
  tab: ArtifactTab;
  onTabChange: (tab: ArtifactTab) => void;
  /** "Verdiep dit" from the node panel — TOGGLES the node in the follow-up scope. */
  onVerdiep: (node: GraphNode) => void;
  /** Ids of nodes currently in the follow-up scope (chip row in the chat). */
  scopedNodeIds?: readonly string[];
  /** Whether the scope is at its MAX_FOCUS_NODES cap. */
  scopeFull?: boolean;
}

const TAB_BASE_CLASS =
  'px-3 py-1.5 rounded-lg text-xs font-bold uppercase tracking-wider transition-colors';
const TAB_ACTIVE_CLASS = 'bg-slate-900 text-white';
const TAB_IDLE_CLASS = 'text-slate-500 hover:text-slate-900 hover:bg-slate-100';

export default function ArtifactPanel({
  analysis,
  sessionId,
  totalAnalyses,
  currentIndex,
  onPrev,
  onNext,
  onClose,
  onAskFollowUp,
  graph,
  tab,
  onTabChange,
  onVerdiep,
  scopedNodeIds,
  scopeFull = false,
}: ArtifactPanelProps) {
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [selectedEdgeId, setSelectedEdgeId] = useState<string | null>(null);

  // Node and edge selection are mutually exclusive: the detail slot shows
  // one panel at a time, so picking one kind always clears the other.
  const selectNode = (node: GraphNode) => {
    setSelectedEdgeId(null);
    setSelectedNodeId(node.id);
  };
  const selectEdge = (edge: GraphEdge) => {
    setSelectedNodeId(null);
    setSelectedEdgeId(edge.id);
  };
  const clearSelection = () => {
    setSelectedNodeId(null);
    setSelectedEdgeId(null);
  };

  // Legacy path: no graph → the artifact area is EXACTLY today's tree.
  if (!isGraphNonEmpty(graph)) {
    if (!analysis) return null;
    return (
      <ReportArtifact
        analysis={analysis}
        sessionId={sessionId}
        totalAnalyses={totalAnalyses}
        currentIndex={currentIndex}
        onPrev={onPrev}
        onNext={onNext}
        onClose={onClose}
        onAskFollowUp={onAskFollowUp}
      />
    );
  }

  // No report yet (live first run) → only the graph is showable.
  const effectiveTab: ArtifactTab = analysis ? tab : 'graaf';
  // Derive the panel node/edge from the graph so live deltas (status flips,
  // added evidence) refresh an open panel instead of showing a stale copy.
  const selectedNode = selectedNodeId
    ? graph.nodes.find((n) => n.id === selectedNodeId) ?? null
    : null;
  const selectedEdge = selectedEdgeId
    ? graph.edges.find((e) => e.id === selectedEdgeId) ?? null
    : null;

  return (
    <div className="h-full w-full flex flex-col overflow-hidden bg-[#FAFAFA]">
      <div
        className="flex items-center gap-1 px-4 py-2 bg-white border-b border-slate-100 flex-shrink-0"
        data-testid="artifact-tabs"
        role="tablist"
      >
        <button
          type="button"
          role="tab"
          aria-selected={effectiveTab === 'graaf'}
          onClick={() => onTabChange('graaf')}
          className={`${TAB_BASE_CLASS} ${effectiveTab === 'graaf' ? TAB_ACTIVE_CLASS : TAB_IDLE_CLASS}`}
        >
          Graaf
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={effectiveTab === 'rapport'}
          disabled={!analysis}
          onClick={() => onTabChange('rapport')}
          className={`${TAB_BASE_CLASS} ${
            effectiveTab === 'rapport' ? TAB_ACTIVE_CLASS : TAB_IDLE_CLASS
          } disabled:opacity-30 disabled:cursor-not-allowed`}
        >
          Rapport
        </button>
      </div>

      {effectiveTab === 'rapport' && analysis ? (
        <div className="flex-1 overflow-hidden">
          <ReportArtifact
            analysis={analysis}
            sessionId={sessionId}
            totalAnalyses={totalAnalyses}
            currentIndex={currentIndex}
            onPrev={onPrev}
            onNext={onNext}
            onClose={onClose}
            onAskFollowUp={onAskFollowUp}
          />
        </div>
      ) : (
        <div className="flex-1 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between px-8 py-5 bg-white border-b border-slate-100 flex-shrink-0">
            <div className="flex items-center gap-3 min-w-0">
              <Waypoints className="w-5 h-5 text-blue-600 flex-shrink-0" />
              <span className="text-xs font-bold uppercase tracking-wider text-slate-900">
                Onderzoeksgraaf
              </span>
              <span className="text-[11px] text-slate-400 tabular-nums">
                {graph.nodes.length} nodes · {graph.edges.length} relaties
              </span>
            </div>
            <IconButton label="Sluit graaf" onClick={onClose}>
              <X className="w-4 h-4" />
            </IconButton>
          </div>

          {graph.summary ? (
            <div
              className="px-8 py-3 bg-blue-50/60 border-b border-blue-100 text-[13px] text-slate-700 leading-relaxed flex-shrink-0"
              data-testid="graph-summary"
            >
              {graph.summary}
            </div>
          ) : null}

          <div className="flex-1 flex overflow-hidden">
            <div className="flex-1 min-w-0">
              <GraphView
                graph={graph}
                selectedNodeId={selectedNodeId}
                selectedEdgeId={selectedEdgeId}
                scopedNodeIds={scopedNodeIds}
                onSelectNode={selectNode}
                onSelectEdge={selectEdge}
                onClearSelection={clearSelection}
              />
            </div>
            {selectedNode ? (
              <div className="w-[380px] flex-shrink-0">
                <NodePanel
                  node={selectedNode}
                  onClose={clearSelection}
                  onVerdiep={onVerdiep}
                  isScoped={(scopedNodeIds ?? []).includes(selectedNode.id)}
                  scopeFull={scopeFull}
                />
              </div>
            ) : selectedEdge ? (
              <div className="w-[380px] flex-shrink-0">
                <EdgePanel
                  edge={selectedEdge}
                  sourceNode={graph.nodes.find((n) => n.id === selectedEdge.source_id) ?? null}
                  targetNode={graph.nodes.find((n) => n.id === selectedEdge.target_id) ?? null}
                  onClose={clearSelection}
                  onSelectNode={selectNode}
                />
              </div>
            ) : null}
          </div>
        </div>
      )}
    </div>
  );
}
