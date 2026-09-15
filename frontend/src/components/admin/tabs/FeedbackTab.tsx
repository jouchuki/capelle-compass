import { useCallback } from 'react';
import { fetchAdminFeedback, type AdminFeedbackRow } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatDateTimeNL } from '../../../utils/adminFormat';
import TopicChip from '../TopicChip';
import StatusChip from '../StatusChip';
import TablePanel from './TablePanel';

export default function FeedbackTab() {
  const fetcher = useCallback((page: number) => fetchAdminFeedback(page), []);
  const { items, total, page, pageSize, loading, error, next, prev } =
    usePagedFetch<AdminFeedbackRow>(fetcher);

  return (
    <TablePanel
      title="Feedback"
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
            <th className="px-5 py-3 text-left">Datum</th>
            <th className="px-5 py-3 text-left">Gebruiker</th>
            <th className="px-5 py-3 text-left">Status</th>
            <th className="px-5 py-3 text-left">Onderwerp</th>
            <th className="px-5 py-3 text-left">Inhoud</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((f) => (
            <tr key={f.id} className="hover:bg-slate-50/30">
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(f.created_at)}
              </td>
              <td className="px-5 py-3 text-slate-700 whitespace-nowrap">{f.user_email}</td>
              <td className="px-5 py-3">
                <StatusChip status={f.status} />
              </td>
              <td className="px-5 py-3">
                <TopicChip topic={f.topic} />
              </td>
              <td className="px-5 py-3 text-slate-900">{f.content}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
