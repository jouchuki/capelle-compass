import { useCallback } from 'react';
import { fetchAdminUsers, type AdminUserRow } from '../adminFetch';
import { usePagedFetch } from '../usePagedFetch';
import { formatDateNL, formatDateTimeNL, formatInt } from '../../../utils/adminFormat';
import TablePanel from './TablePanel';
import DailyLimitCell from './DailyLimitCell';

export default function UsersTab() {
  const fetcher = useCallback((page: number) => fetchAdminUsers(page), []);
  const { items, total, page, pageSize, loading, error, next, prev, patchItems } =
    usePagedFetch<AdminUserRow>(fetcher);

  return (
    <TablePanel
      title="Gebruikers"
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
            <th className="px-5 py-3 text-left">Email</th>
            <th className="px-5 py-3 text-left">Aangemaakt</th>
            <th className="px-5 py-3 text-left">Admin</th>
            <th className="px-5 py-3 text-right">Sessies</th>
            <th className="px-5 py-3 text-right">Berichten</th>
            <th className="px-5 py-3 text-right">Voltooid</th>
            <th className="px-5 py-3 text-left">Laatst actief</th>
            <th className="px-5 py-3 text-left w-72">Dagelijkse limiet</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {items.map((u) => (
            <tr key={u.user_id} className="hover:bg-slate-50/30 align-top">
              <td className="px-5 py-3 text-slate-900 whitespace-nowrap">{u.email}</td>
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateNL(u.created_at)}
              </td>
              <td className="px-5 py-3">
                {u.is_admin ? (
                  <span className="inline-flex items-center rounded-full border border-blue-100 bg-blue-50 px-2.5 py-0.5 text-[11px] font-semibold text-blue-700">
                    Admin
                  </span>
                ) : (
                  <span className="text-xs text-slate-300">—</span>
                )}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(u.sessions_count)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-700">
                {formatInt(u.messages_count)}
              </td>
              <td className="px-5 py-3 text-right tabular-nums text-slate-900">
                {formatInt(u.analyses_completed)}
              </td>
              <td className="px-5 py-3 text-slate-500 whitespace-nowrap">
                {formatDateTimeNL(u.last_active)}
              </td>
              <td className="px-5 py-3">
                <DailyLimitCell
                  userId={u.user_id}
                  value={u.daily_limit_override}
                  onSaved={(newValue) => {
                    patchItems((current) =>
                      current.map((row) =>
                        row.user_id === u.user_id
                          ? { ...row, daily_limit_override: newValue }
                          : row,
                      ),
                    );
                  }}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </TablePanel>
  );
}
