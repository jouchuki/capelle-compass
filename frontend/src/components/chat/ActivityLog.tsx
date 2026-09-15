import { motion } from 'motion/react';
import {
  BookOpen,
  Check,
  Cog,
  Database,
  LineChart,
  Loader2,
  MessageSquare,
  Users,
  type LucideIcon,
} from 'lucide-react';
import type { ActivityStep, ToolSource } from '../../types';
import { sourceChipClass, sourceLabel } from '../../utils/adapt';

interface ActivityLogProps {
  steps: ActivityStep[];
}

const ICONS: Record<ToolSource, LucideIcon> = {
  cbs: Database,
  budget: Database,
  beleid: BookOpen,
  buitenbeter: MessageSquare,
  bewonersenquete: Users,
  platform: Cog,
  jeugdzorg: LineChart,
};

const SOURCE_KEYS: ToolSource[] = [
  'cbs',
  'beleid',
  'budget',
  'buitenbeter',
  'bewonersenquete',
  'platform',
  'jeugdzorg',
];

/** Map a raw tool name to the closest known data source for icon + colour. */
function toSource(tool: string): ToolSource {
  const lower = tool.toLowerCase();
  for (const key of SOURCE_KEYS) {
    if (lower.includes(key)) return key;
  }
  return 'platform';
}

/**
 * Ordered, resolving list of what the agent is doing — one row per tool call,
 * appended in arrival order. A row spins while ``running`` and shows a check
 * once its ``done`` event lands. The list never re-sorts: only the trailing
 * status glyph changes when a step resolves, so there is no layout thrash.
 */
export default function ActivityLog({ steps }: ActivityLogProps) {
  if (steps.length === 0) return null;

  return (
    <ul className="flex flex-col gap-1.5">
      {steps.map((step) => {
        const source = toSource(step.tool);
        const Icon = ICONS[source];
        const running = step.status === 'running';
        return (
          <motion.li
            key={step.id}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.25 }}
            className="flex items-center gap-2.5 text-[13px] text-slate-600"
          >
            <span
              className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[10px] font-bold uppercase tracking-wider ${sourceChipClass(source)}`}
            >
              <Icon className="w-3 h-3" />
              {sourceLabel(source)}
            </span>
            <span className="flex-1 truncate">{step.action}</span>
            <span
              aria-label={running ? 'bezig' : 'voltooid'}
              className="flex-shrink-0"
            >
              {running ? (
                <Loader2 className="w-3.5 h-3.5 text-blue-500 animate-spin" />
              ) : (
                <Check className="w-3.5 h-3.5 text-emerald-500" />
              )}
            </span>
          </motion.li>
        );
      })}
    </ul>
  );
}
