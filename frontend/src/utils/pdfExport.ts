import { toPng } from 'html-to-image';
import { toast } from 'sonner';
import type { AnalysisResult, Block, CitationRef, ColumnDef } from '../types';
import { sectionsToBlocks } from './blockAdapt';

// ─── Public API ──────────────────────────────────────────────────────────────

/**
 * Export an AnalysisReport to PDF via the browser's native print dialog.
 *
 * Rasterises every on-screen `[data-chart-export]` element to PNG via
 * `html-to-image` (handles both recharts SVG and ECharts canvas), then
 * builds a block-aware print document and opens it in a new window so the
 * user can Save as PDF from the native dialog.
 *
 * Signature is intentionally preserved from the pre-block version so
 * `ReportArtifact.handleDownload` needs no changes.
 */
export async function exportReportToPdf(
  element: HTMLElement,
  analysis: AnalysisResult,
): Promise<void> {
  // 1. Collect live on-screen chart nodes in DOM order BEFORE opening the
  //    print window (the print window has no mounted React tree).
  const chartNodes = Array.from(
    element.querySelectorAll<HTMLElement>('[data-chart-export]'),
  );

  // 2. Rasterise all charts concurrently; fall back gracefully per node.
  const chartImages = await Promise.all(
    chartNodes.map(async (node) => {
      try {
        return await toPng(node, { cacheBust: true, backgroundColor: '#ffffff' });
      } catch {
        return null;
      }
    }),
  );

  // 3. Derive the block list exactly as the UI does.
  const blocks = analysis.blocks ?? sectionsToBlocks(analysis);

  // 4. Build the citation registry.
  const citationMap = new Map<string, CitationRef>(
    (analysis.citations ?? []).map((c) => [c.id, c]),
  );

  // 5. Build and open the print window.
  const htmlBody = buildPrintHtml(analysis, blocks, citationMap, chartImages);

  const win = window.open('', '_blank', 'width=900,height=1200');
  if (!win) {
    toast.error(
      'Popup werd geblokkeerd. Sta pop-ups toe om het PDF-rapport te genereren.',
    );
    return;
  }

  win.document.open();
  win.document.write(htmlBody);
  win.document.close();

  await new Promise<void>((resolve) => {
    if (win.document.readyState === 'complete') {
      setTimeout(resolve, 500);
    } else {
      win.addEventListener('load', () => setTimeout(resolve, 500), { once: true });
    }
  });

  win.focus();
  win.print();
}

// ─── Print HTML builder ───────────────────────────────────────────────────────

