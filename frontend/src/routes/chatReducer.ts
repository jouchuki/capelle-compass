/**
 * Chat state + reducer for {@link AppRoute}.
 *
 * Extracted from the route component so the state machine — especially the
 * elicitation queue — is unit-testable in isolation.
 *
 * Elicitations are a **list**, not a single slot. A run may have several
 * clarifying questions in flight at once: the groeikern prompt fans a large
 * comparison out into parallel subagents (one per town), and each subagent can
 * fire its own blocking ``capelle-ask`` against the same assistant
 * ``message_id``. The backend parks each as an independent question_id and
 * broadcasts one ``elicitation`` event per ask, so the frontend must hold every
 * pending question simultaneously and let the user answer each on its own.
 */

import type {
  ActivityStep,
  AnsweredElicitation,
  ChatMessage,
  ElicitationEvent,
  FindingGraph,
  GraphDeltaEvent,
  GraphEdge,
  WSEvent,
} from '../types';
import { applyGraphDelta } from '../utils/graphMerge';

/**
 * Maximum number of graph nodes a follow-up can be scoped to ("Verdiep dit").
 * Mirrors the backend's MAX_FOCUS_NODES on ChatMessageCreate — the reducer
 * enforces it client-side so the chip row can never outgrow what the server
 * will accept.
 */
export const MAX_FOCUS_NODES = 5;

/** One graph node in the follow-up scope: just enough to render its chip. */
export interface ScopedNode {
  id: string;
  claim: string;
}

export interface ChatState {
  messages: ChatMessage[];
  /**
   * In-run activity steps bucketed by the assistant ``message_id`` they belong
   * to, so each analysis in a multi-analysis session keeps its own list and its
   * own collapsed summary. Each `progress` event carries its message_id.
   */
  stepsByMessage: Record<string, ActivityStep[]>;
  /** Monotonic id source for activity steps — keeps the reducer pure. */
  nextStepId: number;
  /** Pending elicitation prompts, keyed by question_id, in arrival order. */
  elicitations: ElicitationEvent[];
  /** Resolved clarifying questions, kept visible in the thread. */
  answeredElicitations: AnsweredElicitation[];
  /** The session's finding-graph (null = none / feature off / legacy session). */
  graph: FindingGraph | null;
  /** graph_delta edges that arrived before both their endpoints did. */
  pendingGraphEdges: GraphEdge[];
  /**
   * Graph nodes the next follow-up is scoped to ("Verdiep dit"), in the order
   * the user added them. Capped at {@link MAX_FOCUS_NODES}; cleared on session
   * change and after a successful send.
   */
  scopedNodes: ScopedNode[];
}

export type Action =
  | { type: 'set'; messages: ChatMessage[] }
  | { type: 'append'; message: ChatMessage }
  | { type: 'ws_status'; event: WSEvent }
  | { type: 'ws_progress'; event: WSEvent }
  | { type: 'ws_complete'; event: WSEvent }
  | { type: 'ws_failed'; event: WSEvent }
  | { type: 'ws_elicitation'; event: ElicitationEvent }
  | { type: 'answer_elicitation'; questionId: string; answer: string }
  | { type: 'dismiss_elicitation' }
  | { type: 'reset_events' }
  | { type: 'set_graph'; graph: FindingGraph | null }
  | { type: 'graph_delta'; event: GraphDeltaEvent; activeSessionId: string | null }
  | { type: 'scope_add'; node: ScopedNode }
  | { type: 'scope_remove'; nodeId: string }
  | { type: 'scope_clear' };

export const INITIAL_STATE: ChatState = {
  messages: [],
  stepsByMessage: {},
  nextStepId: 0,
  elicitations: [],
  answeredElicitations: [],
  graph: null,
  pendingGraphEdges: [],
  scopedNodes: [],
};

