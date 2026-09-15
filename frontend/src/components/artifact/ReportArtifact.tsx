import { useRef } from 'react';
import { motion } from 'motion/react';
import { ChevronDown, ChevronUp, Download, FileText, Share, X } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { toast } from 'sonner';
import type { AnalysisResult, Block, CitationRef } from '../../types';
import { createForkLink, recordEvent } from '../../api/client';
import { exportReportToPdf } from '../../utils/pdfExport';
import BlockRenderer from './BlockRenderer';
import { sectionsToBlocks } from '../../utils/blockAdapt';
import SourcesFooter from './SourcesFooter';
import ReferencesList from './ReferencesList';
import DataGapsCard from './DataGapsCard';
import FollowUpCard from './FollowUpCard';
import IconButton from '../ui/IconButton';

interface ReportArtifactProps {
  analysis: AnalysisResult;
  sessionId: string | null;
  /** Total number of analysis-bearing messages in the session (>= 1). */
  totalAnalyses: number;
  /** Zero-based index of this analysis within that list. */
  currentIndex: number;
  onPrev: () => void;
  onNext: () => void;
  onClose: () => void;
  onAskFollowUp: (question: string) => void;
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('nl-NL', { day: 'numeric', month: 'long', year: 'numeric' });
}

export default function ReportArtifact({
  analysis,
  sessionId,
  totalAnalyses,
  currentIndex,
  onPrev,
  onNext,
  onClose,
  onAskFollowUp,
}: ReportArtifactProps) {
  const bodyRef = useRef<HTMLDivElement>(null);

  // v2 block reports carry no `sections`; derive footer sources from the
  // citation registry, falling back to the legacy sections when present.
  const allSources: string[] =
    analysis.citations && analysis.citations.length > 0
      ? Array.from(new Set(analysis.citations.map((c) => c.source)))
      : (analysis.sections ?? []).map((s) => s.source);

  // Prefer the v2 block document; fall back to adapting legacy sections.
  const blocks: Block[] = analysis.blocks ?? sectionsToBlocks(analysis);
  // id -> { ref, 1-based number } so inline [n] markers map to the references footer.
  const citationList: CitationRef[] = analysis.citations ?? [];
  const citationMap = new Map<string, { ref: CitationRef; num: number }>(
    citationList.map((c, i) => [c.id, { ref: c, num: i + 1 }]),
  );

  const handleShare = async () => {
    if (!sessionId) return;
    try {
      const { url } = await createForkLink(sessionId);
      await navigator.clipboard.writeText(url);
      toast.success('Link gekopieerd naar klembord');
      void recordEvent('share_link_minted', { analysis_id: analysis.id }, sessionId);
    } catch {
      toast.error('Kon link niet aanmaken');
    }
  };

  const handleDownload = async () => {
    if (!bodyRef.current) return;
    void recordEvent('report_printed', { analysis_id: analysis.id }, sessionId ?? undefined);
    await exportReportToPdf(bodyRef.current, analysis);
  };

  const title = analysis.title ?? analysis.query;

  return (
    <motion.div
      initial={{ x: 200, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      exit={{ x: 200, opacity: 0 }}
      transition={{ type: 'spring', damping: 25, stiffness: 200 }}
      className="h-full w-full bg-[#FAFAFA] flex flex-col overflow-hidden"
    >
      <div className="flex items-center justify-between px-8 py-5 bg-white border-b border-slate-100 flex-shrink-0">
        <div className="flex items-center gap-3 min-w-0">
          <FileText className="w-5 h-5 text-blue-600 flex-shrink-0" />
          <span className="text-xs font-bold uppercase tracking-wider text-slate-900 truncate">
            {title}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <IconButton label="Download PDF" onClick={handleDownload}>
            <Download className="w-4 h-4" />
          </IconButton>
          <IconButton label="Deel link" onClick={handleShare}>
            <Share className="w-4 h-4" />
          </IconButton>
          <div className="w-px h-4 bg-slate-200 mx-1" />
          <IconButton label="Sluit rapport" onClick={onClose}>
            <X className="w-4 h-4" />
          </IconButton>
        </div>
      </div>

      <div className="flex-1 overflow-y-auto px-6 lg:px-12 py-10 scroll-smooth custom-scrollbar">
        <div
          ref={bodyRef}
          className="max-w-[800px] mx-auto bg-white p-10 lg:p-16 artifact-shadow rounded-[4px]"
        >
          <header className="mb-12">
            <h1 className="text-3xl font-bold text-slate-900 mb-3 tracking-tight leading-tight">
              {title}
            </h1>
            <div className="mt-6 flex items-center gap-6 text-[11px] font-bold uppercase tracking-wider text-slate-400 border-t border-slate-50 pt-5">
              <span>{formatDate(analysis.timestamp)}</span>
              <span className="w-1 h-1 bg-slate-300 rounded-full" />
              <span>Capelle aan den IJssel</span>
            </div>

            {totalAnalyses > 1 ? (
              <div className="mt-6 flex items-center justify-between text-[11px] font-bold uppercase tracking-wider text-slate-400">
                <span>
                  Analyse {currentIndex + 1} / {totalAnalyses}
                </span>
                <div className="flex items-center gap-1">
                  <button
                    type="button"
                    onClick={onPrev}
                    disabled={currentIndex === 0}
                    className="p-1.5 rounded text-slate-400 hover:text-slate-700 disabled:opacity-30 disabled:cursor-not-allowed"
                    aria-label="Vorige analyse"
                  >
                    <ChevronUp className="w-4 h-4" />
                  </button>
                  <button
                    type="button"
                    onClick={onNext}
                    disabled={currentIndex === totalAnalyses - 1}
                    className="p-1.5 rounded text-slate-400 hover:text-slate-700 disabled:opacity-30 disabled:cursor-not-allowed"
                    aria-label="Volgende analyse"
                  >
                    <ChevronDown className="w-4 h-4" />
                  </button>
                </div>
              </div>
            ) : null}
          </header>

          <section className="mb-14">
            <h2 className="text-[11px] font-bold text-slate-400 uppercase tracking-[0.2em] mb-5">
              Inleiding
            </h2>
            <div className="prose prose-slate prose-sm max-w-none text-slate-700 leading-relaxed text-[15px]">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{analysis.summary}</ReactMarkdown>
            </div>
          </section>

          {blocks.map((block, idx) => (
            <BlockRenderer key={idx} block={block} citations={citationMap} />
          ))}

          {analysis.data_gaps && analysis.data_gaps.length > 0 ? (
            <DataGapsCard gaps={analysis.data_gaps} />
          ) : null}

          {analysis.follow_up && analysis.follow_up.length > 0 ? (
            <FollowUpCard questions={analysis.follow_up} onAsk={onAskFollowUp} />
          ) : null}

          {citationList.length > 0 ? (
            <ReferencesList citations={citationList} />
          ) : (
            <SourcesFooter sources={allSources} />
          )}
        </div>
      </div>
    </motion.div>
  );
}
