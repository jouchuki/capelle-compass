export interface TokenResponse {
  access_token: string;
  token_type: string;
  user_id: string;
  email: string;
}

// Analysis mode picked on the landing-page chooser. Persists for the
// session's lifetime; new sessions get a fresh choice.
export type Mode = 'groeikern' | 'jeugdzorg';

export const SUPPORTED_MODES: readonly Mode[] = ['groeikern', 'jeugdzorg'];

export const DEFAULT_MODE: Mode = 'groeikern';

export interface ChatSession {
  id: string;
  user_id: string;
  title: string;
  topic?: string | null;
  mode?: Mode;
  created_at: string;
  updated_at: string;
}

export type MessageRole = 'user' | 'assistant' | 'system' | 'tool_progress';
export type MessageStatus = 'complete' | 'thinking' | 'streaming' | 'failed';

export interface ChatMessage {
  id: string;
  session_id: string;
  role: MessageRole;
  content: string;
  status: MessageStatus;
  metadata: MessageMetadata | null;
  created_at: string;
}

export interface MessageMetadata {
  type?: string;
  content?: string;
  query?: string;
  skill?: string;
  analysis?: AnalysisResult;
}

// --- AnalysisResult (matches Capelle Dashboard agent schema) ---

export type ToolSource = 'cbs' | 'budget' | 'beleid' | 'buitenbeter' | 'bewonersenquete' | 'platform' | 'jeugdzorg';
export type ColumnType = 'string' | 'number' | 'year' | 'url' | 'text_snippet';
export type ResultType = 'table' | 'search_results' | 'summary' | 'time_series' | 'forecast';
// 'forecast' renders a line with a translucent confidence-interval ribbon —
// the jeugdzorg flow emits it for OLS projections; existing charts (bar /
// line / table / map / treemap) are unchanged.
export type ChartType = 'bar' | 'line' | 'table' | 'map' | 'treemap' | 'forecast';

export interface ColumnDef {
  key: string;
  label: string;
  type: ColumnType;
  unit?: string;
}

export interface ChartHint {
  type: ChartType;
  x: string;
  y: string | string[];
  group_by?: string;
  // Forecast-only — name of the column carrying the prediction-interval
  // lower / upper bounds. Ignored for non-forecast chart types.
  lower?: string;
  upper?: string;
  title: string;
}

export interface ToolOutput {
  tool: ToolSource;
  query: string;
  result_type: ResultType;
  data: Record<string, unknown>[];
  columns: ColumnDef[];
  metadata?: Record<string, unknown>;
  chart_hints?: ChartHint[];
  timestamp?: string;
}

export interface AnalysisSection {
  heading: string;
  source: ToolSource;
  content: string;
  tool_output?: ToolOutput;
}

export interface AnalysisResult {
  id: string;
  timestamp: string;
  query: string;
  /** Agent-generated report title (topic + scope). Falls back to `query`. */
  title?: string;
  summary: string;
  /** Present (=2) when the report uses the block document model. */
  schema_version?: number;
  /** Ordered freeform document. When present, the renderer uses this. */
  blocks?: Block[];
  /** Citation registry referenced by block `citations`/`citation` ids. */
  citations?: CitationRef[];
  /** Legacy fixed structure — still rendered via the sectionsToBlocks adapter. */
  sections: AnalysisSection[];
  data_gaps?: string[];
  follow_up?: string[];
}

// --- Block-based report schema (v2) ---
// Reports may emit an ordered `blocks[]` document instead of the legacy
// fixed `sections[]`. The renderer prefers `blocks` and falls back to an
// adapter over `sections`. `schema_version` disambiguates.

export const SCHEMA_VERSION = 2 as const;

/** The catalog of chart kinds the renderer can draw. */
export type ChartKind =
  | 'bar'
  | 'grouped_bar'
  | 'stacked_bar'
  | 'line'
  | 'area'
  | 'pie'
  | 'donut'
  | 'scatter'
  | 'treemap'
  | 'histogram'
  | 'boxplot'
  | 'violin';

/** A self-contained chart: the agent supplies data + columns + encodings. */
export interface ChartSpec {
  kind: ChartKind;
  data: Record<string, unknown>[];
  columns: ColumnDef[];
  /** Category / X-axis column. */
  x?: string;
  /** Value column(s). A single key, or several for grouped/stacked/multi-line. */
  y?: string | string[];
  /** Optional series-splitting column (grouped_bar, stacked_bar, scatter colour). */
  group_by?: string;
  title?: string;
}

/** A document a claim can cite. Mirrors the per-row source-identifier rules. */
export interface CitationRef {
  id: string;
  source: string;
  /** "CBS-tabel" | "begroting" | "Taakveld" | "Bewonersenquête" | ... */
  doc_type: string;
  /** Table id / human title / taakveld / wijk-onderwerp. */
  document: string;
  year?: string;
  /** beleid only — copied verbatim from the tool. Never fabricated. */
  source_url?: string;
  /** Real source document id — e.g. CVDR647_v1, CBS table 86165NED, taakveld code. */
  ref?: string;
}