function buildPrintHtml(
  analysis: AnalysisResult,
  blocks: Block[],
  citationMap: Map<string, CitationRef>,
  chartImages: Array<string | null>,
): string {
  const title = escapeHtml(analysis.title ?? analysis.query);
  const timestamp = new Date(analysis.timestamp).toLocaleString('nl-NL');
  const filename = filenameFor(analysis);

  // Walk blocks in order; each `chart` block consumes the next available
  // chart image by index.
  let chartIdx = 0;
  const bodyHtml = blocks
    .map((block) => {
      if (block.type === 'chart') {
        const img = chartImages[chartIdx++] ?? null;
        return renderChartBlock(block, img, citationMap);
      }
      return renderBlock(block, citationMap);
    })
    .join('\n');

  return `<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8" />
  <title>${filename}</title>
  <style>
    @page { size: A4; margin: 14mm 12mm 14mm 12mm; }
    * { box-sizing: border-box; }
    body {
      font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
      color: #0f172a;
      background: #ffffff;
      font-size: 11pt;
      line-height: 1.5;
      margin: 0;
      padding: 0;
    }
    .page-header {
      border-bottom: 2px solid #154273;
      padding-bottom: 8pt;
      margin-bottom: 14pt;
    }
    .page-header .brand {
      display: flex; justify-content: space-between; align-items: baseline;
    }
    .page-header .brand-name {
      font-weight: 800; font-size: 14pt; color: #154273;
    }
    .page-header .brand-sub {
      font-size: 9pt; color: #475569;
    }
    .page-header .stamp {
      font-size: 8pt; color: #64748b;
    }
    .page-header h1 {
      margin: 8pt 0 0; font-size: 13pt; color: #0f172a; font-weight: 700;
    }
    .card {
      background: #f8fafc;
      border: 1px solid #cbd5e1;
      border-radius: 6px;
      padding: 10pt 12pt;
      margin: 6pt 0;
      page-break-inside: avoid;
    }
    .card.summary { border-left: 3px solid #154273; }
    .card.gaps    { border-left: 3px solid #b45309; background: #fffbeb; }
    .card.follow  { border-left: 3px solid #0369a1; background: #eff6ff; }
    .card.kpi     { border-left: 3px solid #6366f1; }
    .card.callout-info    { border-left: 3px solid #0369a1; background: #eff6ff; }
    .card.callout-warning { border-left: 3px solid #b45309; background: #fffbeb; }
    .card.callout-insight { border-left: 3px solid #059669; background: #f0fdf4; }
    .card h2, .card h3 {
      margin: 0 0 6pt; font-size: 11pt; font-weight: 700; color: #0f172a;
    }
    h1.block-h1 { font-size: 13pt; font-weight: 800; margin: 12pt 0 4pt; color: #0f172a; }
    h2.block-h2 { font-size: 12pt; font-weight: 700; margin: 10pt 0 4pt; color: #0f172a; }
    h3.block-h3 { font-size: 11pt; font-weight: 600; margin: 8pt 0 3pt; color: #0f172a; }
    .prose p { margin: 4pt 0; color: #0f172a; }
    .prose strong { color: #0f172a; }
    .kpi-value { font-size: 18pt; font-weight: 800; color: #154273; }
    .kpi-label { font-size: 9pt; color: #475569; margin-top: 2pt; }
    .kpi-delta { font-size: 8pt; color: #64748b; margin-top: 1pt; }
    .chart-img-wrap { margin: 6pt 0; page-break-inside: avoid; }
    .chart-img-wrap img { max-width: 100%; height: auto; }
    .chart-caption { font-size: 8pt; color: #64748b; margin-top: 2pt; font-style: italic; }
    .quote-block {
      border-left: 3px solid #94a3b8;
      padding: 4pt 8pt;
      margin: 6pt 0;
      color: #475569;
      font-style: italic;
    }
    hr.divider { border: none; border-top: 1px solid #e2e8f0; margin: 10pt 0; }
    table {
      width: 100%;
      border-collapse: collapse;
      font-size: 9pt;
      margin-top: 6pt;
      page-break-inside: avoid;
    }
    th {
      text-align: left;
      padding: 4pt 6pt;
      font-size: 8pt;
      font-weight: 600;
      text-transform: uppercase;
      color: #64748b;
      border-bottom: 1.5px solid #0f172a;
      letter-spacing: 0.04em;
    }
    td {
      padding: 3pt 6pt;
      border-bottom: 1px solid #e2e8f0;
      color: #0f172a;
    }
    td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
    ul { margin: 4pt 0 0 14pt; padding: 0; }
    li { margin: 2pt 0; }
    .footer {
      margin-top: 18pt;
      border-top: 1px solid #cbd5e1;
      padding-top: 6pt;
      font-size: 7pt;
      color: #64748b;
    }
    .sources-block {
      margin-top: 8pt;
      border-top: 1px solid #cbd5e1;
      padding-top: 4pt;
      page-break-inside: avoid;
    }
    .sources-title {
      font-size: 8pt;
      font-weight: 700;
      color: #64748b;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      margin-bottom: 3pt;
    }
    table.sources {
      font-size: 8pt;
      margin-top: 0;
    }
    table.sources th {
      font-size: 7pt;
      color: #64748b;
      border-bottom: 1px solid #94a3b8;
      padding: 2pt 4pt;
    }
    table.sources td {
      padding: 2pt 4pt;
      border-bottom: 1px solid #e2e8f0;
      color: #334155;
      vertical-align: top;
    }
    table.sources td.num { text-align: right; font-variant-numeric: tabular-nums; }
    table.sources .type-tag {
      display: inline-block;
      padding: 1pt 4pt;
      background: #e2e8f0;
      color: #334155;
      border-radius: 2px;
      font-size: 7pt;
      text-transform: capitalize;
    }
    table.sources a { color: #1d4ed8; text-decoration: none; }
  </style>
</head>
<body>
  <div class="page-header">
    <div class="brand">
      <div>
        <div class="brand-name">DataKompas</div>
        <div class="brand-sub">Gemeentelijke data-analyse — AI-gegenereerd</div>
      </div>
      <div class="stamp">${escapeHtml(timestamp)}</div>
    </div>
    <h1>${title}</h1>
  </div>

  <div class="card summary">
    <h2>Samenvatting</h2>
    <div class="prose">${renderMarkdown(analysis.summary)}</div>
  </div>

  ${bodyHtml}

  ${(analysis.data_gaps?.length ?? 0) > 0 ? `
    <div class="card gaps">
      <h3>Data gaps</h3>
      <ul>${(analysis.data_gaps ?? []).map((g) => `<li>${escapeHtml(g)}</li>`).join('')}</ul>
    </div>` : ''}

  ${(analysis.follow_up?.length ?? 0) > 0 ? `
    <div class="card follow">
      <h3>Vervolgvragen</h3>
      <ul>${(analysis.follow_up ?? []).map((f) => `<li>${escapeHtml(f)}</li>`).join('')}</ul>
    </div>` : ''}

  <div class="footer">
    Bron: CBS StatLine, Capelle begrotingen/jaarstukken, bewonersenquete, BuitenBeter.
    Dit document bevat AI-gegenereerde analyses — controleer belangrijke informatie bij de originele bron.
  </div>
</body>
</html>`;
}

