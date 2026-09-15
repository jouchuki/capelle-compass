import { useCallback } from 'react';
import { fetchAdminEngagement, type AdminEngagementRow } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatInt, formatDateTimeNL } from '../../../utils/adminFormat';
import TablePanel from './TablePanel';

export default function EngagementTab() {
  const fetcher = useCallback((page: number) => fetchAdminEngagement(page), []);
  const { items, total, page, pageSize, loading, error, next, prev } =
    usePagedFetch<AdminEngagementRow>(fetcher);

  return (
    <TablePanel
      title="Engagement"
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
            <th className="px-5 py-3 text-right">Sessies</th>
            <th className="px-5 py-3 text-right">Berichten</th>
            <th className="px-5 py-3 text-right">Voltooid</th>
            <th className="px-5 py-3 text-right">Mislukt</th>
            <th className="px-5 py-3 text-right">Deel-links</th>
            <th className="px-5 py-3 text-right">Feedback</th>
            <th className="px-5 py-3 text-right">Quota-hits</th>
            <th className="px-5 py-3 text-left">Eerst gezien</th>
            <th className="px-5 py-3 text-left">Laatst actief</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((row) => (
            <tr key={row.user_id} className="hover:bg-slate-50/30">
              <td className="px-5 py-3 text-slate-700 whitespace-nowrap">{row.email}</td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-900">
                {formatInt(row.sessions_created)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(row.messages_sent)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-emerald-700">
                {formatInt(row.analyses_completed)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-rose-600">
                {formatInt(row.analyses_failed)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(row.share_links_created)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(row.feedback_submitted)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-amber-700">
                {formatInt(row.quota_limit_hits)}
              </td>
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(row.first_seen)}
              </td>
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(row.last_active)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
