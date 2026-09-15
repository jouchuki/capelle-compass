import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { Block, CitationRef } from '../../types';
import ChartRenderer from './ChartRenderer';
import ResultTable from './ResultTable';

/** id -> its citation + 1-based number in the report's reference list. */
export type CitationLookup = Map<string, { ref: CitationRef; num: number }>;

interface BlockRendererProps {
  block: Block;
  citations: CitationLookup;
}

/**
 * Compact, de-duplicated, numbered citation markers (e.g. `[1,2,3]`) that
 * point at the numbered references footer. Repeating the full document title
 * inline — especially when several citations share one document across years —
 * produced unreadable `[doc] [doc] [doc]` runs, so we render numbers instead.
 */
function CitationMarkers({ ids, citations }: { ids?: string[]; citations: CitationLookup }) {
  if (!ids || ids.length === 0) return null;
  const nums = Array.from(
    new Set(
      ids
        .map((id) => citations.get(id)?.num)
        .filter((n): n is number => typeof n === 'number'),
    ),
  ).sort((a, b) => a - b);
  if (nums.length === 0) return null;
  return (
    <sup className="ml-0.5 text-[10px] text-blue-600 font-bold">[{nums.join(',')}]</sup>
  );
}

const CALLOUT_TONE: Record<'info' | 'warning' | 'insight', string> = {
  info: 'border-blue-200 bg-blue-50',
  warning: 'border-amber-200 bg-amber-50',
  insight: 'border-indigo-200 bg-indigo-50',
};

export default function BlockRenderer({ block, citations }: BlockRendererProps) {
  switch (block.type) {
    case 'heading': {
      const cls =
        block.level === 1
          ? 'text-2xl font-bold text-slate-900 mb-4 mt-2'
          : block.level === 2
            ? 'text-xl font-bold text-slate-900 mb-3 mt-8'
            : 'text-base font-bold text-slate-700 mb-2 mt-6';
      const Tag = (`h${block.level}`) as 'h1' | 'h2' | 'h3';
      return <Tag className={cls}>{block.text}</Tag>;
    }
    case 'prose':
      return (
        <div className="prose prose-slate prose-sm max-w-none text-slate-700 leading-relaxed text-[15px] mb-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.markdown}</ReactMarkdown>
          <CitationMarkers ids={block.citations} citations={citations} />
        </div>
      );
    case 'chart': {
      // Skip visual blocks the agent left without data instead of rendering a
      // "no data" placeholder, which reads as noise in the report.
      const chartData = block.spec?.data;
      if (!chartData || chartData.length === 0) return null;
      return (
        <figure className="mb-8">
          <ChartRenderer spec={block.spec} />
          {block.caption || (block.citations && block.citations.length > 0) ? (
            <figcaption className="mt-2 text-xs text-slate-400 text-center">
              {block.caption ?? ''}
              <CitationMarkers ids={block.citations} citations={citations} />
            </figcaption>
          ) : null}
        </figure>
      );
    }
    case 'table': {
      if (!block.data || block.data.length === 0) return null;
      return (
        <div className="mb-8">
          <ResultTable
            output={{ tool: 'platform', query: block.caption ?? '', result_type: 'table', data: block.data, columns: block.columns ?? [] }}
          />
        </div>
      );
    }
    case 'kpi':
      return (
        <div className="inline-flex flex-col rounded-xl border border-slate-100 px-6 py-4 mb-6 mr-4">
          <span className="text-3xl font-bold text-slate-900 tabular-nums">
            {block.value}
            {block.unit ? <span className="text-base text-slate-400 ml-1">{block.unit}</span> : null}
          </span>
          <span className="text-[11px] uppercase tracking-wider font-bold text-slate-400 mt-1">{block.label}</span>
          {block.delta ? <span className="text-xs text-slate-500 mt-1">{block.delta}</span> : null}
        </div>
      );
    case 'callout':
      return (
        <div className={`rounded-xl border px-5 py-4 mb-6 ${CALLOUT_TONE[block.tone]}`}>
          <div className="prose prose-slate prose-sm max-w-none text-slate-700">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.markdown}</ReactMarkdown>
          </div>
        </div>
      );
    case 'quote':
      return (
        <blockquote className="border-l-2 border-slate-200 pl-4 italic text-slate-600 mb-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{block.markdown}</ReactMarkdown>
          {block.citation ? <CitationMarkers ids={[block.citation]} citations={citations} /> : null}
        </blockquote>
      );
    case 'divider':
      return <hr className="my-8 border-slate-100" />;
    case 'sources':
      return null; // rendered by the references footer, not inline
    default:
      return null;
  }
}