export interface BlockStyle {
  emphasis?: boolean;
  width?: 'full' | 'half';
  tone?: 'default' | 'muted';
}

/** Citations are referenced by id into `AnalysisResult.citations`. */
export type Block =
  | { type: 'heading'; level: 1 | 2 | 3; text: string; style?: BlockStyle }
  | { type: 'prose'; markdown: string; citations?: string[]; style?: BlockStyle }
  | { type: 'chart'; spec: ChartSpec; caption?: string; citations?: string[]; style?: BlockStyle }
  | {
      type: 'table';
      columns: ColumnDef[];
      data: Record<string, unknown>[];
      caption?: string;
      citations?: string[];
      style?: BlockStyle;
    }
  | { type: 'kpi'; value: string | number; label: string; unit?: string; delta?: string; citations?: string[]; style?: BlockStyle }
  | { type: 'callout'; tone: 'info' | 'warning' | 'insight'; markdown: string; style?: BlockStyle }
  | { type: 'quote'; markdown: string; citation?: string; style?: BlockStyle }
  | { type: 'divider'; style?: BlockStyle }
  | { type: 'sources'; refs: string[]; style?: BlockStyle };

// --- Finding-graph (mirrors backend/capelle_platform/graph/models.py) ---
// Field names match the server JSON exactly (model_dump(mode="json")).

export type GraphNodeType = 'finding' | 'context' | 'hypothesis' | 'verification';

export type GraphEdgeType =
  | 'causes'
  | 'explains'
  | 'controls'
  | 'tensions_with'
  | 'depends_on'
  | 'decomposes_into';

export type GraphNodeStatus = 'proposed' | 'supported' | 'verified' | 'pruned';

export type Confidence = 'low' | 'medium' | 'high';

/** A single node in the session's finding-graph. `blocks` reuses the v2
 *  Block schema so the existing BlockRenderer renders the node's evidence. */
export interface GraphNode {
  id: string;
  node_type: GraphNodeType;
  claim: string;
  blocks: Block[];
  citations: CitationRef[];
  confidence: Confidence;
  status: GraphNodeStatus;
  agent_label: string;
  message_id: string;
  created_at: string;
  trace_id?: string | null;
}

/** A directed, typed relationship between two graph nodes. */
export interface GraphEdge {
  id: string;
  source_id: string;
  target_id: string;
  edge_type: GraphEdgeType;
  rationale: string;
  created_at: string;
}

/** The session-scoped finding-graph as served by
 *  GET /api/chat/sessions/{id}/graph. */
export interface FindingGraph {
  session_id: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  summary: string | null;
  version: number;
}

/**
 * Live graph mutation pushed over the existing WebSocket.
 * `data` is a GraphNode (kind="node"), GraphEdge (kind="edge") or the new
 * summary string (kind="summary"). Matches GraphService._emit exactly.
 */
export interface GraphDeltaEvent {
  type: 'graph_delta';
  session_id: string;
  message_id: string;
  kind: 'node' | 'edge' | 'summary';
  data: GraphNode | GraphEdge | string;
}

// --- WebSocket ---

/**
 * Mid-run elicitation: the agent needs clarification from the user.
 * Matches the backend event shape exactly.
 */
export interface ElicitationEvent {
  type: 'elicitation';
  message_id: string;
  question_id: string;
  question: string;
  options: string[];
  allow_free_text: boolean;
}

/** A clarifying question the agent asked + the answer the user gave, kept in
 *  the thread so the exchange stays visible after the card is dismissed. */
export interface AnsweredElicitation {
  message_id: string;
  question: string;
  answer: string;
}

export interface WSEvent {
  type: 'status' | 'progress' | 'message_complete' | 'message_failed' | 'elicitation' | 'graph_delta';
  message_id: string;
  status?: string;
  content?: string;
  metadata?: MessageMetadata;
  error?: string;
  tool?: string;
  action?: string;
  // Elicitation-specific fields (present when type === 'elicitation')
  question_id?: string;
  question?: string;
  options?: string[];
  allow_free_text?: boolean;
  // Graph-delta-specific fields (present when type === 'graph_delta')
  session_id?: string;
  kind?: 'node' | 'edge' | 'summary';
  data?: GraphNode | GraphEdge | string;
}

export interface ToolProgressEvent {
  tool: string;
  action: string;
  status: 'running' | 'done';
}

/**
 * One row in the in-run activity list: a single tool call the agent made,
 * tracked from its `running` (pre-tool-use) event to its `done` (post-tool-use)
 * event. `id` is a monotonic counter assigned by the reducer so the row is
 * stable and React-keyable; `source` is derived at render time from `tool`.
 */
export interface ActivityStep {
  id: string;
  tool: string;
  action: string;
  status: 'running' | 'done';
}
