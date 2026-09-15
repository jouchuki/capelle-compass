import { useEffect, useMemo, useRef } from 'react';
import { AnimatePresence } from 'motion/react';
import { X } from 'lucide-react';
import type {
  ActivityStep,
  AnsweredElicitation,
  ChatMessage,
  ElicitationEvent,
} from '../../types';
import MessageBubble from './MessageBubble';
import ChatInput from './ChatInput';
import ElicitationCard from './ElicitationCard';
import AnsweredElicitationCard from './AnsweredElicitationCard';
import ActivityLog from './ActivityLog';
import ActivitySummary from './ActivitySummary';
import SleuthMascot from './SleuthMascot';

interface ChatProps {
  messages: ChatMessage[];
  /** In-run activity steps bucketed by assistant message_id. */
  stepsByMessage: Record<string, ActivityStep[]>;
  /** Pending elicitation prompts from the agent — one card per in-flight ask
   *  (parallel subagents can each have a question open at once). */
  elicitations: ElicitationEvent[];
  /** Resolved clarifying questions, rendered inline in the thread. */
  answeredElicitations: AnsweredElicitation[];
  sending: boolean;
  isArtifactOpen: boolean;
  /** Node-scoped follow-up ("Verdiep dit"): the next message is scoped to
   *  these graph nodes. Renders one dismissible chip per node above the
   *  composer plus an n/5 counter. Optional — legacy callers without graph
   *  support render exactly as before. */
  scopedNodes?: readonly { id: string; claim: string }[];
  /** Maximum number of scoped nodes — drives the chip-row counter. */
  maxScopedNodes?: number;
  onRemoveScope?: (nodeId: string) => void;
  onSend: (content: string) => void;
  onOpenReport: (messageId: string) => void;
  onElicitationAnswer: (questionId: string, answer: string) => void;
}

const NO_STEPS: ActivityStep[] = [];

/** Max characters of the node claim shown in the composer scope chip. */
const SCOPE_CHIP_CLAIM_MAX_LEN = 50;

function truncateClaim(claim: string): string {
  if (claim.length <= SCOPE_CHIP_CLAIM_MAX_LEN) return claim;
  return `${claim.slice(0, SCOPE_CHIP_CLAIM_MAX_LEN)}…`;
}

/**
 * Chat surface: message thread above, composer below. While the most recent
 * assistant bubble is in-flight, we show the sleuth mascot during the
 * cold-start gap, then an ordered, resolving {@link ActivityLog} of the agent's
 * real tool steps. Completed runs collapse to an {@link ActivitySummary}.
 */
export default function Chat({
  messages,
  stepsByMessage,
  elicitations,
  answeredElicitations,
  sending,
  isArtifactOpen,
  scopedNodes = [],
  maxScopedNodes = 5,
  onRemoveScope,
  onSend,
  onOpenReport,
  onElicitationAnswer,
}: ChatProps) {
  const answeredFor = (messageId: string) =>
    answeredElicitations.filter((a) => a.message_id === messageId);
  const pendingFor = (messageId: string) =>
    elicitations.filter((e) => e.message_id === messageId);
  const stepsFor = (messageId: string) => stepsByMessage[messageId] ?? NO_STEPS;
  const scrollRef = useRef<HTMLDivElement>(null);

  // Find the in-flight assistant message (last one still running). Both
  // 'thinking' and 'streaming' are in-progress states — the backend flips
  // thinking->streaming early, so keying only on 'thinking' would race.
  const inProgressMessage = useMemo(() => {
    for (let i = messages.length - 1; i >= 0; i -= 1) {
      const m = messages[i];
      if (m && m.role === 'assistant' && (m.status === 'thinking' || m.status === 'streaming')) {
        return m;
      }
    }
    return null;
  }, [messages]);

  const inProgressStepCount = inProgressMessage
    ? stepsByMessage[inProgressMessage.id]?.length ?? 0
    : 0;

  // Auto-scroll to bottom on new messages or fresh activity steps.
  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;
    el.scrollTo({ top: el.scrollHeight, behavior: 'smooth' });
  }, [messages, inProgressStepCount, inProgressMessage]);

  return (
    <div className="flex flex-col h-full bg-white relative">
      <div
        ref={scrollRef}
        className="flex-1 overflow-y-auto px-6 py-12 scroll-smooth custom-scrollbar"
      >
        <div className={`mx-auto space-y-8 transition-all duration-500 ${isArtifactOpen ? 'max-w-full' : 'max-w-screen-md'}`}>
          <AnimatePresence initial={false}>
            {messages
              .filter((m) => m.role === 'user' || m.role === 'assistant')
              .map((message) => {
                const steps = stepsFor(message.id);
                // In-progress assistant bubble: Q&A on top, then the live
                // activity (sleuth during cold start, step list once tools
                // fire), then the bubble.
                if (inProgressMessage && message.id === inProgressMessage.id) {
                  return (
                    <div key={message.id} className="space-y-3">
                      {answeredFor(message.id).map((a, i) => (
                        <AnsweredElicitationCard key={i} question={a.question} answer={a.answer} />
                      ))}
                      {pendingFor(message.id).map((e) => (
                        <ElicitationCard
                          key={e.question_id}
                          question={e.question}
                          options={e.options}
                          allowFreeText={e.allow_free_text}
                          onAnswer={(answer) =>
                            onElicitationAnswer(e.question_id, answer)
                          }
                        />
                      ))}
                      {steps.length === 0 ? (
                        <SleuthMascot />
                      ) : (
                        <ActivityLog steps={steps} />
                      )}
                      {message.content ? (
                        <MessageBubble message={message} onOpenReport={onOpenReport} />
                      ) : null}
                    </div>
                  );
                }
                // Settled message: bubble, then a collapsed run summary (if it
                // made tool calls), then any answered clarifying questions.
                const answered = answeredFor(message.id);
                return (
                  <div key={message.id} className="space-y-3">
                    <MessageBubble message={message} onOpenReport={onOpenReport} />
                    {steps.length > 0 ? <ActivitySummary steps={steps} /> : null}
                    {answered.map((a, i) => (
                      <AnsweredElicitationCard key={i} question={a.question} answer={a.answer} />
                    ))}
                  </div>
                );
              })}
          </AnimatePresence>
        </div>
      </div>

      <div className="p-8 flex-shrink-0">
        {scopedNodes.length > 0 ? (
          <div
            className="max-w-screen-md mx-auto mb-2 flex flex-wrap items-center gap-1.5"
            data-testid="scope-chip-row"
          >
            {scopedNodes.map((scopedNode) => (
              <span
                key={scopedNode.id}
                className="inline-flex items-center gap-2 rounded-full bg-blue-50 border border-blue-200 pl-3 pr-1.5 py-1 text-xs font-medium text-blue-700 max-w-full"
                data-testid={`scope-chip-${scopedNode.id}`}
              >
                <span className="truncate">
                  Verdieping van: {truncateClaim(scopedNode.claim)}
                </span>
                <button
                  type="button"
                  aria-label={`Verwijder uit verdieping: ${truncateClaim(scopedNode.claim)}`}
                  onClick={() => onRemoveScope?.(scopedNode.id)}
                  className="p-0.5 rounded-full hover:bg-blue-100 flex-shrink-0"
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
            <span
              className="text-[11px] text-slate-400 tabular-nums flex-shrink-0"
              data-testid="scope-counter"
            >
              {scopedNodes.length}/{maxScopedNodes}
            </span>
          </div>
        ) : null}
        <ChatInput onSend={onSend} disabled={sending} />
      </div>
    </div>
  );
}
