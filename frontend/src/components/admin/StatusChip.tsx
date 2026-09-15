import type { AdminFeedbackStatus } from './adminFetch';

interface StatusChipProps {
  status: AdminFeedbackStatus | string;
}

const STATUS_STYLES: Record<string, string> = {
  new: 'bg-blue-50 text-blue-700 border-blue-100',
  reviewed: 'bg-amber-50 text-amber-700 border-amber-100',
  resolved: 'bg-emerald-50 text-emerald-700 border-emerald-100',
};

const STATUS_LABELS: Record<string, string> = {
  new: 'Nieuw',
  reviewed: 'Gezien',
  resolved: 'Opgelost',
};

/** Feedback-row status chip. Falls back to slate for unknown values. */
export default function StatusChip({ status }: StatusChipProps) {
  const style = STATUS_STYLES[status] ?? 'bg-slate-50 text-slate-600 border-slate-200';
  const label = STATUS_LABELS[status] ?? status;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${style}`}
    >
      {label}
    </span>
  );
}
