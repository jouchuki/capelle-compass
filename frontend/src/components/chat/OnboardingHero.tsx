import { useState, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Sparkles, X } from 'lucide-react';
import type { WijkSlug } from '../../data/capelleWijken';
import { GROEIKERNEN, type GroeikernSlug } from '../../data/groeikernen';
import CapelleMap from './CapelleMap';
import GroeikernThumbnail from './GroeikernThumbnail';
import ChatInput from './ChatInput';

// localStorage key bumps when the announcement copy changes — dismissal of
// an older version doesn't suppress newer ones.
const NEW_SOURCES_BANNER_KEY = 'datakompas:newsources:groeikernen-regelgeving-v1';

interface OnboardingHeroProps {
  /** Fires when the user clicks a wijk, a groeikern thumbnail, or sends
   *  from the composer — parent creates a session + sends. */
  onAsk: (question: string) => void;
  /** When true, every interactive surface (map cells, groeikern thumbs,
   *  composer) becomes non-interactive so a slow-network user can't fire
   *  a second request before the first lands. Defaults to ``false``. */
  sending?: boolean;
}

/**
 * Pre-session landing screen. Capelle aan den IJssel rendered as a coloured
 * pixelated wijk map in the centre, flanked left + right by silhouette
 * thumbnails of six peer 1970s groeikernen — clicking any of them seeds a
 * cross-municipality comparison question. Composer below for free-form.
 */

const WIJK_QUESTIONS: Readonly<Record<WijkSlug, string>> = {
  'schollevaar-noord':       'Hoe ontwikkelt de demografie in Schollevaar-Noord?',
  'schollevaar-zuid':        'Hoe ontwikkelt de leefbaarheid in Schollevaar-Zuid?',
  'oostgaarde-noord':        'Wat zegt de bewonersenquête over Oostgaarde-Noord?',
  'oostgaarde-zuid':         'Hoe staat de woningvoorraad ervoor in Oostgaarde-Zuid?',
  'schenkel':                'Welke beleidsthema’s spelen in Schenkel?',
  'middelwatering-west':     'Wat zijn de leefbaarheidssignalen in Middelwatering-West?',
  'middelwatering-oost':     'Hoe is de sociale samenstelling in Middelwatering-Oost?',
  'capelle-west-sgravenland':"Hoe is de woningmix in Capelle-West en 's-Gravenland?",
  'rivium-fascinatio':       'Wat is de werkgelegenheidsmix in Rivium en Fascinatio?',
};

/**
 * Per-groeikern comparison question. Each town gets its own thematic angle
 * so the suggestions don't all blur into "vergelijk leefbaarheid".
 */
const GROEIKERN_QUESTIONS: Readonly<Record<GroeikernSlug, string>> = {
  almere:     'Hoe verhoudt Capelle zich tot Almere op woningbouw en bevolkingsgroei?',
  zoetermeer: 'Hoe scoort Capelle op leefbaarheid t.o.v. Zoetermeer?',
  nieuwegein: 'Hoe verschilt het sociaal domein van Capelle van Nieuwegein?',
  purmerend:  'Hoe vergelijken de gemeentefinanciën van Capelle en Purmerend zich?',
  lelystad:   'Hoe ontwikkelt de bevolkingssamenstelling in Capelle vs Lelystad?',
  houten:     'Wat zegt de bewonersenquête over Capelle en Houten?',
};

export default function OnboardingHero({ onAsk, sending = false }: OnboardingHeroProps) {
  // Three thumbnails on each side of Capelle.
  const leftThumbs = GROEIKERNEN.slice(0, 3);
  const rightThumbs = GROEIKERNEN.slice(3, 6);

  // Dismissible "new sources" banner — sticky-dismissed in localStorage so
  // returning users don't see it again. The key version bumps when the
  // copy changes.
  const [showNewSources, setShowNewSources] = useState<boolean>(false);
  useEffect(() => {
    try {
      const dismissed = window.localStorage.getItem(NEW_SOURCES_BANNER_KEY);
      if (!dismissed) setShowNewSources(true);
    } catch {
      // localStorage blocked (private mode etc.) — just don't show it.
    }
  }, []);
  const dismissBanner = () => {
    setShowNewSources(false);
    try {
      window.localStorage.setItem(NEW_SOURCES_BANNER_KEY, String(Date.now()));
    } catch {
      // ignore
    }
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.45 }}
      className="max-w-5xl mx-auto py-2"
    >
      <AnimatePresence>
        {showNewSources && (
          <motion.div
            initial={{ opacity: 0, y: -6 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
            className="mb-4 mx-auto max-w-2xl bg-emerald-50 border border-emerald-200 rounded-md px-3 py-1.5 flex items-center gap-2 text-xs text-emerald-900"
            role="status"
          >
            <Sparkles className="w-3.5 h-3.5 flex-shrink-0 text-emerald-600" aria-hidden="true" />
            <span className="flex-1">
              Nieuwe bronnen voor groeikernen — meer documenten over aangenomen regelgeving.
            </span>
            <button
              type="button"
              onClick={dismissBanner}
              className="flex-shrink-0 p-0.5 rounded text-emerald-700 hover:bg-emerald-100 transition-colors"
              aria-label="Sluit melding"
            >
              <X className="w-3.5 h-3.5" aria-hidden="true" />
            </button>
          </motion.div>
        )}
      </AnimatePresence>
      <h1 className="text-2xl font-bold text-slate-900 tracking-tight leading-tight mb-2 text-center">
        Vergelijk Capelle aan den IJssel met andere groeikernen
      </h1>
      <p className="text-slate-500 text-sm leading-relaxed mb-6 text-center max-w-xl mx-auto">
        Klik op een wijk voor een lokale analyse — of op een nabuurgemeente om Capelle te vergelijken. Eigen vraag? Tik onderaan.
      </p>

      <div className="flex items-center justify-center gap-4">
        <div className="flex flex-col gap-3 flex-shrink-0">
          {leftThumbs.map((t) => (
            <GroeikernThumbnail
              key={t.slug}
              thumb={t}
              disabled={sending}
              onClick={() => onAsk(GROEIKERN_QUESTIONS[t.slug])}
            />
          ))}
        </div>

        <div className="flex-1 min-w-0">
          <CapelleMap
            wijkQuestions={WIJK_QUESTIONS}
            disabled={sending}
            onAskWijk={(slug) => onAsk(WIJK_QUESTIONS[slug])}
          />
        </div>

        <div className="flex flex-col gap-3 flex-shrink-0">
          {rightThumbs.map((t) => (
            <GroeikernThumbnail
              key={t.slug}
              thumb={t}
              disabled={sending}
              onClick={() => onAsk(GROEIKERN_QUESTIONS[t.slug])}
            />
          ))}
        </div>
      </div>

      <div className="mt-14">
        <ChatInput onSend={onAsk} disabled={sending} />
      </div>
    </motion.div>
  );
}
