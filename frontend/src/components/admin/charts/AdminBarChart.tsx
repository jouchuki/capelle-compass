import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import ChartTooltip from './ChartTooltip';
import { formatChartTick } from '../../../utils/adminFormat';

interface AdminBarChartProps {
  data: Array<{ date: string; value: number }>;
  color?: string;
  seriesLabel?: string;
  height?: number;
}

const AXIS_TICK = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;

/** Single-series bar chart used for daily count KPIs on the Overzicht tab. */
export default function AdminBarChart({
  data,
  color = '#2563eb',
  seriesLabel = 'Waarde',
  height = 240,
}: AdminBarChartProps) {
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
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
        <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={formatChartTick}
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
        />
        <YAxis axisLine={false} tickLine={false} tick={AXIS_TICK} width={36} />
        <Tooltip
          content={(props) => <ChartTooltip {...props} />}
          cursor={{ fill: '#f1f5f9', opacity: 0.5 }}
        />
        <Bar dataKey="value" name={seriesLabel} fill={color} radius={[4, 4, 0, 0]} />
      </BarChart>
    </ResponsiveContainer>
  );
}
