import { sourceChipClass, sourceLabel } from '../../utils/adapt';

interface SourceBadgeProps {
  source: string;
  /** Optional override label (e.g. for a sub-source — defaults to the Dutch name). */
  children?: string;
}

/**
 * Coloured pill used both inline in section eyebrows and in the bronnen-footer.
 * Pulls its palette from the ``source-chip-*`` utilities in index.css.
 */
export default function SourceBadge({ source, children }: SourceBadgeProps) {
  return (
    <span
      className={`inline-flex items-center px-3 py-1 rounded-full text-[11px] font-bold uppercase tracking-wider ${sourceChipClass(source)}`}
    >
      {children ?? sourceLabel(source)}
    </span>
  );
}