// ─── Block renderers ──────────────────────────────────────────────────────────

function renderBlock(block: Block, citationMap: Map<string, CitationRef>): string {
  switch (block.type) {
    case 'heading':
      return renderHeadingBlock(block);
    case 'prose':
      return renderProseBlock(block, citationMap);
    case 'table':
      return renderTableBlock(block, citationMap);
    case 'kpi':
      return renderKpiBlock(block);
    case 'callout':
      return renderCalloutBlock(block);
    case 'quote':
      return renderQuoteBlock(block, citationMap);
    case 'divider':
      return '<hr class="divider" />';
    case 'sources':
      return renderSourcesBlock(block, citationMap);
    case 'chart':
      // Should not be reached — chart blocks are handled in buildPrintHtml.
      return '';
    default:
      return '';
  }
}

function renderHeadingBlock(block: Extract<Block, { type: 'heading' }>): string {
  const tag = `h${block.level}` as const;
  const cls = `block-h${block.level}`;
  return `<${tag} class="${cls}">${escapeHtml(block.text)}</${tag}>`;
}

function renderProseBlock(
  block: Extract<Block, { type: 'prose' }>,
  citationMap: Map<string, CitationRef>,
): string {
  const resolved = resolveCitationMarkers(block.markdown, citationMap);
  return `<div class="prose">${renderMarkdown(resolved)}</div>`;
}

function renderChartBlock(
  block: Extract<Block, { type: 'chart' }>,
  chartImg: string | null,
  citationMap: Map<string, CitationRef>,
): string {
  const captionHtml = block.caption
    ? `<div class="chart-caption">${escapeHtml(block.caption)}</div>`
    : '';

  if (chartImg) {
    return `<div class="chart-img-wrap">
      <img src="${chartImg}" alt="${block.caption ? escapeHtml(block.caption) : 'chart'}" />
      ${captionHtml}
    </div>`;
  }

  // Fallback: render chart's data as a table.
  return renderTableFallback(block.spec.columns, block.spec.data, block.caption ?? '', citationMap);
}

function renderTableBlock(
  block: Extract<Block, { type: 'table' }>,
  _citationMap: Map<string, CitationRef>,
): string {
  return renderTableFallback(block.columns, block.data, block.caption ?? '', _citationMap);
}

function renderTableFallback(
  columns: ColumnDef[],
  data: Record<string, unknown>[],
  caption: string,
  _citationMap: Map<string, CitationRef>,
): string {
  const cols = columns.filter((c) => c.type !== 'text_snippet' && !c.key.startsWith('_'));
  if (cols.length === 0 || data.length === 0) return '';

  const captionHtml = caption
    ? `<div class="chart-caption">${escapeHtml(caption)}</div>`
    : '';

  const head = cols
    .map((c) => {
      const isNum = c.type === 'number' || c.type === 'year';
      return `<th class="${isNum ? 'num' : ''}">${escapeHtml(c.label)}${c.unit ? ` (${escapeHtml(c.unit)})` : ''}</th>`;
    })
    .join('');

  const rows = data.slice(0, 40).map((row) => {
    const tds = cols.map((c) => {
      const val = row[c.key];
      if (val == null) return '<td>—</td>';
      if (c.type === 'year') {
        const n = Number(val);
        return `<td class="num">${isNaN(n) ? escapeHtml(String(val)) : Math.trunc(n)}</td>`;
      }
      if (c.type === 'number') {
        const n = Number(val);
        return `<td class="num">${isNaN(n) ? escapeHtml(String(val)) : n.toLocaleString('nl-NL')}</td>`;
      }
      return `<td>${escapeHtml(String(val))}</td>`;
    });
    return `<tr>${tds.join('')}</tr>`;
  });

  return `${captionHtml}<table><thead><tr>${head}</tr></thead><tbody>${rows.join('')}</tbody></table>`;
}