export function reducer(state: ChatState, action: Action): ChatState {
  switch (action.type) {
    case 'set': {
      // Dedup by id — the source can be a fresh getSession response that
      // happens to overlap with a just-arrived ws_complete update; never
      // surface the same id twice in the rendered list.
      const seen = new Set<string>();
      const messages = action.messages.filter((m) => {
        if (seen.has(m.id)) return false;
        seen.add(m.id);
        return true;
      });
      return {
        messages,
        stepsByMessage: {},
        nextStepId: 0,
        elicitations: [],
        answeredElicitations: [],
        // Graph state is session-scoped but loaded by its own fetch; the
        // route resets it explicitly via 'set_graph' on session change so a
        // slow getSession response can't wipe an already-merged live graph.
        graph: state.graph,
        pendingGraphEdges: state.pendingGraphEdges,
        // Same story for the follow-up scope: the route clears it explicitly
        // ('scope_clear') on session change / after a successful send, so a
        // slow getSession response can't wipe chips the user just added.
        scopedNodes: state.scopedNodes,
      };
    }
    case 'append': {
      // Defensive dedup. handleSend appends user + assistant immediately,
      // the activeSessionId useEffect later swaps to the DB version via
      // 'set'. In the rare window where ws_complete races the
      // getSession-driven 'set' (e.g., the request manager retried under
      // load), the same id can flow through 'append' twice. Skip the
      // duplicate rather than surface a ghost bubble.
      if (state.messages.some((m) => m.id === action.message.id)) {
        return state;
      }
      return { ...state, messages: [...state.messages, action.message] };
    }
    case 'ws_status':
      return state;
    case 'ws_progress': {
      const { event } = action;
      if (!event.tool || !event.action) return state;
      const mid = event.message_id;
      const bucket = state.stepsByMessage[mid] ?? [];

      // 'done' (post-tool-use): resolve the most recent still-open step that
      // matches (tool, action). Heuristic — exact pairing would need a
      // tool_use_id the hook doesn't forward yet; a mismatch is cosmetic only.
      if (event.status === 'done') {
        for (let i = bucket.length - 1; i >= 0; i -= 1) {
          const s = bucket[i];
          if (s && s.status === 'running' && s.tool === event.tool && s.action === event.action) {
            const resolved = bucket.map((step, idx) =>
              idx === i ? { ...step, status: 'done' as const } : step,
            );
            return { ...state, stepsByMessage: { ...state.stepsByMessage, [mid]: resolved } };
          }
        }
        // No open match (start was missed) — append an already-done row so the
        // action isn't lost.
        const doneStep: ActivityStep = {
          id: String(state.nextStepId),
          tool: event.tool,
          action: event.action,
          status: 'done',
        };
        return {
          ...state,
          stepsByMessage: { ...state.stepsByMessage, [mid]: [...bucket, doneStep] },
          nextStepId: state.nextStepId + 1,
        };
      }

      // 'running' (pre-tool-use), or any non-'done' status: append a new step.
      const runningStep: ActivityStep = {
        id: String(state.nextStepId),
        tool: event.tool,
        action: event.action,
        status: 'running',
      };
      return {
        ...state,
        stepsByMessage: { ...state.stepsByMessage, [mid]: [...bucket, runningStep] },
        nextStepId: state.nextStepId + 1,
      };
    }
    case 'ws_complete': {
      const { event } = action;
      // The message's step bucket is left intact — it backs the collapsed
      // "✓ N stappen" summary on the now-completed message.
      return {
        ...state,
        // The run is done — any still-pending asks for this message are moot.
        elicitations: state.elicitations.filter((e) => e.message_id !== event.message_id),
        messages: state.messages.map((m) =>
          m.id === event.message_id
            ? {
                ...m,
                status: 'complete',
                content: event.content ?? m.content,
                metadata: event.metadata ?? m.metadata,
              }
            : m,
        ),
      };
    }
    case 'ws_failed': {
      const { event } = action;
      return {
        ...state,
        elicitations: state.elicitations.filter((e) => e.message_id !== event.message_id),
        messages: state.messages.map((m) =>
          m.id === event.message_id
            ? {
                ...m,
                status: 'failed',
                content: event.error ?? m.content ?? 'Er ging iets mis.',
              }
            : m,
        ),
      };
    }
    case 'ws_elicitation': {
      // Append, never overwrite. Dedup on question_id so a re-broadcast (WS
      // reconnect replay, double-delivery) doesn't stack the same card twice.
      if (state.elicitations.some((e) => e.question_id === action.event.question_id)) {
        return state;
      }
      return { ...state, elicitations: [...state.elicitations, action.event] };
    }
    case 'answer_elicitation': {
      // Record the resolved Q&A so it stays visible in the thread, then drop
      // just the answered card — sibling questions from other subagents stay.
      const active = state.elicitations.find((e) => e.question_id === action.questionId);
      const elicitations = state.elicitations.filter(
        (e) => e.question_id !== action.questionId,
      );
      if (!active) {
        return { ...state, elicitations };
      }
      const answered: AnsweredElicitation = {
        message_id: active.message_id,
        question: active.question,
        answer: action.answer,
      };
      return {
        ...state,
        elicitations,
        answeredElicitations: [...state.answeredElicitations, answered],
      };
    }
    case 'dismiss_elicitation':
      return { ...state, elicitations: [] };
    case 'reset_events':
      // A new send opens a fresh message_id bucket, so steps need no wiping
      // here — only the pending asks are cleared.
      return { ...state, elicitations: [] };
    case 'set_graph':
      return { ...state, graph: action.graph, pendingGraphEdges: [] };
    case 'graph_delta': {
      // Merge logic lives in the pure helper (utils/graphMerge) so it is
      // unit-testable without React. Identity-preserving no-ops (other
      // session, duplicate edge) skip the state copy entirely.
      const merged = applyGraphDelta(
        { graph: state.graph, pendingEdges: state.pendingGraphEdges },
        action.event,
        action.activeSessionId,
      );
      if (merged.graph === state.graph && merged.pendingEdges === state.pendingGraphEdges) {
        return state;
      }
      return { ...state, graph: merged.graph, pendingGraphEdges: merged.pendingEdges };
    }
    case 'scope_add': {
      // Toggle-adds are idempotent (dedup on id) and hard-capped at
      // MAX_FOCUS_NODES — the server rejects anything beyond that, so the
      // UI never lets the chip row grow past it in the first place.
      if (
        state.scopedNodes.some((n) => n.id === action.node.id) ||
        state.scopedNodes.length >= MAX_FOCUS_NODES
      ) {
        return state;
      }
      return { ...state, scopedNodes: [...state.scopedNodes, action.node] };
    }
    case 'scope_remove': {
      const scopedNodes = state.scopedNodes.filter((n) => n.id !== action.nodeId);
      if (scopedNodes.length === state.scopedNodes.length) {
        return state;
      }
      return { ...state, scopedNodes };
    }
    case 'scope_clear':
      if (state.scopedNodes.length === 0) {
        return state;
      }
      return { ...state, scopedNodes: [] };
  }
}
