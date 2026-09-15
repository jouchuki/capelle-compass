import { motion } from 'motion/react';
import { ChevronRight, FileText } from 'lucide-react';
import type { AnalysisResult } from '../../types';

interface AnalysisHandoffCardProps {
  analysis: AnalysisResult;
  onOpen: () => void;
}

/**
 * The inline "Data Analyse Voltooid → Open Rapport" card that appears at the
 * bottom of any assistant bubble carrying a finished analysis. Clicking the
 * primary CTA opens the artifact pane.
 */
export default function AnalysisHandoffCard({ analysis, onOpen }: AnalysisHandoffCardProps) {
  // v2 block reports have no `sections`: count heading blocks as "secties"
  // and derive sources from the citation registry; fall back to legacy sections.
  const sectionCount =
    analysis.sections?.length ??
    (analysis.blocks ?? []).filter((b) => b.type === 'heading').length;
  const sourceCount =
    analysis.citations && analysis.citations.length > 0
      ? new Set(analysis.citations.map((c) => c.source)).size
      : new Set((analysis.sections ?? []).map((s) => s.source)).size;

  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="mt-6 p-6 bg-white border border-slate-100 rounded-xl artifact-shadow"
    >
      <div className="flex items-center gap-5">
        <div className="w-12 h-12 bg-blue-50 rounded-full flex items-center justify-center text-blue-600 flex-shrink-0">
          <FileText className="w-6 h-6" />
        </div>
        <div className="flex-1 min-w-0">
          <h4 className="font-bold text-sm text-slate-900 tracking-tight truncate">
            Data Analyse Voltooid
          </h4>
          <p className="text-[10px] uppercase tracking-wider font-bold text-slate-400 mt-0.5">
            {sectionCount} {sectionCount === 1 ? 'sectie' : 'secties'} · {sourceCount} {sourceCount === 1 ? 'bron' : 'bronnen'}
          </p>
        </div>
        <button
          type="button"
          onClick={onOpen}
          className="flex items-center gap-2 px-5 py-2.5 bg-blue-600 hover:bg-blue-700 text-white rounded-lg text-xs font-bold uppercase tracking-wider transition-all group flex-shrink-0"
        >
          Open Rapport
          <ChevronRight className="w-4 h-4 group-hover:translate-x-0.5 transition-transform" />
        </button>
      </div>
    </motion.div>
  );
}