function renderKpiBlock(block: Extract<Block, { type: 'kpi' }>): string {
  const valueStr =
    typeof block.value === 'number' ? block.value.toLocaleString('nl-NL') : block.value;
  const unitStr = block.unit ? ` ${escapeHtml(block.unit)}` : '';
  const deltaHtml = block.delta
    ? `<div class="kpi-delta">${escapeHtml(block.delta)}</div>`
    : '';
  return `<div class="card kpi">
    <div class="kpi-value">${escapeHtml(String(valueStr))}${unitStr}</div>
    <div class="kpi-label">${escapeHtml(block.label)}</div>
    ${deltaHtml}
  </div>`;
}

function renderCalloutBlock(block: Extract<Block, { type: 'callout' }>): string {
  const toneClass = `callout-${block.tone}`;
  return `<div class="card ${toneClass}">
    <div class="prose">${renderMarkdown(block.markdown)}</div>
  </div>`;
}

function renderQuoteBlock(
  block: Extract<Block, { type: 'quote' }>,
  citationMap: Map<string, CitationRef>,
): string {
  const resolved = resolveCitationMarkers(block.markdown, citationMap);
  let citationHtml = '';
  if (block.citation) {
    const ref = citationMap.get(block.citation);
    if (ref) {
      citationHtml = ` <span style="font-size:8pt;color:#64748b;">— ${escapeHtml(ref.document)}</span>`;
    }
  }
  return `<blockquote class="quote-block">
    <div class="prose">${renderMarkdown(resolved)}</div>${citationHtml}
  </blockquote>`;
}

function renderSourcesBlock(
  block: Extract<Block, { type: 'sources' }>,
  citationMap: Map<string, CitationRef>,
): string {
  const refs = block.refs
    .map((id) => citationMap.get(id))
    .filter((r): r is CitationRef => r !== undefined);
  if (refs.length === 0) return '';

  const rows = refs.map((ref, i) => {
    const url = ref.source_url;
    const linkHtml = url
      ? `<a href="${escapeHtml(url)}">bekijken</a>`
      : '<span style="color:#94a3b8;">—</span>';
    return `<tr>
      <td class="num">${i + 1}</td>
      <td><span class="type-tag">${escapeHtml(ref.doc_type)}</span></td>
      <td class="num">${ref.year ? escapeHtml(ref.year) : '—'}</td>
      <td>${escapeHtml(ref.document)}</td>
      <td>${linkHtml}</td>
    </tr>`;
  });

  return `<div class="sources-block">
    <div class="sources-title">Bronnen (${refs.length})</div>
    <table class="sources">
      <thead>
        <tr>
          <th style="width:18pt;">#</th>
          <th>Type</th>
          <th style="width:28pt;">Jaar</th>
          <th>Document</th>
          <th style="width:36pt;">Link</th>
        </tr>
      </thead>
      <tbody>${rows.join('')}</tbody>
    </table>
  </div>`;
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Replace `[doc]` citation markers in markdown text with the human-readable
 * document name from the citation registry.
 */
function resolveCitationMarkers(
  text: string,
  citationMap: Map<string, CitationRef>,
): string {
  return text.replace(/\[([^\]]+)\]/g, (match, id: string) => {
    const ref = citationMap.get(id);
    if (!ref) return match;
    return `[${ref.document}]`;
  });
}

function filenameFor(analysis: AnalysisResult): string {
  const slug = analysis.query
    .slice(0, 50)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
  const today = new Date().toISOString().slice(0, 10);
  return `capelle-${slug}-${today}.pdf`;
}

function renderMarkdown(md: string): string {
  if (!md) return '';
  let html = escapeHtml(md);
  html = html.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\*([^*]+)\*/g, '<em>$1</em>');
  html = html.replace(/\n\n+/g, '</p><p>').replace(/\n/g, '<br/>');
  return `<p>${html}</p>`;
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
