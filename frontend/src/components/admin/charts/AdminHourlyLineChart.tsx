import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import ChartTooltip from './ChartTooltip';

interface HourlyDatum {
  hour: string;
  value: number;
}

interface AdminHourlyLineChartProps {
  data: HourlyDatum[];
  color?: string;
  seriesLabel?: string;
  height?: number;
}

const AXIS_TICK = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;

/**
 * Thin variant of ``AdminLineChart`` keyed on an hourly bucket label
 * (e.g. ``"14:00"``) instead of an ISO date. Used by the Codex refresh
 * failures chart on the Overzicht tab, where the rolling window is 24
 * hourly buckets rather than 30 daily ones.
 */
function formatHourTick(hour: string): string {
  // Accept either "14:00" → "14:00" or an ISO string → "14u".
  if (/^\d{2}:\d{2}$/.test(hour)) return hour;
  const d = new Date(hour);
  if (Number.isNaN(d.getTime())) return hour;
  return `${String(d.getHours()).padStart(2, '0')}u`;
}

export default function AdminHourlyLineChart({
  data,
  color = '#dc2626',
  seriesLabel = 'Mislukkingen',
  height = 240,
}: AdminHourlyLineChartProps) {
  if (data.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-xs text-slate-400"
        style={{ height }}
      >
        Geen data
      </div>
    );
  }
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="hour"
          tickFormatter={formatHourTick}
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
          width={36}
          allowDecimals={false}
        />
        <Tooltip
          content={(props) => <ChartTooltip {...props} />}
          cursor={{ stroke: '#e2e8f0', strokeWidth: 1 }}
        />
        <Line
          type="monotone"
          dataKey="value"
          name={seriesLabel}
          stroke={color}
          strokeWidth={2}
          dot={{ r: 3, fill: color, stroke: 'white', strokeWidth: 1 }}
          activeDot={{ r: 5 }}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
