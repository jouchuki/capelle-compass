import type { CitationRef } from '../../types';
import { sourceLabel } from '../../utils/adapt';

interface ReferencesListProps {
  citations: CitationRef[];
}

/**
 * Numbered "Geraadpleegde bronnen" footer. The inline [n] citation markers
 * point at these entries, so each claim is traceable to a specific document
 * (CBS table, beleidsstuk, taakveld, enquête-jaargang, …).
 */
export default function ReferencesList({ citations }: ReferencesListProps) {
  if (citations.length === 0) return null;
  return (
    <footer className="mt-20 pt-10 border-t border-slate-100">
      <h2 className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.2em] mb-6">
        Geraadpleegde Bronnen
      </h2>
      <ol className="space-y-2">
        {citations.map((c, i) => (
          <li key={c.id} className="flex gap-3 text-[13px] leading-snug text-slate-600">
            <span className="text-slate-400 tabular-nums font-bold flex-shrink-0">{i + 1}.</span>
            <span>
              <span className="font-semibold text-slate-700">{sourceLabel(c.source)}</span>
              {' — '}
              {c.document}
              {c.ref ? <span className="font-mono text-[11px] text-slate-400"> {c.ref}</span> : null}
              {c.year ? <span className="text-slate-400"> ({c.year})</span> : null}
              {c.doc_type ? <span className="text-slate-400"> · {c.doc_type}</span> : null}
              {c.source_url ? (
                <>
                  {' · '}
                  <a
                    href={c.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-600 hover:underline"
                  >
                    bron
                  </a>
                </>
              ) : null}
            </span>
          </li>
        ))}
      </ol>
    </footer>
  );
}
