import { useState } from 'react';
import { motion } from 'motion/react';
import { HelpCircle, Send } from 'lucide-react';

interface ElicitationCardProps {
  question: string;
  options: string[];
  allowFreeText: boolean;
  onAnswer: (answer: string) => void;
}

/**
 * Mid-run elicitation card shown in the message timeline while the agent
 * awaits a clarifying answer from the user.
 *
 * Renders the agent's question, one button per provided option, and (when
 * allowFreeText is true) a text input + submit button. Calls onAnswer with
 * the chosen value, then disables further input.
 */
export default function ElicitationCard({
  question,
  options,
  allowFreeText,
  onAnswer,
}: ElicitationCardProps) {
  const [answered, setAnswered] = useState<boolean>(false);
  const [freeText, setFreeText] = useState<string>('');

  function handleChoose(value: string): void {
    if (answered) return;
    setAnswered(true);
    onAnswer(value);
  }

  function handleSubmitFreeText(): void {
    const trimmed = freeText.trim();
    if (!trimmed || answered) return;
    setAnswered(true);
    onAnswer(trimmed);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'Enter') {
      handleSubmitFreeText();
    }
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3 }}
      className="mt-4 p-6 bg-white border border-blue-100 rounded-xl artifact-shadow"
    >
      <div className="flex items-start gap-4">
        <div className="w-10 h-10 bg-blue-50 rounded-full flex items-center justify-center text-blue-600 flex-shrink-0 mt-0.5">
          <HelpCircle className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0 space-y-4">
          <div>
            <p className="text-[10px] uppercase tracking-wider font-bold text-blue-600 mb-2">
              Vraag van de agent
            </p>
            <p className="text-sm text-slate-800 font-medium leading-relaxed">{question}</p>
          </div>

          {options.length > 0 ? (
            <div className="space-y-2">
              {options.map((opt) => (
                <button
                  key={opt}
                  type="button"
                  disabled={answered}
                  onClick={() => handleChoose(opt)}
                  className="w-full text-left px-4 py-3 rounded-lg bg-slate-50 hover:bg-blue-50 border border-transparent hover:border-blue-100 text-sm text-slate-700 hover:text-blue-700 font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {opt}
                </button>
              ))}
            </div>
          ) : null}

          {allowFreeText ? (
            <div className="flex gap-2">
              <input
                type="text"
                value={freeText}
                disabled={answered}
                onChange={(e) => setFreeText(e.target.value)}
                onKeyDown={handleKeyDown}
                placeholder="Typ uw antwoord..."
                className="flex-1 px-4 py-2.5 rounded-lg border border-slate-200 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent disabled:opacity-50 disabled:cursor-not-allowed"
              />
              <button
                type="button"
                disabled={answered || freeText.trim() === ''}
                onClick={handleSubmitFreeText}
                className="flex items-center gap-2 px-4 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-sm font-medium transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Send className="w-4 h-4" />
              </button>
            </div>
          ) : null}

          {answered ? (
            <p className="text-[11px] text-slate-400 font-medium">Antwoord verzonden.</p>
          ) : null}
        </div>
      </div>
    </motion.div>
  );
}
