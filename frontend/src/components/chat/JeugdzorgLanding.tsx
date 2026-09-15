import { motion } from 'motion/react';
import { LineChart } from 'lucide-react';
import ChatInput from './ChatInput';

interface JeugdzorgLandingProps {
  /** Fires when the user clicks a suggested probe or sends from the
   *  composer. Parent creates a session + sends. */
  onAsk: (question: string) => void;
  /** Disables every interactive surface to block double-submits. */
  sending?: boolean;
}

/**
 * Pre-session landing for jeugdzorg mode. No wijk map / groeikern tiles
 * — those are groeikern-specific. Instead: a brief intro + a small set
 * of starter probes mirroring the report's default panels (Wijk,
 * Categorie, Aanbieder) so the user has one-click entry into the
 * structured EDA + forecast flow.
 */
const STARTERS: ReadonlyArray<{ label: string; query: string }> = [
  {
    label: 'Hoe staat de jeugdzorg ervoor?',
    query:
      'Geef het macroplaatje van de jeugdzorg in Capelle 2020-2025: hoe ontwikkelen de totale uitgaven zich, groeit dat sneller dan inflatie, en welke wijk en welke aanbieder zijn de grootste motor achter die groei?',
  },
  {
    label: 'Wat zit er achter de groei in een wijk?',
    query:
      'Welke wijk groeit het hardst in jeugdzorg-uitgaven, en wat zit daar precies achter? Komt het door meer cliënten, meer zorg per cliënt, of duurdere zorg — en welke aanbieder en welke categorie pakken die groei?',
  },
  {
    label: 'Wie wint en wie verliest marktaandeel?',
    query:
      'Welke zorgaanbieders winnen of verliezen marktaandeel in Capelle? Trek volume, bedrag en goedkeuringspercentage (OK-rate) samen — een aanbieder die groeit met een lage OK-rate is een ander verhaal dan eentje die groeit met hoge kwaliteit.',
  },
  {
    label: 'Zijn er signalen die we moeten uitzoeken?',
    query:
      'Spoor anomalieën op in de jeugdzorg-data: negatieve declaraties, plotselinge prijssprongen per aanbieder, OK-rate-uitbijters, of aanbieders die opvallend in één wijk geconcentreerd zijn. Wat is qua omvang het belangrijkst om uit te zoeken?',
  },
];

export default function JeugdzorgLanding({
  onAsk,
  sending = false,
}: JeugdzorgLandingProps) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="max-w-3xl mx-auto py-6"
    >
      <div className="text-center mb-6">
        <span className="inline-flex items-center justify-center w-12 h-12 rounded-xl bg-emerald-50 text-emerald-600 mb-3">
          <LineChart className="w-6 h-6" aria-hidden="true" />
        </span>
        <h1 className="text-2xl font-bold text-slate-900 tracking-tight leading-tight">
          Jeugdzorg-analyse
        </h1>
        <p className="text-sm text-slate-600 mt-1">
          Verkennende EDA en lineaire-regressie toekomsttrends op de
          synthetische declaratiedataset (2020–2025).
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 mb-6">
        {STARTERS.map(({ label, query }) => (
          <button
            key={label}
            type="button"
            onClick={() => onAsk(query)}
            disabled={sending}
            className="text-left px-4 py-3 rounded-lg border border-slate-200 bg-white hover:border-emerald-300 hover:bg-emerald-50 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <div className="text-sm font-semibold text-slate-900">{label}</div>
            <div className="text-xs text-slate-600 mt-0.5 line-clamp-2">
              {query}
            </div>
          </button>
        ))}
      </div>

      <ChatInput
        onSend={onAsk}
        disabled={sending}
        placeholder="Stel een vraag over de jeugdzorg-declaratiedataset..."
      />
    </motion.div>
  );
}
