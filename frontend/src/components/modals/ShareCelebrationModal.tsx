import { useEffect, useState } from 'react';
import { Copy, Check, Loader2, Sparkles } from 'lucide-react';
import { motion } from 'motion/react';
import { toast } from 'sonner';
import ModalShell from './ModalShell';
import { createForkLink, ApiError } from '../../api/client';

export interface ShareCelebrationModalProps {
  open: boolean;
  onClose: () => void;
  /** Session whose analysis is being shared — passed straight to ``createForkLink``. */
  sessionId: string | null;
}

/**
 * Light celebration prompt that appears after an analysis completes, asking
 * the user if they want to mint a shareable read-only link. On confirm we
 * call ``createForkLink`` and swap the body to a copy-to-clipboard panel.
 * The sparkle decoration is intentionally subtle — this is municipal
 * software, not a consumer growth loop.
 */
export default function ShareCelebrationModal({
  open,
  onClose,
  sessionId,
}: ShareCelebrationModalProps) {
  const [url, setUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);

  useEffect(() => {
    if (open) {
      setUrl(null);
      setError(null);
      setBusy(false);
      setCopied(false);
    }
  }, [open]);

  const handleCreate = async () => {
    if (!sessionId || busy) return;
    setBusy(true);
    setError(null);
    try {
      const link = await createForkLink(sessionId);
      setUrl(link.url);
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? typeof err.detail === 'string'
            ? err.detail
            : err.message
          : 'Het maken van de link is mislukt. Probeer het opnieuw.';
      setError(msg);
    } finally {
      setBusy(false);
    }
  };

  const handleCopy = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      toast.success('Link gekopieerd naar klembord');
      window.setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard API rejected (likely insecure context). Fall back to
      // a manual select — the input remains visible for the user to
      // copy by hand. No user-facing error needed.
    }
  };

  return (
    <ModalShell open={open} onClose={onClose} size="md" ariaLabel="Deel je analyse">
      <div className="relative">
        {/* Tasteful sparkle accents — three small motion dots behind the headline. */}
        <div className="pointer-events-none absolute -top-2 right-0 flex gap-2 opacity-60">
          {[0, 1, 2].map((idx) => (
            <motion.span
              key={idx}
              initial={{ opacity: 0, scale: 0.6 }}
              animate={{ opacity: [0, 1, 0], scale: [0.6, 1, 0.6] }}
              transition={{
                duration: 2.4,
                repeat: Infinity,
                delay: idx * 0.5,
              }}
              className="block w-1.5 h-1.5 rounded-full bg-blue-400"
            />
          ))}
        </div>

        <p className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400 flex items-center gap-2">
          <Sparkles className="w-3 h-3" aria-hidden="true" />
          Deel je analyse
        </p>

        {url ? (
          <>
            <h2 className="mt-3 text-xl font-bold text-slate-900 tracking-tight">Link gekopieerd!</h2>
            <p className="mt-3 text-sm text-slate-500">
              Plak de link in een mail of chat — je collega kan de analyse direct openen en overnemen.
            </p>
            <div className="mt-6 flex items-center gap-2">
              <input
                type="text"
                readOnly
                value={url}
                aria-label="Deelbare link"
                onFocus={(event) => event.currentTarget.select()}
                className="flex-1 bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 text-sm text-slate-700 font-mono focus:outline-none focus:ring-2 focus:ring-blue-100"
              />
              <button
                type="button"
                onClick={handleCopy}
                aria-label="Kopieer link"
                className="inline-flex items-center justify-center w-11 h-11 rounded-lg bg-blue-600 hover:bg-blue-700 text-white transition-all"
              >
                {copied ? <Check className="w-4 h-4" /> : <Copy className="w-4 h-4" />}
              </button>
            </div>
            <div className="mt-6 flex justify-end">
              <button
                type="button"
                onClick={onClose}
                className="bg-slate-50 hover:bg-slate-100 text-slate-700 rounded-lg px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all"
              >
                Sluiten
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 className="mt-3 text-xl font-bold text-slate-900 tracking-tight">
              Heb je een interessante analyse?
            </h2>
            <p className="mt-3 text-sm text-slate-500 leading-relaxed">
              Deel hem met een collega — die kan hem direct openen en overnemen.
            </p>

            {error ? <p className="mt-4 text-[11px] text-red-600">{error}</p> : null}

            <div className="mt-8 flex items-center justify-end gap-3">
              <button
                type="button"
                onClick={onClose}
                className="px-4 py-2.5 text-xs font-bold uppercase tracking-wider text-slate-500 hover:text-slate-900 transition-colors"
              >
                Misschien later
              </button>
              <button
                type="button"
                onClick={handleCreate}
                disabled={busy || !sessionId}
                className="inline-flex items-center justify-center gap-2 bg-blue-600 hover:bg-blue-700 text-white rounded-lg px-5 py-2.5 text-xs font-bold uppercase tracking-wider transition-all disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Maak deelbare link'}
              </button>
            </div>
          </>
        )}
      </div>
    </ModalShell>
  );
}
