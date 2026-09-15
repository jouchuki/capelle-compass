import { useMemo, useState } from 'react';
import { MessageSquare } from 'lucide-react';
import ModalShell from './ModalShell';
import FeedbackModal from './FeedbackModal';
import { useQuota } from '../../hooks/useQuota';

export interface QuotaLimitModalProps {
  open: boolean;
  onClose: () => void;
}

/**
 * Surfaced by the parent when a ``sendMessage`` call comes back 429. Reads
 * the live quota snapshot so the "reset om HH:MM" copy stays accurate
 * even if the user lingered on the modal for a while. Offers a feedback
 * shortcut (preselects topic ``more_usage``) for users who routinely
 * bump the daily cap.
 */
export default function QuotaLimitModal({ open, onClose }: QuotaLimitModalProps) {
  const { snapshot } = useQuota();
  const [feedbackOpen, setFeedbackOpen] = useState<boolean>(false);

  const resetsLabel = useMemo(() => {
    if (!snapshot?.resets_at) return null;
    const parsed = new Date(snapshot.resets_at);
    if (Number.isNaN(parsed.getTime())) return null;
    return new Intl.DateTimeFormat('nl-NL', {
      hour: '2-digit',
      minute: '2-digit',
    }).format(parsed);
  }, [snapshot?.resets_at]);

  return (
    <>
      <ModalShell open={open && !feedbackOpen} onClose={onClose} size="md" ariaLabel="Daglimiet bereikt">
        <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
          Dagelijkse limiet bereikt
        </p>
        <h2 className="mt-3 text-xl font-bold text-slate-900 tracking-tight">
          Je hebt de daglimiet voor analyses bereikt
        </h2>
        <p className="mt-3 text-sm text-slate-500 leading-relaxed">
          {resetsLabel
            ? `Je limiet wordt automatisch hersteld om ${resetsLabel}. Daarna kun je weer nieuwe analyses starten.`
            : 'Je limiet wordt automatisch hersteld zodra de dag is afgesloten. Daarna kun je weer nieuwe analyses starten.'}
        </p>

        <div className="mt-8 flex items-center justify-between gap-4">
          <button
            type="button"
            onClick={() => setFeedbackOpen(true)}
            className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-slate-500 hover:text-blue-700 transition-colors"
          >
            <MessageSquare className="w-4 h-4" aria-hidden="true" />
            Stuur feedback over de limiet
          </button>
          <button
            type="button"
            onClick={onClose}
            className="bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all"
          >
            Begrepen
          </button>
        </div>
      </ModalShell>
      <FeedbackModal
        open={feedbackOpen}
        onClose={() => {
          setFeedbackOpen(false);
          onClose();
        }}
        initialTopic="more_usage"
      />
    </>
  );
}
