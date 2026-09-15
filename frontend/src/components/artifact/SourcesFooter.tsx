import SourceBadge from './SourceBadge';

interface SourcesFooterProps {
  sources: string[];
}

/**
 * Deduplicated chip-row of every source touched by the analysis, displayed
 * under a small "GERAADPLEEGDE BRONNEN" eyebrow at the bottom of the report.
 */
export default function SourcesFooter({ sources }: SourcesFooterProps) {
  const unique = Array.from(new Set(sources));
  if (unique.length === 0) return null;

  return (
    <footer className="mt-20 pt-10 border-t border-slate-100">
      <h2 className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.2em] mb-6">
        Geraadpleegde Bronnen
      </h2>
      <div className="flex flex-wrap gap-3">
        {unique.map((source) => (
          <SourceBadge key={source} source={source} />
        ))}
      </div>
    </footer>
  );
}
