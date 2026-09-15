import { ChevronLeft, ChevronRight } from 'lucide-react';

interface PaginationProps {
  page: number;
  pageSize: number;
  total: number;
  itemsOnPage: number;
  onPrev: () => void;
  onNext: () => void;
  disabled?: boolean;
}

/**
 * "← Vorige · Pagina N · X van de 50 op deze pagina · Volgende →"
 *
 * Matches the legacy footer copy verbatim — same words, same separators.
 */
export default function Pagination({
  page,
  pageSize,
  total,
  itemsOnPage,
  onPrev,
  onNext,
  disabled,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = !disabled && page > 1;
  const canNext = !disabled && page < totalPages;
  return (
    <div className="flex items-center justify-between px-4 py-3 text-xs text-slate-500 border-t border-slate-100">
      <button
        type="button"
        onClick={onPrev}
        disabled={!canPrev}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-transparent"
      >
        <ChevronLeft className="h-3.5 w-3.5" />
        Vorige
      </button>
      <span className="flex items-center gap-2">
        <span>Pagina {page}</span>
        <span className="text-slate-300">·</span>
        <span>
          {itemsOnPage} van de {pageSize} op deze pagina
        </span>
      </span>
      <button
        type="button"
        onClick={onNext}
        disabled={!canNext}
        className="inline-flex items-center gap-1 rounded-md px-2 py-1 font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40 disabled:hover:bg-transparent"
      >
        Volgende
        <ChevronRight className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
