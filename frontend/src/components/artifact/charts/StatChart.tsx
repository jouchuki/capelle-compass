import { useEffect, useRef } from 'react';
import * as echarts from 'echarts';

interface StatChartProps {
  kind: 'boxplot' | 'violin';
  values: number[];
  title?: string;
}

/** Chart palette — matches the app's recharts BLUE / BLUE_SOFT. */
const CHART_COLORS = {
  fill: '#93C5FD',
  border: '#2563EB',
  areaOpacity: 0.6,
} as const;

/** KDE sampling resolution — 40 points renders a smooth violin without visible faceting. */
const DENSITY_SAMPLES = 40;
/** Violin half-width: density is scaled to ±45 around the centerline at 50 (xAxis range 0–100). */
const VIOLIN_CENTER = 50;
const VIOLIN_HALF_WIDTH = 45;

/**
 * Five-number summary for a boxplot.
 * noUncheckedIndexedAccess: all indices are provably in-range (sorted is
 * non-empty — callers must guard before calling), so non-null assertions are safe.
 */
function quartiles(sorted: number[]): [number, number, number, number, number] {
  const q = (p: number): number => {
    const idx = (sorted.length - 1) * p;
    const lo = Math.floor(idx);
    const hi = Math.ceil(idx);
    // lo and hi are always valid indices when sorted is non-empty
    const loVal = sorted[lo]!;
    const hiVal = sorted[hi]!;
    if (lo === hi) return loVal;
    return loVal + (hiVal - loVal) * (idx - lo);
  };
  // Non-null assertions: sorted is non-empty, so first/last indices exist.
  return [sorted[0]!, q(0.25), q(0.5), q(0.75), sorted[sorted.length - 1]!];
}

/**
 * Gaussian kernel density estimate sampled across the value range.
 * Returns empty array when values is empty.
 */
function densityCurve(values: number[], samples = DENSITY_SAMPLES): Array<[number, number]> {
  if (values.length === 0) return [];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const n = values.length;
  const mean = values.reduce((a, b) => a + b, 0) / n;
  const variance = values.reduce((a, b) => a + (b - mean) ** 2, 0) / n;
  const std = Math.sqrt(variance) || 1;
  const bw = 1.06 * std * Math.pow(n, -0.2); // Silverman's rule
  const span = max - min || 1;
  const out: Array<[number, number]> = [];
  for (let i = 0; i <= samples; i += 1) {
    const x = min + (span * i) / samples;
    let d = 0;
    for (const v of values) {
      const u = (x - v) / bw;
      d += Math.exp(-0.5 * u * u);
    }
    d /= n * bw * Math.sqrt(2 * Math.PI);
    out.push([x, d]);
  }
  return out;
}

function buildOption(props: StatChartProps): echarts.EChartsOption {
  // Guard: return an empty, valid option when there are no values to display.
  if (props.values.length === 0) {
    return {
      title: props.title
        ? { text: props.title, left: 'center', textStyle: { fontSize: 12 } }
        : undefined,
    };
  }

  const sorted = [...props.values].sort((a, b) => a - b);

  if (props.kind === 'boxplot') {
    return {
      title: props.title
        ? { text: props.title, left: 'center', textStyle: { fontSize: 12 } }
        : undefined,
      tooltip: { trigger: 'item' },
      yAxis: { type: 'value' },
      xAxis: { type: 'category', data: [''] },
      series: [
        {
          type: 'boxplot',
          data: [quartiles(sorted)],
          itemStyle: { color: CHART_COLORS.fill, borderColor: CHART_COLORS.border },
        },
      ],
    };
  }

  // violin: mirror the KDE curve around a centre axis as two line+area series
  const curve = densityCurve(sorted);
  const maxD = Math.max(...curve.map(([, d]) => d), 1);
  const right = curve.map(([x, d]) => [VIOLIN_CENTER + (d / maxD) * VIOLIN_HALF_WIDTH, x] as [number, number]);
  const left = curve.map(([x, d]) => [VIOLIN_CENTER - (d / maxD) * VIOLIN_HALF_WIDTH, x] as [number, number]);

  return {
    title: props.title
      ? { text: props.title, left: 'center', textStyle: { fontSize: 12 } }
      : undefined,
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'value', min: 0, max: 100, show: false },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'line',
        data: right,
        smooth: true,
        areaStyle: { color: CHART_COLORS.fill, opacity: CHART_COLORS.areaOpacity },
        lineStyle: { color: CHART_COLORS.border },
        symbol: 'none',
      },
      {
        type: 'line',
        data: left,
        smooth: true,
        areaStyle: { color: CHART_COLORS.fill, opacity: CHART_COLORS.areaOpacity },
        lineStyle: { color: CHART_COLORS.border },
        symbol: 'none',
      },
    ],
  };
}

/** Lazily-mounted ECharts instance for distribution charts. */
export default function StatChart(props: StatChartProps) {
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!ref.current) return undefined;
    const chart = echarts.init(ref.current);
    chart.setOption(buildOption(props));
    const onResize = (): void => chart.resize();
    window.addEventListener('resize', onResize);
    return () => {
      window.removeEventListener('resize', onResize);
      chart.dispose();
    };
  }, [props.kind, props.values, props.title]);

  return <div ref={ref} data-chart-export="1" className="h-[320px] w-full bg-white rounded-lg" />;
}
