import type { ToolOutput } from '../../types';
import { formatCell, toTableRows } from '../../utils/adapt';

interface ResultTableProps {
  output: ToolOutput;
  /** Optional small badge displayed in the top-right corner (e.g. "Kaartweergave volgt"). */
  cornerBadge?: string;
}

/**
 * Compact data table — mirrors the reference's table style: rounded border,
 * slate-50/50 header, tracking-widest uppercase column labels, hover slate-50/30.
 */
export default function ResultTable({ output, cornerBadge }: ResultTableProps) {
  const rows = toTableRows(output);
  const columns = output.columns;

  if (columns.length === 0 || rows.length === 0) {
    return (
      <div className="text-xs text-slate-400 italic px-4 py-3">
        Geen gegevens beschikbaar.
      </div>
    );
  }

  return (
    <div className="relative overflow-x-auto border border-slate-100 rounded-xl">
      {cornerBadge ? (
        <div className="absolute top-3 right-3 z-10 px-2 py-1 rounded-full text-[10px] font-bold uppercase tracking-wider bg-slate-50 text-slate-500 border border-slate-100">
          {cornerBadge}
        </div>
      ) : null}
      <table className="min-w-full text-left border-collapse">
        <thead>
          <tr className="bg-slate-50/50 border-b border-slate-100">
            {columns.map((col) => (
              <th
                key={col.key}
                className="px-6 py-4 text-[11px] font-bold text-slate-400 uppercase tracking-widest"
              >
                {col.label}
                {col.unit ? (
                  <span className="ml-1 text-slate-300 normal-case tracking-normal">({col.unit})</span>
                ) : null}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-50">
          {rows.map((row, idx) => (
            <tr key={idx} className="hover:bg-slate-50/30 transition-colors">
              {columns.map((col) => (
                <td
                  key={col.key}
                  className="px-6 py-3 text-sm font-medium text-slate-600 whitespace-nowrap"
                >
                  {formatCell(row[col.key] ?? '', col)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
