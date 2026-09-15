/**
 * Soft pastel chip for topic strings.
 *
 * Topics come from arbitrary backend strings — we map a known set of
 * Capelle topics and feedback topics to deterministic pastel colors,
 * falling back to slate for anything unmapped so the table stays
 * legible.
 */

interface TopicChipProps {
  topic: string | null | undefined;
}

const TOPIC_STYLES: Record<string, string> = {
  // Content topics
  Veiligheid: 'bg-rose-50 text-rose-700 border-rose-100',
  Wonen: 'bg-amber-50 text-amber-700 border-amber-100',
  'Sociaal domein': 'bg-violet-50 text-violet-700 border-violet-100',
  Klimaat: 'bg-emerald-50 text-emerald-700 border-emerald-100',
  Onderwijs: 'bg-sky-50 text-sky-700 border-sky-100',
  Vergelijking: 'bg-indigo-50 text-indigo-700 border-indigo-100',
  'Openbare ruimte': 'bg-teal-50 text-teal-700 border-teal-100',
  // Feedback topics
  bug: 'bg-rose-50 text-rose-700 border-rose-100',
  suggestion: 'bg-sky-50 text-sky-700 border-sky-100',
  more_usage: 'bg-amber-50 text-amber-700 border-amber-100',
  other: 'bg-slate-50 text-slate-600 border-slate-200',
};

const FEEDBACK_LABELS: Record<string, string> = {
  bug: 'Bug',
  suggestion: 'Suggestie',
  more_usage: 'Meer gebruik',
  other: 'Overig',
};

export default function TopicChip({ topic }: TopicChipProps) {
  if (!topic) {
    return <span className="text-xs text-slate-300">—</span>;
  }
  const style = TOPIC_STYLES[topic] ?? 'bg-slate-50 text-slate-600 border-slate-200';
  const label = FEEDBACK_LABELS[topic] ?? topic;
  return (
    <span
      className={`inline-flex items-center rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${style}`}
    >
      {label}
    </span>
  );
}
