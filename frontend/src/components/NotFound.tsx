import { Link } from 'react-router-dom';
import { motion } from 'motion/react';

interface NotFoundProps {
  /** Optional subtitle override; defaults to the generic page-not-found message. */
  subtitle?: string;
}

/**
 * Shared 404 page used for every unauthorized / nonexistent route in the
 * SPA. Huge ghosted "404" fills the visual void so the page reads as a
 * deliberate state rather than a blank canvas; small headline + button
 * keep it scannable.
 */
export default function NotFound({ subtitle }: NotFoundProps) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-white px-6 overflow-hidden">
      <motion.div
        initial={{ opacity: 0, y: 8 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.35 }}
        className="relative flex flex-col items-center text-center"
      >
        <div
          aria-hidden="true"
          className="select-none font-black text-slate-100 leading-none tracking-tighter"
          style={{ fontSize: 'min(28vw, 360px)' }}
        >
          404
        </div>
        <div className="absolute inset-0 flex flex-col items-center justify-center pt-2">
          <p className="text-[11px] font-bold uppercase tracking-[0.25em] text-slate-400 mb-3">
            Niet gevonden
          </p>
          <h1 className="text-2xl sm:text-3xl font-bold text-slate-900 tracking-tight mb-3 max-w-md">
            Deze pagina bestaat niet
          </h1>
          <p className="text-sm text-slate-500 max-w-sm mb-8">
            {subtitle ?? 'De link is verlopen, de pagina is verplaatst, of je hebt geen toegang. Keer terug naar de assistent.'}
          </p>
          <Link
            to="/"
            className="px-6 py-3 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold uppercase tracking-wider transition-all"
          >
            Naar de assistent
          </Link>
        </div>
      </motion.div>
    </div>
  );
}
