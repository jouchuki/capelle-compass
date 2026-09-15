import { motion } from 'motion/react';
import { HelpCircle } from 'lucide-react';

interface AnsweredElicitationCardProps {
  question: string;
  answer: string;
}

/**
 * Static record of a resolved clarifying question — shown in the thread after
 * the user answers the ElicitationCard, so the Q&A stays part of the visible
 * chat history instead of vanishing when the card is dismissed.
 */
export default function AnsweredElicitationCard({
  question,
  answer,
}: AnsweredElicitationCardProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="rounded-xl border border-slate-100 bg-slate-50/60 px-5 py-4"
    >
      <div className="flex items-center gap-2 mb-2">
        <HelpCircle className="w-3.5 h-3.5 text-slate-400" aria-hidden="true" />
        <span className="text-[10px] uppercase tracking-wider font-bold text-slate-400">
          Verduidelijking
        </span>
      </div>
      <p className="text-sm text-slate-600 leading-snug mb-3">{question}</p>
      <div className="flex justify-end">
        <span className="inline-block max-w-[85%] rounded-xl bg-blue-600 text-white px-4 py-2 text-sm font-medium">
          {answer}
        </span>
      </div>
    </motion.div>
  );
}
