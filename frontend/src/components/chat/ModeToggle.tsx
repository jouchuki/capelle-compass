import { MapPin, LineChart } from 'lucide-react';
import type { Mode } from '../../types';

const OPTIONS: ReadonlyArray<{ mode: Mode; label: string; Icon: typeof MapPin }> = [
  { mode: 'groeikern', label: 'Groeikernen', Icon: MapPin },
  { mode: 'jeugdzorg', label: 'Jeugdzorg', Icon: LineChart },
];

interface ModeToggleProps {
  value: Mode;
  onChange: (mode: Mode) => void;
}

/**
 * Inline, always-visible mode switcher shown at the top of the landing
 * surface (no blocking modal). Picking a mode swaps the landing between the
 * groeikern and jeugdzorg start screens; the choice rides into the session
 * created on the next analysis.
 */
export default function ModeToggle({ value, onChange }: ModeToggleProps) {
  return (
    <div className="flex justify-center mb-10">
      <div
        role="tablist"
        aria-label="Analysemodus"
        className="inline-flex items-center gap-1 p-1 rounded-full border border-slate-200 bg-white"
      >
        {OPTIONS.map(({ mode, label, Icon }) => {
          const active = value === mode;
          return (
            <button
              key={mode}
              type="button"
              role="tab"
              aria-selected={active}
              onClick={() => onChange(mode)}
              className={`inline-flex items-center gap-2 px-5 py-2.5 rounded-full text-xs font-bold uppercase tracking-wider transition-colors ${
                active
                  ? 'bg-blue-600 text-white'
                  : 'text-slate-500 hover:text-slate-800'
              }`}
            >
              <Icon className="w-4 h-4" aria-hidden="true" />
              {label}
            </button>
          );
        })}
      </div>
    </div>
  );
}
