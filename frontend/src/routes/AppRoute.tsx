/**
 * Main chat + side-artifact UX.
 *
 * Orchestrates:
 *   - auth gate (`useAuth` redirects to /login)
 *   - sidebar / chat / artifact triptych with framer-motion width animation
 *   - URL ↔ active-session sync (`?session=<id>`)
 *   - reducer-driven WS event handling for streaming progress / completion
 *   - mock-mode bridge: when the backend is mocked, the WS hook doesn't fire,
 *     so we synthesise a canned tool-progress stream + final analysis payload
 *     ourselves to keep the demo flow honest.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useReducer,
  useRef,
  useState,
} from 'react';
import { Navigate, useSearchParams } from 'react-router-dom';
import { AnimatePresence, motion } from 'motion/react';
import { toast } from 'sonner';
import { Waypoints } from 'lucide-react';
import type {
  ChatMessage,
  GraphDeltaEvent,
  GraphNode,
  ToolProgressEvent,
  WSEvent,
} from '../types';
import { reducer, INITIAL_STATE, MAX_FOCUS_NODES } from './chatReducer';
import {
  createSession,
  getSession,
  getSessionGraph,
  postElicitationAnswer,
  recordEvent,
  sendMessage,
  ApiError,
} from '../api/client';
import { useAuth } from '../hooks/useAuth';
import { useWebSocket } from '../hooks/useWebSocket';
import { isMockMode } from '../mocks';
import { MOCK_ANALYSIS_VEILIGHEID } from '../mocks/fixtures';
import { isGraphNonEmpty } from '../utils/graphMerge';
import Sidebar from '../components/Sidebar';
import Chat from '../components/chat/Chat';
import OnboardingHero from '../components/chat/OnboardingHero';
import JeugdzorgLanding from '../components/chat/JeugdzorgLanding';
import ModeToggle from '../components/chat/ModeToggle';
import ArtifactPanel from '../components/artifact/ArtifactPanel';
import type { ArtifactTab } from '../components/artifact/ArtifactPanel';
import TopDisclaimer from '../components/TopDisclaimer';
import QuotaLimitModal from '../components/modals/QuotaLimitModal';
import type { Mode } from '../types';

// ─── Mock-mode streaming simulator ───────────────────────────────────────

const MOCK_PROGRESS: ToolProgressEvent[] = [
  { tool: 'cbs', action: 'CBS data ophalen', status: 'running' },
  { tool: 'beleid', action: 'Beleidsdocumenten doorzoeken', status: 'running' },
  { tool: 'budget', action: 'Begroting analyseren', status: 'running' },
  { tool: 'buitenbeter', action: 'Meldingen verwerken', status: 'running' },
  { tool: 'bewonersenquete', action: 'Enquête synthetiseren', status: 'running' },
];

// ─── Route component ─────────────────────────────────────────────────────

export default function AppRoute() {
  const { isAuthenticated, loading, email, handleLogout } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const activeSessionId = searchParams.get('session');

  const [state, dispatch] = useReducer(reducer, INITIAL_STATE);
  const [sending, setSending] = useState<boolean>(false);
  const [refreshKey, setRefreshKey] = useState<number>(0);
  const [activeReportMessageId, setActiveReportMessageId] = useState<string | null>(null);
  const [quotaModalOpen, setQuotaModalOpen] = useState<boolean>(false);
  // Mode for the landing surface, switched inline via <ModeToggle>. Defaults
  // to groeikern so login lands straight on a usable screen (no blocking
  // modal); the choice rides into the session created on the next analysis.
  const [chosenMode, setChosenMode] = useState<Mode>('groeikern');
  // Finding-graph UI state. `artifactTab` picks the pane inside the artifact
  // area; `graphOpen` opens that area for the graph alone (live run before
  // the first report exists). The follow-up scope ("Verdiep dit", up to
  // MAX_FOCUS_NODES nodes) lives in the reducer as `state.scopedNodes`.
  const [artifactTab, setArtifactTab] = useState<ArtifactTab>('rapport');
  const [graphOpen, setGraphOpen] = useState<boolean>(false);
  const mockTimersRef = useRef<Array<ReturnType<typeof setTimeout>>>([]);
  // Synchronous guard against rapid double-submits: setSending(true) is async
  // (React batched state), so a second Enter/click in the same tick would
  // otherwise slip through before `sending` reaches the ChatInput's disabled
  // prop. The ref closes that window.
  const inflightRef = useRef<boolean>(false);

  // Wire every WS frame straight into the reducer. Using a callback (not a
  // single "lastEvent" slot) means concurrent frames in the same tick — e.g.
  // parallel subagents each firing a `capelle-ask` elicitation — are all
  // delivered instead of collapsing to whichever arrived last.
  const handleWsEvent = useCallback((event: WSEvent) => {
    switch (event.type) {
      case 'status':
        dispatch({ type: 'ws_status', event });
        break;
      case 'progress':
        dispatch({ type: 'ws_progress', event });
        break;
      case 'message_complete':
        dispatch({ type: 'ws_complete', event });
        inflightRef.current = false;
        setSending(false);
        break;
      case 'message_failed':
        dispatch({ type: 'ws_failed', event });
        inflightRef.current = false;
        setSending(false);
        break;
      case 'elicitation': {
        // Only dispatch if all required elicitation fields are present.
        if (
          event.question_id &&
          event.question !== undefined &&
          event.options !== undefined &&
          event.allow_free_text !== undefined
        ) {
          dispatch({
            type: 'ws_elicitation',
            event: {
              type: 'elicitation',
              message_id: event.message_id,
              question_id: event.question_id,
              question: event.question,
              options: event.options,
              allow_free_text: event.allow_free_text,
            },
          });
        }
        break;
      }
      case 'graph_delta': {
        // Only dispatch well-formed deltas; the merge helper additionally
        // drops anything that isn't for the active session.
        if (event.session_id && event.kind && event.data !== undefined) {
          const delta: GraphDeltaEvent = {
            type: 'graph_delta',
            session_id: event.session_id,
            message_id: event.message_id,
            kind: event.kind,
            data: event.data,
          };
          dispatch({ type: 'graph_delta', event: delta, activeSessionId });
        }
        break;
      }
    }
  }, [activeSessionId]);

  useWebSocket(isAuthenticated && !isMockMode(), handleWsEvent);

  const graphAvailable = isGraphNonEmpty(state.graph);
  const isArtifactOpen =
    activeReportMessageId !== null || (graphOpen && graphAvailable);

  // Auth gate.
  // (Run the redirect after the early-returns below so hook order stays stable.)
  const authRedirect = !loading && !isAuthenticated;

  const setActiveSessionId = useCallback(
    (id: string | null) => {
      const next = new URLSearchParams(searchParams);
      if (id) next.set('session', id);
      else next.delete('session');
      setSearchParams(next, { replace: false });
    },
    [searchParams, setSearchParams],
  );

  // Load messages whenever the active session changes.
  useEffect(() => {
    // Graph state is session-scoped: clear it (and every graph affordance)
    // eagerly so a stale graph never bleeds into the next session.
    dispatch({ type: 'set_graph', graph: null });
    setGraphOpen(false);
    setArtifactTab('rapport');
    dispatch({ type: 'scope_clear' });
    if (!activeSessionId) {
      dispatch({ type: 'set', messages: [] });
      setActiveReportMessageId(null);
      return;
    }
    let cancelled = false;
    getSession(activeSessionId)
      .then((r) => {
        if (!cancelled) {
          dispatch({ type: 'set', messages: r.messages });
          setActiveReportMessageId(null);
        }
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: 'set', messages: [] });
      });
    // Fetch the finding-graph alongside the messages. 404 (no graph yet,
    // legacy session, or feature flag off) resolves to null — and so does
    // any other failure: the graph is an enhancement, never a blocker.
    getSessionGraph(activeSessionId)
      .then((graph) => {
        if (!cancelled) dispatch({ type: 'set_graph', graph });
      })
      .catch(() => {
        if (!cancelled) dispatch({ type: 'set_graph', graph: null });
      });
    return () => {
      cancelled = true;
    };
  }, [activeSessionId]);

  // Cleanup any pending mock timers on unmount.
  useEffect(() => {
    return () => {
      for (const t of mockTimersRef.current) clearTimeout(t);
      mockTimersRef.current = [];
    };
  }, []);

  /** Drive a canned progress/complete sequence in mock mode (no real WS). */
  const runMockStream = useCallback((assistantMessageId: string, query: string) => {
    for (const t of mockTimersRef.current) clearTimeout(t);
    mockTimersRef.current = [];

    MOCK_PROGRESS.forEach((step, idx) => {
      const handle = setTimeout(() => {
        dispatch({
          type: 'ws_progress',
          event: {
            type: 'progress',
            message_id: assistantMessageId,
            tool: step.tool,
            action: step.action,
          },
        });
      }, 350 + idx * 500);
      mockTimersRef.current.push(handle);
    });

    const completeHandle = setTimeout(
      () => {
        dispatch({
          type: 'ws_complete',
          event: {
            type: 'message_complete',
            message_id: assistantMessageId,
            content:
              'Ik heb een analyse opgesteld op basis van CBS-criminaliteit, BuitenBeter-meldingen, beleidsdocumenten, Iv3-budget en de bewonersenquête. De volledige rapportage kun je rechts inzien.',
            metadata: {
              analysis: {
                ...MOCK_ANALYSIS_VEILIGHEID,
                id: `a-mock-${Date.now()}`,
                timestamp: new Date().toISOString(),
                query,
              },
            },
          },
        });
        inflightRef.current = false;
        setSending(false);
      },
      350 + MOCK_PROGRESS.length * 500 + 400,
    );
    mockTimersRef.current.push(completeHandle);
  }, []);

  const handleSend = useCallback(
    async (content: string, sessionIdOverride?: string) => {
      // Synchronous guard: if a send is already in flight, ignore. This is
      // checked + set before any await so two same-tick invocations can't
      // both pass through.
      if (inflightRef.current) return;
      const sessionId = sessionIdOverride ?? activeSessionId;
      if (!sessionId) return;
      inflightRef.current = true;
      setSending(true);
      dispatch({ type: 'reset_events' });
      try {
        // Node-scoped follow-up: forward the scoped nodes' ids; the backend
        // injects each node's local subgraph as prior context (Task 5).
        // client.ts picks the wire shape (singular node_id for one node,
        // node_ids[] for several). Chips clear only after a successful send.
        const scopedIds = state.scopedNodes.map((n) => n.id);
        const res = await sendMessage(
          sessionId,
          content,
          scopedIds.length > 0 ? scopedIds : undefined,
        );
        dispatch({ type: 'scope_clear' });
        // Mock router doesn't return the same shape as the real backend
        // (it returns the full user/assistant message objects); the contract
        // we rely on is the assistant_message_id, falling back to whatever
        // the mock returns.
        const userMsgId =
          ('user_message_id' in res && typeof res.user_message_id === 'string'
            ? res.user_message_id
            : '') || `m-${Date.now()}-u`;
        const assistantMsgId =
          ('assistant_message_id' in res &&
          typeof res.assistant_message_id === 'string'
            ? res.assistant_message_id
            : '') || `m-${Date.now()}-a`;

        const nowIso = new Date().toISOString();
        const userMsg: ChatMessage = {
          id: userMsgId,
          session_id: sessionId,
          role: 'user',
          content,
          status: 'complete',
          metadata: null,
          created_at: nowIso,
        };
        const assistantMsg: ChatMessage = {
          id: assistantMsgId,
          session_id: sessionId,
          role: 'assistant',
          content: '',
          status: 'thinking',
          metadata: null,
          created_at: nowIso,
        };
        dispatch({ type: 'append', message: userMsg });
        dispatch({ type: 'append', message: assistantMsg });

        void recordEvent('message_sent', { length: content.length }, sessionId);

        if (isMockMode()) {
          runMockStream(assistantMsgId, content);
        }
      } catch (err) {
        inflightRef.current = false;
        setSending(false);
        if (err instanceof ApiError && err.status === 429) {
          setQuotaModalOpen(true);
        } else {
          const msg =
            err instanceof Error
              ? err.message
              : 'Bericht versturen mislukt';
          toast.error(msg);
        }
      }
    },
    [activeSessionId, runMockStream, state.scopedNodes],
  );

  const handleNewSession = useCallback(() => {
    // "Nieuwe analyse" returns to the landing (keeping the current mode, which
    // the inline ModeToggle lets the user change). We do NOT create a session
    // here; that happens inside handleAsk once there is a query, so the mode
    // chosen at that moment is the one persisted on the session row.
    setActiveSessionId(null);
  }, [setActiveSessionId]);

  const handleAsk = useCallback(
    async (question: string) => {
      // Used by both OnboardingHero (map cells, groeikern thumbs, composer)
      // and FollowUpCard rows. Without a guard, rapid double-clicks could
      // each `await createSession()` and produce two backend sessions before
      // either reached the handleSend ref-check — the second session would
      // then race the first into the URL via setActiveSessionId and the
      // user would see ghost duplicates.
      //
      // We reuse ``inflightRef`` so the entire user-action surface (compose,
      // ask, map cell, groeikern, follow-up) shares one lock and stays in
      // sync with the ``sending`` UI state.
      if (inflightRef.current) return;
      inflightRef.current = true;
      setSending(true);
      try {
        let sessionId = activeSessionId;
        if (!sessionId) {
          // Fresh analysis → must have a chosen mode. If the user somehow
          // reached this path without picking (defensive — UI gates the
          // landing surface behind the chooser), fall back to groeikern
          // so the call doesn't fail.
          const modeForSession: Mode = chosenMode;
          const session = await createSession(
            question.slice(0, 60),
            modeForSession,
          );
          sessionId = session.id;
          setActiveSessionId(session.id);
          setRefreshKey((k) => k + 1);
          void recordEvent(
            'session_created',
            { from: 'example_or_followup', mode: modeForSession },
            session.id,
          );
          void recordEvent('example_query_clicked', { query: question }, session.id);
        }
        // handleSend will re-check inflightRef; we release the lock here so
        // handleSend can re-acquire it on the same tick. handleSend owns
        // the lock for the actual streaming lifetime (until ws_complete
        // or ws_failed fires).
        inflightRef.current = false;
        await handleSend(question, sessionId);
      } catch {
        inflightRef.current = false;
        setSending(false);
        toast.error('Er ging iets mis. Probeer het opnieuw.');
      }
    },
    [activeSessionId, handleSend, setActiveSessionId, chosenMode],
  );

  const handleElicitationAnswer = useCallback(
    async (questionId: string, answer: string) => {
      // Record the Q&A into the thread (and clear the card) before posting.
      dispatch({ type: 'answer_elicitation', questionId, answer });
      try {
        await postElicitationAnswer(questionId, answer);
      } catch {
        toast.error('Kon antwoord niet verzenden. Probeer het opnieuw.');
      }
    },
    [],
  );

  const handleSelectSession = useCallback(
    (id: string) => {
      setActiveSessionId(id);
    },
    [setActiveSessionId],
  );

  const handleOpenReport = useCallback((messageId: string) => {
    setActiveReportMessageId(messageId);
    setArtifactTab('rapport');
  }, []);

  const handleCloseArtifact = useCallback(() => {
    setActiveReportMessageId(null);
    setGraphOpen(false);
  }, []);

  const handleOpenGraph = useCallback(() => {
    setGraphOpen(true);
    setArtifactTab('graaf');
  }, []);

  // "Verdiep dit" toggles the node in the follow-up scope: a node that is
  // already scoped gets removed, a new one is added (the reducer dedups and
  // caps at MAX_FOCUS_NODES).
  const scopedNodeIds = useMemo(
    () => state.scopedNodes.map((n) => n.id),
    [state.scopedNodes],
  );

  const handleVerdiep = useCallback(
    (node: GraphNode) => {
      if (scopedNodeIds.includes(node.id)) {
        dispatch({ type: 'scope_remove', nodeId: node.id });
      } else {
        dispatch({ type: 'scope_add', node: { id: node.id, claim: node.claim } });
      }
    },
    [scopedNodeIds],
  );

  // Analyses in this session, ordered chronologically.
  const analyses = useMemo(() => {
    return state.messages
      .filter((m) => !!m.metadata?.analysis)
      .map((m) => ({ messageId: m.id, analysis: m.metadata!.analysis! }));
  }, [state.messages]);

  const activeIndex = useMemo(() => {
    if (!activeReportMessageId) return -1;
    return analyses.findIndex((a) => a.messageId === activeReportMessageId);
  }, [analyses, activeReportMessageId]);

  const activeAnalysis = activeIndex >= 0 ? analyses[activeIndex] : undefined;

  const handlePrev = useCallback(() => {
    if (activeIndex <= 0) return;
    const prev = analyses[activeIndex - 1];
    if (prev) setActiveReportMessageId(prev.messageId);
  }, [analyses, activeIndex]);

  const handleNext = useCallback(() => {
    if (activeIndex < 0 || activeIndex >= analyses.length - 1) return;
    const next = analyses[activeIndex + 1];
    if (next) setActiveReportMessageId(next.messageId);
  }, [analyses, activeIndex]);

  // ─── Render ────────────────────────────────────────────────────────────
  if (authRedirect) {
    return <Navigate to="/login" replace />;
  }

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-white">
        <p className="text-sm text-slate-400">Bezig met laden...</p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-screen bg-white text-slate-900 font-sans selection:bg-blue-100">
      <TopDisclaimer />
      <div className="flex flex-1 overflow-hidden">
      <AnimatePresence>
        {!isArtifactOpen && (
          <Sidebar
            activeSessionId={activeSessionId}
            email={email}
            onNewSession={() => void handleNewSession()}
            onSelectSession={handleSelectSession}
            refreshKey={refreshKey}
            onLogout={() => void handleLogout()}
          />
        )}
      </AnimatePresence>

      <main className="flex flex-1 overflow-hidden">
        <motion.div
          animate={{ width: isArtifactOpen ? '35%' : '100%' }}
          transition={{ type: 'spring', damping: 30, stiffness: 200 }}
          className="relative h-full border-r border-slate-100 z-10 bg-white"
        >
          {graphAvailable && !isArtifactOpen ? (
            <button
              type="button"
              onClick={handleOpenGraph}
              className="absolute top-4 right-4 z-20 inline-flex items-center gap-1.5 rounded-full bg-white border border-slate-200 px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-slate-600 shadow-sm hover:text-blue-600 hover:border-blue-200 transition-colors"
              aria-label="Open onderzoeksgraaf"
            >
              <Waypoints className="w-3.5 h-3.5" />
              Graaf
            </button>
          ) : null}
          {activeSessionId ? (
            <Chat
              messages={state.messages}
              stepsByMessage={state.stepsByMessage}
              elicitations={state.elicitations}
              answeredElicitations={state.answeredElicitations}
              sending={sending}
              isArtifactOpen={isArtifactOpen}
              scopedNodes={state.scopedNodes}
              maxScopedNodes={MAX_FOCUS_NODES}
              onRemoveScope={(nodeId) => dispatch({ type: 'scope_remove', nodeId })}
              onSend={(c) => void handleSend(c)}
              onOpenReport={handleOpenReport}
              onElicitationAnswer={(qId, answer) => void handleElicitationAnswer(qId, answer)}
            />
          ) : (
            <div className="h-full overflow-y-auto px-8 py-12 custom-scrollbar">
              <ModeToggle value={chosenMode} onChange={setChosenMode} />
              {chosenMode === 'jeugdzorg' ? (
                <JeugdzorgLanding
                  onAsk={(q) => void handleAsk(q)}
                  sending={sending}
                />
              ) : (
                <OnboardingHero
                  onAsk={(q) => void handleAsk(q)}
                  sending={sending}
                />
              )}
            </div>
          )}
        </motion.div>

        <AnimatePresence>
          {isArtifactOpen && (activeAnalysis || graphAvailable) ? (
            <motion.div
              initial={{ width: 0 }}
              animate={{ width: '65%' }}
              exit={{ width: 0 }}
              transition={{ type: 'spring', damping: 30, stiffness: 200 }}
              className="h-full overflow-hidden"
            >
              <ArtifactPanel
                analysis={activeAnalysis?.analysis ?? null}
                sessionId={activeSessionId}
                totalAnalyses={analyses.length}
                currentIndex={activeIndex}
                onPrev={handlePrev}
                onNext={handleNext}
                onClose={handleCloseArtifact}
                onAskFollowUp={(q) => void handleAsk(q)}
                graph={state.graph}
                tab={artifactTab}
                onTabChange={setArtifactTab}
                onVerdiep={handleVerdiep}
                scopedNodeIds={scopedNodeIds}
                scopeFull={state.scopedNodes.length >= MAX_FOCUS_NODES}
              />
            </motion.div>
          ) : null}
        </AnimatePresence>
      </main>
      </div>
      <QuotaLimitModal open={quotaModalOpen} onClose={() => setQuotaModalOpen(false)} />
    </div>
  );
}
