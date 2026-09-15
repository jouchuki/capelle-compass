import { ArrowRight } from 'lucide-react';

interface FollowUpCardProps {
  questions: string[];
  onAsk: (question: string) => void;
}

/**
 * Renders the agent's suggested follow-up questions as a vertical list of
 * clickable buttons. Clicking a row fires ``onAsk`` so the parent can enqueue
 * a fresh user message and re-run the analysis pipeline.
 */
export default function FollowUpCard({ questions, onAsk }: FollowUpCardProps) {
  if (questions.length === 0) return null;

  return (
    <section className="mb-16">
      <h2 className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.2em] mb-6">
        Vervolgvragen
      </h2>
      <div className="space-y-2">
        {questions.map((q, idx) => (
          <button
            key={idx}
            type="button"
            onClick={() => onAsk(q)}
            className="group w-full flex items-center justify-between gap-4 px-5 py-4 rounded-xl bg-slate-50 hover:bg-blue-50 text-left transition-colors border border-transparent hover:border-blue-100"
          >
            <span className="text-sm text-slate-700 group-hover:text-blue-700 font-medium">
              {q}
            </span>
            <ArrowRight className="w-4 h-4 text-slate-300 group-hover:text-blue-600 group-hover:translate-x-0.5 transition-all flex-shrink-0" />
          </button>
        ))}
      </div>
    </section>
  );
}
