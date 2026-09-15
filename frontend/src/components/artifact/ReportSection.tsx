import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { AnalysisSection } from '../../types';
import { sourceLabel } from '../../utils/adapt';
import ResultChart from './ResultChart';
import ResultTable from './ResultTable';

interface ReportSectionProps {
  section: AnalysisSection;
}

/**
 * Renders a single analysis section: source eyebrow + heading + markdown
 * content body + (if a tool_output is attached) either a ResultChart or
 * ResultTable depending on chart_hints.
 */
export default function ReportSection({ section }: ReportSectionProps) {
  const firstHint = section.tool_output?.chart_hints?.[0];

  return (
    <section className="mb-14">
      <div className="flex items-center gap-3 mb-3">
        <span className="text-[10px] uppercase tracking-[0.2em] font-bold text-slate-400">
          {sourceLabel(section.source)}
        </span>
      </div>
      <h2 className="text-xl font-bold text-slate-900 mb-4 tracking-tight">
        {section.heading}
      </h2>
      <div className="prose prose-slate prose-sm max-w-none text-slate-700 leading-relaxed text-[15px] mb-6">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{section.content}</ReactMarkdown>
      </div>
      {section.tool_output ? (
        firstHint ? (
          <ResultChart output={section.tool_output} hint={firstHint} />
        ) : (
          <ResultTable output={section.tool_output} />
        )
      ) : null}
    </section>
  );
}
