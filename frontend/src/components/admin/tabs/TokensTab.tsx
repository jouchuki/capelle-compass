import { useCallback } from 'react';
import { fetchAdminTokens, type AdminTokenRow } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatInt } from '../../../utils/adminFormat';
import TablePanel from './TablePanel';

export default function TokensTab() {
  const fetcher = useCallback((page: number) => fetchAdminTokens(page), []);
  const { items, total, page, pageSize, loading, error, next, prev } =
    usePagedFetch<AdminTokenRow>(fetcher);

  return (
    <TablePanel
      title="Tokens"
      loading={loading}
      error={error}
      page={page}
      pageSize={pageSize}
      total={total}
      itemsOnPage={items.length}
      onPrev={prev}
      onNext={next}
    >
      <table className="w-full text-sm">
        <thead className="bg-slate-50/50">
          <tr className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
            <th className="px-5 py-3 text-left">Gebruiker</th>
            <th className="px-5 py-3 text-right">Analyses</th>
            <th className="px-5 py-3 text-right">Input tokens</th>
            <th className="px-5 py-3 text-right">Output tokens</th>
            <th className="px-5 py-3 text-right">Cache tokens</th>
            <th className="px-5 py-3 text-right">Totaal</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((t) => (
            <tr key={t.user_id} className="hover:bg-slate-50/30">
              <td className="px-5 py-3 text-slate-700 whitespace-nowrap">{t.email}</td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-900">
                {formatInt(t.analyses)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(t.input_tokens)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(t.output_tokens)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(t.cache_tokens)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums font-semibold text-slate-900">
                {formatInt(t.total_tokens)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
