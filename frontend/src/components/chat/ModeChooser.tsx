import { motion } from 'motion/react';
import { MapPin, LineChart } from 'lucide-react';
import ModalShell from '../modals/ModalShell';
import type { Mode } from '../../types';

interface ModeChooserProps {
  /** Whether the chooser is presented to the user. */
  open: boolean;
  /** Fires with the picked mode. The chooser does not auto-close itself —
   *  the parent decides what to do (e.g., flip its mode state, which then
   *  unmounts the chooser via the ``open`` flag). */
  onPick: (mode: Mode) => void;
}

// Tile description copy intentionally short — the user is picking which
// kind of analysis to start, not deciding between SaaS plans. One line each.
const TILES: ReadonlyArray<{
  mode: Mode;
  title: string;
  blurb: string;
  Icon: typeof MapPin;
  accent: string;
}> = [
  {
    mode: 'groeikern',
    title: 'Groeikernen',
    blurb:
      'Vergelijk Capelle met de zes andere groeikernen op CBS-cijfers, beleid, budget en bewonersenquête.',
    Icon: MapPin,
    accent: 'blue',
  },
  {
    mode: 'jeugdzorg',
    title: 'Jeugdzorg',
    blurb:
      'Verkennende EDA + lineaire-regressie toekomsttrends op de declaratiedataset (Wijk, Categorie, Aanbieder).',
    Icon: LineChart,
    accent: 'emerald',
  },
];

const ACCENT_CLASSES: Record<string, string> = {
  blue: 'hover:border-blue-400 hover:bg-blue-50 group-hover:text-blue-700',
  emerald: 'hover:border-emerald-400 hover:bg-emerald-50 group-hover:text-emerald-700',
};

const ICON_ACCENT: Record<string, string> = {
  blue: 'text-blue-600',
  emerald: 'text-emerald-600',
};

/**
 * Two-tile chooser the user sees before a new analysis starts. The modal
 * is non-dismissible (no Esc / no outside-click) — picking a mode is the
 * only way out. Parent owns the chosen-mode state.
 */
export default function ModeChooser({ open, onPick }: ModeChooserProps) {
  return (
    <ModalShell
      open={open}
      onClose={() => {
        /* non-dismissible: user must pick a mode */
      }}
      size="lg"
      ariaLabel="Kies een analysemodus"
    >
      <h2 className="text-xl font-bold text-slate-900 tracking-tight mb-1 text-center">
        Welke analyse wil je starten?
      </h2>
      <p className="text-sm text-slate-600 text-center mb-6">
        Kies per analyse. Je kunt deze keuze later opnieuw maken voor een
        volgende analyse.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {TILES.map(({ mode, title, blurb, Icon, accent }) => (
          <motion.button
            key={mode}
            type="button"
            onClick={() => onPick(mode)}
            whileHover={{ scale: 1.02 }}
            whileTap={{ scale: 0.99 }}
            className={`group text-left p-5 rounded-xl border-2 border-slate-100 bg-white transition-colors cursor-pointer ${ACCENT_CLASSES[accent]}`}
            aria-label={`Start een ${title}-analyse`}
          >
            <div className="flex items-start gap-3">
              <span
                className={`mt-0.5 inline-flex items-center justify-center w-9 h-9 rounded-lg bg-slate-50 ${ICON_ACCENT[accent]} flex-shrink-0`}
              >
                <Icon className="w-5 h-5" aria-hidden="true" />
              </span>
              <div className="flex-1">
                <div
                  className={`text-base font-semibold text-slate-900 transition-colors ${(ACCENT_CLASSES[accent] ?? '')
                    .split(' ')
                    .filter((c) => c.startsWith('group-hover'))
                    .join(' ')}`}
                >
                  {title}
                </div>
                <p className="mt-1 text-sm text-slate-600 leading-snug">{blurb}</p>
              </div>
            </div>
          </motion.button>
        ))}
      </div>
    </ModalShell>
  );
}
