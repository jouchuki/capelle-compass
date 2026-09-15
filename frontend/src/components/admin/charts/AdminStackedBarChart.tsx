import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import ChartTooltip from './ChartTooltip';
import { formatChartTick, formatTokensCompact } from '../../../utils/adminFormat';

export interface StackedSeries {
  key: string;
  color: string;
  label: string;
}

interface AdminStackedBarChartProps {
  data: Array<{ date: string } & Record<string, number | string>>;
  series: StackedSeries[];
  height?: number;
}

const AXIS_TICK = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;

/**
 * Stacked bar chart for token-usage breakdown (input/output/cache).
 *
 * The Y-axis uses the compact-token formatter so large values stay
 * legible at small chart heights.
 */
export default function AdminStackedBarChart({
  data,
  series,
  height = 240,
}: AdminStackedBarChartProps) {
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
      <BarChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
        <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="date"
          tickFormatter={formatChartTick}
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
        />
        <YAxis
          axisLine={false}
          tickLine={false}
          tick={AXIS_TICK}
          width={48}
          tickFormatter={(v: number) => formatTokensCompact(v)}
        />
        <Tooltip
          content={(props) => <ChartTooltip {...props} />}
          cursor={{ fill: '#f1f5f9', opacity: 0.5 }}
        />
        <Legend
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 11, fontWeight: 600, color: '#475569', paddingTop: 8 }}
        />
        {series.map((s, idx) => (
          <Bar
            key={s.key}
            dataKey={s.key}
            name={s.label}
            stackId="tokens"
            fill={s.color}
            radius={idx === series.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
          />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}
