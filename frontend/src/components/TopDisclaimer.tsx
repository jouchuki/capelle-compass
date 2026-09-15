import { AlertTriangle } from 'lucide-react';

/**
 * Slim slate bar rendered at the top of the authenticated app shell. Reminds
 * municipal users that LLM output requires human verification before it
 * feeds policy decisions. Intentionally low-contrast so it lives at the
 * edge of attention without crowding the chat surface.
 */
export default function TopDisclaimer() {
  return (
    <div
      role="status"
      className="bg-slate-50 border-b border-slate-100 py-2.5 px-4 text-xs text-slate-600 font-medium flex items-center justify-center gap-2"
    >
      <AlertTriangle className="w-3.5 h-3.5 flex-shrink-0 text-slate-400" aria-hidden="true" />
      <span>
        AI-gegenereerde antwoorden kunnen fouten bevatten. Controleer beleidsbeslissingen altijd met de oorspronkelijke bron.
      </span>
    </div>
  );
}
