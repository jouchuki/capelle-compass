import { motion } from 'motion/react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage } from '../../types';
import AnalysisHandoffCard from './AnalysisHandoffCard';

interface MessageBubbleProps {
  message: ChatMessage;
  onOpenReport?: (messageId: string) => void;
}

/**
 * Single chat row. User bubbles are slate-100 right-aligned; assistant
 * bubbles are bordered white with markdown body. If the assistant message
 * carries a finished analysis, the handoff card is inlined at the bottom.
 */
export default function MessageBubble({ message, onOpenReport }: MessageBubbleProps) {
  const isUser = message.role === 'user';
  const analysis = message.metadata?.analysis;
  // A finished analysis is present if it carries v2 blocks OR legacy sections.
  const hasAnalysis =
    !!analysis &&
    (((analysis.blocks?.length ?? 0) > 0) || ((analysis.sections?.length ?? 0) > 0));

  if (message.role === 'system' || message.role === 'tool_progress') {
    // Tool-progress and system rows live above the in-flight assistant
    // bubble (ActivityLog) rather than as standalone bubbles. Skip them
    // here so they don't double-render.
    return null;
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className={`flex ${isUser ? 'justify-end' : 'justify-start'}`}
    >
      <div
        className={`max-w-[85%] rounded-xl text-sm leading-relaxed ${
          isUser
            ? 'bg-slate-100 text-slate-900 px-6 py-4'
            : 'bg-white text-slate-900 px-6 py-4 border border-slate-100'
        }`}
      >
        {isUser ? (
          <p className="whitespace-pre-wrap">{message.content}</p>
        ) : (
          <div className="prose prose-slate prose-sm max-w-none [&_p]:my-2 [&_p:first-child]:mt-0 [&_p:last-child]:mb-0 [&_ul]:my-2 [&_ol]:my-2 [&_li]:my-0.5">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>
              {message.content || '...'}
            </ReactMarkdown>
          </div>
        )}

        {hasAnalysis && onOpenReport && analysis ? (
          <AnalysisHandoffCard
            analysis={analysis}
            onOpen={() => onOpenReport(message.id)}
          />
        ) : null}
      </div>
    </motion.div>
  );
}
