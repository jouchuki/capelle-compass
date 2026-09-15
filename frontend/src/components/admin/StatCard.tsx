interface StatCardProps {
  label: string;
  value: string;
}

/** The big-number / small-eyebrow stat tile used on the Overzicht header. */
export default function StatCard({ label, value }: StatCardProps) {
  return (
    <div className="bg-white border border-slate-100 artifact-shadow rounded-2xl px-5 py-6 flex flex-col gap-2">
      <span className="text-4xl font-bold text-slate-900 tracking-tight tabular-nums">{value}</span>
      <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">{label}</span>
    </div>
  );
}
