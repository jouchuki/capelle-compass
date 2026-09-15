import { useEffect, useState, type FormEvent } from 'react';
import { Loader2 } from 'lucide-react';
import { toast } from 'sonner';
import ModalShell from './ModalShell';
import { submitFeedback, ApiError, type FeedbackTopic } from '../../api/client';

export interface FeedbackModalProps {
  open: boolean;
  onClose: () => void;
  /** Preselect a topic chip (e.g. "more_usage" when opened from the quota modal). */
  initialTopic?: FeedbackTopic;
}

const TOPICS: Array<{ value: FeedbackTopic; label: string }> = [
  { value: 'bug', label: 'Bug' },
  { value: 'suggestion', label: 'Suggestie' },
  { value: 'more_usage', label: 'Meer gebruik' },
  { value: 'other', label: 'Anders' },
];

/**
 * Feedback capture. Four topic chips + a textarea + send/cancel. On success
 * the form swaps to a calm "BEDANKT" panel that auto-dismisses after 2.5s.
 * Errors render inline above the footer — no toast plumbing required.
 */
export default function FeedbackModal({
  open,
  onClose,
  initialTopic = 'suggestion',
}: FeedbackModalProps) {
  const [topic, setTopic] = useState<FeedbackTopic>(initialTopic);
  const [content, setContent] = useState<string>('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState<boolean>(false);
  const [sent, setSent] = useState<boolean>(false);

  // Reset state every time the modal opens — preselect the caller's topic
  // and clear out any leftover content from a previous session.
  useEffect(() => {
    if (open) {
      setTopic(initialTopic);
      setContent('');
      setError(null);
      setSent(false);
      setSubmitting(false);
    }
  }, [open, initialTopic]);

  // Auto-close after a short celebration window so the user isn't
  // forced to click a second time.
  useEffect(() => {
    if (!sent) return;
    const id = window.setTimeout(() => {
      onClose();
    }, 2500);
    return () => {
      window.clearTimeout(id);
    };
  }, [sent, onClose]);

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!content.trim() || submitting) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback(topic, content.trim());
      setSent(true);
      toast.success('Feedback verstuurd — bedankt!');
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? typeof err.detail === 'string'
            ? err.detail
            : err.message
          : 'Versturen mislukt. Probeer het later opnieuw.';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <ModalShell open={open} onClose={onClose} size="md" ariaLabel="Feedback versturen">
      {sent ? (
        <div className="text-center py-4">
          <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">Bedankt</p>
          <h2 className="mt-4 text-2xl font-bold text-slate-900 tracking-tight">
            Je feedback is ontvangen
          </h2>
          <p className="mt-3 text-sm text-slate-500">We lezen elke inzending — bedankt voor je tijd.</p>
        </div>
      ) : (
        <form onSubmit={handleSubmit}>
          <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">Feedback</p>
          <h2 className="mt-3 text-xl font-bold text-slate-900 tracking-tight">Help ons verbeteren</h2>

          <div className="mt-6">
            <p className="text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-3">
              Onderwerp
            </p>
            <div className="flex flex-wrap gap-2">
              {TOPICS.map((option) => {
                const active = option.value === topic;
                return (
                  <button
                    key={option.value}
                    type="button"
                    onClick={() => setTopic(option.value)}
                    aria-pressed={active}
                    className={`px-3 py-1.5 rounded-full text-[11px] font-bold uppercase tracking-wider transition-all border ${
                      active
                        ? 'bg-blue-600 text-white border-blue-600'
                        : 'bg-white text-slate-600 border-slate-200 hover:border-slate-300'
                    }`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
          </div>

          <div className="mt-6">
            <label
              htmlFor="feedback-content"
              className="block text-[11px] font-bold uppercase tracking-wider text-slate-400 mb-2"
            >
              Bericht
            </label>
            <textarea
              id="feedback-content"
              value={content}
              onChange={(event) => setContent(event.target.value)}
              placeholder="Wat kunnen we verbeteren?"
              className="w-full bg-slate-50 border border-slate-200 rounded-lg p-4 text-sm min-h-[140px] focus:bg-white focus:outline-none focus:ring-2 focus:ring-blue-100 focus:border-blue-300 transition-all resize-y placeholder:text-slate-400"
            />
          </div>

          {error ? (
            <p className="mt-4 text-[11px] text-red-600">{error}</p>
          ) : null}

          <div className="mt-6 flex items-center justify-end gap-3">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2.5 text-xs font-bold uppercase tracking-wider text-slate-500 hover:text-slate-900 transition-colors"
            >
              Annuleren
            </button>
            <button
              type="submit"
              disabled={submitting || !content.trim()}
              className="inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Versturen'}
            </button>
          </div>
        </form>
      )}
    </ModalShell>
  );
}
