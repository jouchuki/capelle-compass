import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  LineChart,
  Line,
  CartesianGrid,
  Treemap,
  Legend,
  ComposedChart,
  Area,
} from 'recharts';
import type { ChartHint, ToolOutput } from '../../types';
import {
  toBarChartData,
  toForecastChartData,
  toLineChartData,
  toTreemapData,
} from '../../utils/adapt';
import ResultTable from './ResultTable';

interface ResultChartProps {
  output: ToolOutput;
  hint: ChartHint;
}

const BLUE = '#2563EB';
const BLUE_SOFT = '#93C5FD';
const TICK_STYLE = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;
const TOOLTIP_STYLE = {
  borderRadius: '12px',
  border: 'none',
  boxShadow: '0 20px 40px -10px rgba(0,0,0,0.1)',
  padding: '12px',
  fontSize: '12px',
} as const;

/**
 * Branches on ChartHint.type and renders the appropriate recharts widget.
 * For unrecognised / unsupported hints we fall back to the plain table.
 */
export default function ResultChart({ output, hint }: ResultChartProps) {
  if (hint.type === 'bar') {
    const data = toBarChartData(output, hint);
    return (
      <div className="h-[320px] w-full bg-white rounded-lg">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <YAxis axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <Tooltip cursor={{ fill: '#f8fafc' }} contentStyle={TOOLTIP_STYLE} />
            <Bar dataKey="value" fill={BLUE} radius={[2, 2, 0, 0]} barSize={24} opacity={0.9} />
          </BarChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (hint.type === 'line') {
    const data = toLineChartData(output, hint);
    const yKeys = Array.isArray(hint.y) ? hint.y : [hint.y];
    const colors = [BLUE, BLUE_SOFT, '#1e40af', '#3b82f6'];
    return (
      <div className="h-[320px] w-full bg-white rounded-lg">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <YAxis axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            {yKeys.length > 1 ? <Legend wrapperStyle={{ fontSize: 11 }} /> : null}
            {yKeys.map((key, idx) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                stroke={colors[idx % colors.length] ?? BLUE}
                strokeWidth={2.5}
                dot={{ r: 3, fill: colors[idx % colors.length] ?? BLUE }}
                activeDot={{ r: 5 }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    );
  }

  if (hint.type === 'treemap') {
    const data = toTreemapData(output, hint);
    return (
      <div className="h-[320px] w-full bg-white rounded-lg">
        <ResponsiveContainer width="100%" height="100%">
          <Treemap
            data={data}
            dataKey="size"
            stroke="#fff"
            fill={BLUE}
            isAnimationActive={false}
          />
        </ResponsiveContainer>
      </div>
    );
  }

  if (hint.type === 'map') {
    return <ResultTable output={output} cornerBadge="Kaartweergave volgt" />;
  }

  if (hint.type === 'forecast') {
    const { rows, groups } = toForecastChartData(output, hint);
    // Palette balances three default jeugdzorg dimensions (≤8 values each)
    // — extend if a future dataset has more.
    const palette = [
      '#2563EB', '#059669', '#D97706', '#7C3AED',
      '#DB2777', '#0891B2', '#65A30D', '#DC2626',
    ];
    return (
      <div className="h-[360px] w-full bg-white rounded-lg">
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={rows} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
            <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <YAxis axisLine={false} tickLine={false} tick={TICK_STYLE} />
            <Tooltip contentStyle={TOOLTIP_STYLE} />
            {groups.length > 1 ? <Legend wrapperStyle={{ fontSize: 11 }} /> : null}
            {groups.map((group, idx) => {
              const color = palette[idx % palette.length] ?? BLUE;
              return (
                <Area
                  key={`${group}_band`}
                  type="monotone"
                  // Function dataKey returns [lower, upper] so recharts draws
                  // a range band rather than an area from the X-axis. The
                  // historical points carry zero-width intervals, so the band
                  // visually starts where the forecast does.
                  dataKey={(d: Record<string, number>) => [
                    d[`${group}_lower`],
                    d[`${group}_upper`],
                  ]}
                  name={`${group} 95% PI`}
                  stroke="none"
                  fill={color}
                  fillOpacity={0.14}
                  isAnimationActive={false}
                  legendType="none"
                />
              );
            })}
            {groups.map((group, idx) => {
              const color = palette[idx % palette.length] ?? BLUE;
              return (
                <Line
                  key={`${group}_pred`}
                  type="monotone"
                  dataKey={`${group}_pred`}
                  name={group}
                  stroke={color}
                  strokeWidth={2.5}
                  dot={{ r: 3, fill: color }}
                  activeDot={{ r: 5 }}
                  isAnimationActive={false}
                />
              );
            })}
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    );
  }

  // table or anything we don't know
  return <ResultTable output={output} />;
}
