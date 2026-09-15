import { motion } from 'motion/react';
import type { GroeikernGrid } from '../../data/groeikernen';

interface GroeikernThumbnailProps {
  thumb: GroeikernGrid;
  onClick: () => void;
  /** When true, the thumbnail becomes non-interactive (parent is mid-send). */
  disabled?: boolean;
}

/**
 * Small clickable silhouette of a Dutch groeikern (planned 1970s new-town).
 * Single muted color, no district coloring — purpose is just to make the
 * comparator gemeente visually recognizable next to Capelle.
 */
export default function GroeikernThumbnail({ thumb, onClick, disabled = false }: GroeikernThumbnailProps) {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      disabled={disabled}
      whileHover={disabled ? undefined : { scale: 1.04 }}
      whileTap={disabled ? undefined : { scale: 0.98 }}
      className="group flex flex-col items-center gap-1.5 p-2 rounded-lg hover:bg-slate-50 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-200 disabled:opacity-60 disabled:cursor-not-allowed disabled:hover:bg-transparent"
      aria-label={`Vergelijk met ${thumb.label}`}
    >
      <div
        className="grid gap-px"
        style={{
          gridTemplateColumns: `repeat(${thumb.cols}, minmax(0, 1fr))`,
          gridTemplateRows: `repeat(${thumb.rows}, minmax(0, 1fr))`,
          width: '88px',
          height: '88px',
        }}
        aria-hidden="true"
      >
        {thumb.grid.flat().map((filled, i) => (
          <div
            key={i}
            className={
              filled
                ? 'bg-slate-300 group-hover:bg-blue-400 transition-colors'
                : 'bg-transparent'
            }
          />
        ))}
      </div>
      <span className="text-[10px] font-bold uppercase tracking-[0.1em] text-slate-500 group-hover:text-slate-900 transition-colors">
        {thumb.label}
      </span>
    </motion.button>
  );
}
