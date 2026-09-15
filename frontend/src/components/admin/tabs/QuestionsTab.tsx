import { useCallback } from 'react';
import { fetchAdminQuestions, type AdminQuestionRow } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatDateTimeNL } from '../../../utils/adminFormat';
import TopicChip from '../TopicChip';
import TablePanel from './TablePanel';

export default function QuestionsTab() {
  const fetcher = useCallback((page: number) => fetchAdminQuestions(page), []);
  const { items, total, page, pageSize, loading, error, next, prev } =
    usePagedFetch<AdminQuestionRow>(fetcher);

  return (
    <TablePanel
      title="Vragen"
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
            <th className="px-5 py-3 text-left">Onderwerp</th>
            <th className="px-5 py-3 text-left">Vraag</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((q) => (
            <tr key={q.id} className="hover:bg-slate-50/30">
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(q.created_at)}
              </td>
              <td className="px-5 py-3 text-slate-700 whitespace-nowrap">{q.user_email}</td>
              <td className="px-5 py-3">
                <TopicChip topic={q.topic} />
              </td>
              <td className="px-5 py-3 text-slate-900">{q.question}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
