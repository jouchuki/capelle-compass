import { useCallback } from 'react';
import { fetchCodexEvents, type CodexRefreshEvent, type CodexHealthStatus } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatDateTimeNL } from '../../../utils/adminFormat';
import TablePanel from './TablePanel';

const STATUS_LABEL: Record<CodexHealthStatus, string> = {
  in_sync: 'In sync',
  rotated: 'Geroteerd',
  near_expiry: 'Bijna verlopen',
  failed: 'Mislukt',
  auth_json_missing: 'Configuratie ontbreekt',
};

const STATUS_CLASS: Record<CodexHealthStatus, string> = {
  in_sync: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  rotated: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  near_expiry: 'bg-amber-50 text-amber-700 border-amber-100',
  failed: 'bg-rose-50 text-rose-700 border-rose-100',
  auth_json_missing: 'bg-rose-50 text-rose-700 border-rose-100',
};

interface CodexStatusChipProps {
  status: CodexHealthStatus;
}

function CodexStatusChip({ status }: CodexStatusChipProps) {
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${STATUS_CLASS[status]}`}
    >
      {STATUS_LABEL[status]}
    </span>
  );
}

export default function CodexTab() {
  const fetcher = useCallback((page: number) => fetchCodexEvents(page), []);
  const { items, total, page, pageSize, loading, error, next, prev } =
    usePagedFetch<CodexRefreshEvent>(fetcher);

  return (
    <TablePanel
      title="Codex refresh-events"
      loading={loading}
      error={error}
      page={page}
      pageSize={pageSize}
      total={total}
      itemsOnPage={items.length}
      onPrev={prev}
      onNext={next}
      empty="Geen refresh-events geregistreerd."
    >
      <table className="w-full text-sm">
        <thead className="bg-slate-50/50">
          <tr className="text-[11px] font-bold uppercase tracking-widest text-slate-400">
            <th className="px-5 py-3 text-left">Tijdstip</th>
            <th className="px-5 py-3 text-left">Host</th>
            <th className="px-5 py-3 text-left">Status</th>
            <th className="px-5 py-3 text-left">Token-staart</th>
            <th className="px-5 py-3 text-left">Bericht</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((e) => (
            <tr key={e.id} className="hover:bg-slate-50/30">
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(e.created_at)}
              </td>
              <td className="px-5 py-3 text-slate-700 whitespace-nowrap tabular-nums">
                {e.hostname}
              </td>
              <td className="px-5 py-3">
                <CodexStatusChip status={e.status} />
              </td>
              <td className="px-5 py-3 text-slate-500 font-mono tabular-nums">
                {e.access_tail ? `…${e.access_tail}` : '—'}
              </td>
              <td className="px-5 py-3 text-slate-900">{e.message}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
