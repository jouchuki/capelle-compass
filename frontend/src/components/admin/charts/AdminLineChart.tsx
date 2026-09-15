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
import { formatChartTick } from '../../../utils/adminFormat';

interface AdminLineChartProps {
  data: Array<{ date: string; value: number }>;
  color?: string;
  seriesLabel?: string;
  height?: number;
}

const AXIS_TICK = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;

/** Single-series line chart used for daily-trend KPIs on the Overzicht tab. */
export default function AdminLineChart({
  data,
  color = '#2563eb',
  seriesLabel = 'Waarde',
  height = 240,
}: AdminLineChartProps) {
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
          dataKey="date"
          tickFormatter={formatChartTick}
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
        />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} width={36} />
        <Tooltip content={(props) => <ChartTooltip {...props} />} cursor={{ stroke: '#e2e8f0', strokeWidth: 1 }} />
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
