import { lazy, Suspense } from 'react';
import {
  ResponsiveContainer,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
  Legend,
  LineChart,
  Line,
  AreaChart,
  Area,
  PieChart,
  Pie,
  Cell,
  ScatterChart,
  Scatter,
  Treemap,
} from 'recharts';
import type { ChartSpec, ToolOutput } from '../../types';
import { validateChartSpec } from '../../utils/chartContracts';
import {
  toPieData,
  toScatterData,
  toHistogramData,
  toDistributionValues,
} from '../../utils/blockAdapt';
import {
  toBarChartData,
  toLineChartData,
  toTreemapData,
} from '../../utils/adapt';
import ResultTable from './ResultTable';

const StatChart = lazy(() => import('./charts/StatChart'));

const BLUE = '#2563EB';
const PALETTE = ['#2563EB', '#93C5FD', '#1e40af', '#3b82f6', '#60a5fa', '#1d4ed8'];
const TICK = { fontSize: 10, fontWeight: 700, fill: '#94a3b8' } as const;

/** Build a ToolOutput-shaped object so we can reuse ResultTable + adapt mappers. */
function asToolOutput(spec: ChartSpec): ToolOutput {
  return {
    tool: 'platform',
    query: spec.title ?? '',
    result_type: 'table',
    data: spec.data ?? [],
    columns: spec.columns ?? [],
    chart_hints:
      spec.x !== undefined && spec.y !== undefined
        ? [{ type: 'bar', x: spec.x, y: spec.y, title: spec.title ?? '' }]
        : undefined,
  };
}

function yKeys(spec: ChartSpec): string[] {
  if (spec.y === undefined) return [];
  return Array.isArray(spec.y) ? spec.y : [spec.y];
}

export default function ChartRenderer({ spec }: { spec: ChartSpec }) {
  const result = validateChartSpec(spec);
  if (!result.ok) {
    return <ResultTable output={asToolOutput(spec)} cornerBadge="Tabelweergave" />;
  }

  const hint = { type: 'bar' as const, x: spec.x ?? '', y: spec.y ?? '', title: spec.title ?? '' };

  switch (spec.kind) {
    case 'bar': {
      const data = toBarChartData(asToolOutput(spec), hint);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK} />
              <YAxis axisLine={false} tickLine={false} tick={TICK} />
              <Tooltip cursor={{ fill: '#f8fafc' }} />
              <Bar dataKey="value" fill={BLUE} radius={[2, 2, 0, 0]} barSize={24} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'grouped_bar':
    case 'stacked_bar': {
      const data = toLineChartData(asToolOutput(spec), hint);
      const keys = yKeys(spec);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK} />
              <YAxis axisLine={false} tickLine={false} tick={TICK} />
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {keys.map((k, i) => (
                <Bar
                  key={k}
                  dataKey={k}
                  stackId={spec.kind === 'stacked_bar' ? 'a' : undefined}
                  fill={PALETTE[i % PALETTE.length] ?? BLUE}
                  radius={[2, 2, 0, 0]}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'line': {
      const data = toLineChartData(asToolOutput(spec), hint);
      const keys = yKeys(spec);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK} />
              <YAxis axisLine={false} tickLine={false} tick={TICK} />
              <Tooltip />
              {keys.length > 1 ? <Legend wrapperStyle={{ fontSize: 11 }} /> : null}
              {keys.map((k, i) => (
                <Line
                  key={k}
                  type="monotone"
                  dataKey={k}
                  stroke={PALETTE[i % PALETTE.length] ?? BLUE}
                  dot={false}
                  strokeWidth={2}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'area': {
      const data = toLineChartData(asToolOutput(spec), hint);
      const key = yKeys(spec)[0] ?? '';
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <AreaChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" vertical={false} />
              <XAxis dataKey="name" axisLine={false} tickLine={false} tick={TICK} />
              <YAxis axisLine={false} tickLine={false} tick={TICK} />
              <Tooltip />
              <Area type="monotone" dataKey={key} stroke={BLUE} fill={BLUE} fillOpacity={0.2} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'pie':
    case 'donut': {
      const data = toPieData(spec);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="name"
                cx="50%"
                cy="50%"
                outerRadius={110}
                innerRadius={spec.kind === 'donut' ? 60 : 0}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={PALETTE[i % PALETTE.length] ?? BLUE} />
                ))}
              </Pie>
              <Tooltip />
              <Legend wrapperStyle={{ fontSize: 11 }} />
            </PieChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'scatter': {
      const data = toScatterData(spec);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <ScatterChart margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#f1f5f9" />
              <XAxis
                type="number"
                dataKey="x"
                name={spec.x}
                axisLine={false}
                tickLine={false}
                tick={TICK}
              />
              <YAxis
                type="number"
                dataKey="y"
                name={Array.isArray(spec.y) ? (spec.y[0] ?? '') : (spec.y ?? '')}
                axisLine={false}
                tickLine={false}
                tick={TICK}
              />
              <Tooltip cursor={{ strokeDasharray: '3 3' }} />
              <Scatter data={data} fill={BLUE} />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'histogram': {
      const data = toHistogramData(spec);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={data} margin={{ top: 20, right: 30, left: 20, bottom: 40 }}>
              <XAxis
                dataKey="name"
                axisLine={false}
                tickLine={false}
                tick={{ ...TICK, fontSize: 8 }}
                angle={-30}
                textAnchor="end"
              />
              <YAxis axisLine={false} tickLine={false} tick={TICK} />
              <Tooltip cursor={{ fill: '#f8fafc' }} />
              <Bar dataKey="value" fill={BLUE} barSize={18} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      );
    }
    case 'treemap': {
      const data = toTreemapData(asToolOutput(spec), hint);
      return (
        <div data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg">
          <ResponsiveContainer width="100%" height="100%">
            <Treemap data={data} dataKey="size" stroke="#fff" fill={BLUE} />
          </ResponsiveContainer>
        </div>
      );
    }
    case 'boxplot':
    case 'violin':
      return (
        <Suspense
          fallback={<div data-chart-export="1" className="h-[320px] w-full bg-slate-50 rounded-lg" />}
        >
          <StatChart kind={spec.kind} values={toDistributionValues(spec)} title={spec.title} />
        </Suspense>
      );
    default:
      return <ResultTable output={asToolOutput(spec)} cornerBadge="Tabelweergave" />;
  }
}
