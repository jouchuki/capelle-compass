import { useMemo, useState } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { ArrowRight } from 'lucide-react';
import { COLS, GRID, ROWS, WIJKEN, type Cell, type WijkSlug } from '../../data/capelleWijken';

/**
 * Pixelated geographic map of Capelle aan den IJssel rendered from real CBS
 * 2024 wijk polygons (see scripts/build_capelle_grid.py). Each grid cell is
 * a tiny coloured square; cells belonging to the same wijk highlight as a
 * group on hover. Clicking anywhere on a wijk fires `onAskWijk(slug)` —
 * the parent decides which preset question to send. A floating tooltip
 * shows the hovered wijk's label + question preview.
 */

interface CapelleMapProps {
  /** Preset question per wijk — what gets asked when the user clicks. */
  wijkQuestions: Readonly<Record<WijkSlug, string>>;
  onAskWijk: (slug: WijkSlug) => void;
  /** When true, every wijk cell becomes non-interactive (parent is mid-send). */
  disabled?: boolean;
}

/**
 * Per-wijk soft pastel palette. Kept within the 100–200 lightness band so
 * the map reads as one surface rather than a flag.
 */
const WIJK_FILL: Record<WijkSlug, { base: string; active: string }> = {
  'schollevaar-noord':       { base: 'bg-blue-100',    active: 'bg-blue-300'    },
  'schollevaar-zuid':        { base: 'bg-blue-200',    active: 'bg-blue-400'    },
  'oostgaarde-noord':        { base: 'bg-sky-100',     active: 'bg-sky-300'     },
  'oostgaarde-zuid':         { base: 'bg-sky-200',     active: 'bg-sky-400'     },
  'schenkel':                { base: 'bg-violet-100',  active: 'bg-violet-300'  },
  'middelwatering-west':     { base: 'bg-amber-100',   active: 'bg-amber-300'   },
  'middelwatering-oost':     { base: 'bg-amber-200',   active: 'bg-amber-400'   },
  'capelle-west-sgravenland':{ base: 'bg-rose-100',    active: 'bg-rose-300'    },
  'rivium-fascinatio':       { base: 'bg-indigo-100',  active: 'bg-indigo-300'  },
};

function cellClass(cell: Cell, hovered: WijkSlug | null): string {
  if (cell === 'empty') return 'bg-transparent';
  const fill = WIJK_FILL[cell];
  return hovered === cell ? fill.active : fill.base;
}

export default function CapelleMap({ wijkQuestions, onAskWijk, disabled = false }: CapelleMapProps) {
  const [hover, setHover] = useState<WijkSlug | null>(null);
  const flatCells = useMemo(() => GRID.flat(), []);

  const hoveredWijk = hover ? WIJKEN.find((w) => w.slug === hover) ?? null : null;
  const hoveredQuestion = hover ? wijkQuestions[hover] : null;

  return (
    <div className="w-full max-w-[580px] mx-auto">
      <motion.div
        initial={{ opacity: 0, scale: 0.98 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.4 }}
        className="relative aspect-[40/30] w-full"
      >
        {/* Pixel grid */}
        <div
          className="absolute inset-0 grid gap-px"
          style={{
            gridTemplateColumns: `repeat(${COLS}, minmax(0, 1fr))`,
            gridTemplateRows: `repeat(${ROWS}, minmax(0, 1fr))`,
          }}
          onMouseLeave={() => setHover(null)}
        >
          {flatCells.map((cell, idx) => {
            const isWijk = cell !== 'empty';
            const cellDisabled = !isWijk || disabled;
            return (
              <button
                key={idx}
                type="button"
                disabled={cellDisabled}
                onMouseEnter={() => isWijk && !disabled && setHover(cell)}
                onFocus={() => isWijk && !disabled && setHover(cell)}
                onClick={() => isWijk && !disabled && onAskWijk(cell)}
                className={`${cellClass(cell, hover)} ${
                  isWijk
                    ? `transition-colors duration-150 focus:outline-none focus:ring-1 focus:ring-blue-300 ${
                        disabled
                          ? 'cursor-not-allowed opacity-60'
                          : 'cursor-pointer'
                      }`
                    : 'cursor-default'
                }`}
                aria-label={isWijk ? `Vraag stellen over ${WIJKEN.find((w) => w.slug === cell)?.label ?? cell}` : undefined}
                tabIndex={cellDisabled ? -1 : 0}
              />
            );
          })}
        </div>

        {/* Floating tooltip — anchored near the hovered wijk's centroid */}
        <AnimatePresence>
          {hoveredWijk && hoveredQuestion ? (
            <motion.div
              key={hoveredWijk.slug}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 4 }}
              transition={{ duration: 0.15 }}
              className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-[calc(100%+8px)] max-w-[260px] rounded-xl bg-white border border-slate-100 artifact-shadow px-4 py-3"
              style={{
                left: `${(hoveredWijk.col / COLS) * 100}%`,
                top: `${(hoveredWijk.row / ROWS) * 100}%`,
              }}
            >
              <p className="text-[9px] uppercase tracking-[0.15em] font-bold text-slate-400 mb-1">
                {hoveredWijk.label}
              </p>
              <p className="text-[12px] font-medium text-slate-700 leading-snug flex items-start gap-2">
                <span className="flex-1">{hoveredQuestion}</span>
                <ArrowRight className="w-3.5 h-3.5 mt-0.5 text-blue-600 flex-shrink-0" />
              </p>
            </motion.div>
          ) : null}
        </AnimatePresence>
      </motion.div>
    </div>
  );
}
