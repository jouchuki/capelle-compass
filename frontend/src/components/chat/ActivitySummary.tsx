import { useState } from 'react';
import { Check, ChevronDown, ChevronRight } from 'lucide-react';
import type { ActivityStep } from '../../types';
import ActivityLog from './ActivityLog';

interface ActivitySummaryProps {
  steps: ActivityStep[];
}

/**
 * Collapsed footer for a completed run: a single "✓ N stappen" line that
 * expands to the full {@link ActivityLog}. Lets the finished thread stay tidy
 * while keeping the agent's actual steps one click away. Renders nothing when
 * the run made no tool calls.
 */
export default function ActivitySummary({ steps }: ActivitySummaryProps) {
  const [open, setOpen] = useState<boolean>(false);
  if (steps.length === 0) return null;

  const label = `${steps.length} ${steps.length === 1 ? 'stap' : 'stappen'}`;

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 self-start text-[12px] font-medium text-slate-400 hover:text-slate-600 transition-colors"
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5" />
        )}
        <Check className="w-3.5 h-3.5 text-emerald-500" />
        {label}
      </button>
      {open ? <ActivityLog steps={steps} /> : null}
    </div>
  );
}
