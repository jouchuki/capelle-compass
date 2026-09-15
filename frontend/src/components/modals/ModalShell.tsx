import { useEffect, type ReactNode, type MouseEvent } from 'react';
import { motion, AnimatePresence } from 'motion/react';

export type ModalSize = 'sm' | 'md' | 'lg';

export interface ModalShellProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** Card max-width. ``sm`` ≈ 24rem, ``md`` ≈ 28rem, ``lg`` ≈ 36rem. */
  size?: ModalSize;
  /** Optional aria-label for the dialog wrapper (screen-reader hint). */
  ariaLabel?: string;
}

const SIZE_CLASS: Record<ModalSize, string> = {
  sm: 'max-w-sm',
  md: 'max-w-md',
  lg: 'max-w-lg',
};

const SPRING = { type: 'spring' as const, damping: 30, stiffness: 200 };

/**
 * Shared backdrop + card primitive for every modal in the app. Owns:
 *   - the slate/blur overlay (click-outside to close)
 *   - Esc-to-close keyboard binding
 *   - motion mount/unmount transitions
 *   - propagation guards so clicks inside the card don't dismiss it
 *
 * Individual modals (Quota / Feedback / Share) just render their own
 * content as children — they shouldn't reimplement these mechanics.
 */
export default function ModalShell({
  open,
  onClose,
  children,
  size = 'md',
  ariaLabel,
}: ModalShellProps) {
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => {
      window.removeEventListener('keydown', onKey);
    };
  }, [open, onClose]);

  const stop = (event: MouseEvent<HTMLDivElement>) => {
    event.stopPropagation();
  };

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          key="modal-backdrop"
          role="dialog"
          aria-modal="true"
          aria-label={ariaLabel}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          onClick={onClose}
          className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 backdrop-blur-sm px-4"
        >
          <motion.div
            key="modal-card"
            initial={{ opacity: 0, y: 10, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 8, scale: 0.98 }}
            transition={SPRING}
            onClick={stop}
            className={`w-full ${SIZE_CLASS[size]} bg-white rounded-2xl border border-slate-100 artifact-shadow p-8`}
          >
            {children}
          </motion.div>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}
