import { AlertTriangle } from 'lucide-react';

interface DataGapsCardProps {
  gaps: string[];
}

/**
 * Small inline card listing data-gaps reported by the agent — rendered between
 * the report sections and the follow-up questions when the analysis admits
 * gaps in its underlying datasets.
 */
export default function DataGapsCard({ gaps }: DataGapsCardProps) {
  if (gaps.length === 0) return null;

  return (
    <section className="mb-12 rounded-xl border border-amber-100 bg-amber-50/40 p-6">
      <div className="flex items-center gap-2 mb-3">
        <AlertTriangle className="w-4 h-4 text-amber-600" />
        <h2 className="text-[11px] font-bold uppercase tracking-[0.2em] text-amber-700">
          Ontbrekende gegevens
        </h2>
      </div>
      <ul className="space-y-2 text-sm text-slate-700 leading-relaxed">
        {gaps.map((gap, idx) => (
          <li key={idx} className="flex items-start gap-2">
            <span className="text-amber-500 mt-1.5 w-1 h-1 rounded-full bg-amber-500 flex-shrink-0" />
            <span>{gap}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}
