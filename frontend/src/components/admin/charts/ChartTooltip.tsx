import type { TooltipContentProps } from 'recharts';
import type { NameType, ValueType } from 'recharts/types/component/DefaultTooltipContent';
import { formatInt } from '../../../utils/adminFormat';

/**
 * Recharts custom tooltip: rounded, no border, soft shadow.
 *
 * Values are formatted with the Dutch grouping locale; the label
 * (X-axis date) is passed through verbatim so each chart can decide
 * its own tick format.
 *
 * Kept generic over recharts' default ``ValueType``/``NameType`` so it
 * plugs into any Tooltip without parameter-variance friction at the
 * call site.
 */
export default function ChartTooltip({
  active,
  payload,
  label,
}: TooltipContentProps<ValueType, NameType>) {
  if (!active || !payload || payload.length === 0) return null;
  return (
    <div className="rounded-xl bg-white px-3 py-2 shadow-md text-xs">
      <div className="text-[10px] uppercase tracking-widest font-bold text-slate-400 mb-1">
        {String(label ?? '')}
      </div>
      <div className="flex flex-col gap-0.5">
        {payload.map((p, idx) => {
          const value =
            typeof p.value === 'number' ? formatInt(p.value) : p.value != null ? String(p.value) : '—';
          const seriesName = p.name != null ? String(p.name) : '';
          const color = typeof p.color === 'string' ? p.color : '#2563eb';
          return (
            <div key={`${seriesName}-${idx}`} className="flex items-center gap-2 text-slate-700">
              <span className="h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
              <span className="font-medium">{seriesName}</span>
              <span className="text-slate-500 tabular-nums">{value}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
