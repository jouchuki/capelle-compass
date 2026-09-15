import { motion } from 'motion/react';

/**
 * Cold-start companion shown while the agent boots and takes its first turn,
 * before any real tool step lands. A little magnifying-glass sleuth that paces
 * and scans — honest about the only thing happening yet ("even speuren…",
 * roughly *"just investigating…"*) without faking tool activity. Replaced the
 * old rotating-phrase orb. Vanishes the instant the first step appears.
 */
export default function SleuthMascot() {
  return (
    <div className="flex flex-col items-center justify-center gap-4 py-10 select-none">
      <motion.div
        aria-hidden="true"
        className="relative"
        animate={{ x: [-10, 10, -10] }}
        transition={{ duration: 3.2, repeat: Infinity, ease: 'easeInOut' }}
      >
        <motion.svg
          width="56"
          height="56"
          viewBox="0 0 56 56"
          fill="none"
          xmlns="http://www.w3.org/2000/svg"
          animate={{ rotate: [-8, 8, -8] }}
          transition={{ duration: 1.6, repeat: Infinity, ease: 'easeInOut' }}
        >
          {/* lens */}
          <circle cx="23" cy="23" r="14" className="stroke-blue-500" strokeWidth="3" fill="white" />
          <circle cx="23" cy="23" r="14" className="fill-blue-100/40" />
          {/* face */}
          <circle cx="18" cy="22" r="1.8" className="fill-slate-600" />
          <circle cx="28" cy="22" r="1.8" className="fill-slate-600" />
          <path d="M18 28c2 2 8 2 10 0" className="stroke-slate-500" strokeWidth="1.6" strokeLinecap="round" fill="none" />
          {/* handle */}
          <rect
            x="33"
            y="33"
            width="6"
            height="18"
            rx="3"
            transform="rotate(-45 36 42)"
            className="fill-blue-500"
          />
        </motion.svg>
      </motion.div>

      <div className="flex items-center gap-1.5">
        <p className="text-base font-medium text-slate-400 tracking-tight">even speuren</p>
        <motion.span
          className="text-base font-medium text-slate-400"
          animate={{ opacity: [0.2, 1, 0.2] }}
          transition={{ duration: 1.4, repeat: Infinity }}
        >
          …
        </motion.span>
      </div>
    </div>
  );
}
