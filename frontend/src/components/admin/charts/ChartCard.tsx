import type { ReactNode } from 'react';

interface ChartCardProps {
  title: string;
  subtitle?: string;
  children: ReactNode;
}

/** White-card wrapper around a recharts ResponsiveContainer. */
export default function ChartCard({ title, subtitle, children }: ChartCardProps) {
  return (
    <div className="bg-white border border-slate-100 artifact-shadow rounded-2xl p-5 flex flex-col gap-3">
      <div className="flex flex-col gap-0.5">
        <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
          {title}
        </span>
        {subtitle ? <span className="text-xs text-slate-400">{subtitle}</span> : null}
      </div>
      <div className="w-full">{children}</div>
    </div>
  );
}
