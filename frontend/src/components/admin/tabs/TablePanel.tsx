import type { ReactNode } from 'react';
import Spinner from '../Spinner';
import Pagination from '../Pagination';

interface TablePanelProps {
  title: string;
  loading: boolean;
  error: string | null;
  page: number;
  pageSize: number;
  total: number;
  itemsOnPage: number;
  onPrev: () => void;
  onNext: () => void;
  children: ReactNode;
  empty?: string;
}

/**
 * Shared white card + pagination footer used by every table tab.
 *
 * Tabs pass their own ``<table>`` element as children; this component
 * owns only the wrapper, loading/error/empty states, and the legacy
 * pagination strip.
 */
export default function TablePanel({
  title,
  loading,
  error,
  page,
  pageSize,
  total,
  itemsOnPage,
  onPrev,
  onNext,
  children,
  empty = 'Geen rijen.',
}: TablePanelProps) {
  return (
    <div className="bg-white border border-slate-100 artifact-shadow rounded-2xl overflow-hidden">
      <div className="px-5 py-4 border-b border-slate-100 flex items-center justify-between">
        <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
          {title}
        </span>
        <span className="text-xs text-slate-400 tabular-nums">{total} totaal</span>
      </div>

      <div className="custom-scrollbar overflow-x-auto">
        {loading ? (
          <div className="py-16">
            <Spinner label="Laden" />
          </div>
        ) : error ? (
          <div className="p-6 text-sm text-rose-700 bg-rose-50">{error}</div>
        ) : itemsOnPage === 0 ? (
          <div className="py-16 text-center text-sm text-slate-400">{empty}</div>
        ) : (
          children
        )}
      </div>

      <Pagination
        page={page}
        pageSize={pageSize}
        total={total}
        itemsOnPage={itemsOnPage}
        onPrev={onPrev}
        onNext={onNext}
        disabled={loading}
      />
    </div>
  );
}
