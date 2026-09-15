import { Chart, registerables } from 'chart.js';
Chart.register(...registerables);

const TOOL_COLORS = {
  cbs:             { bg: '#154273', label: 'CBS' },
  budget:          { bg: '#39870c', label: 'Budget' },
  beleid:          { bg: '#E17000', label: 'Beleid' },
  buitenbeter:     { bg: '#ca005d', label: 'BuitenBeter' },
  bewonersenquete: { bg: '#6366f1', label: 'Enquete' },
};

const CHART_PALETTE = [
  '#154273', '#E17000', '#39870c', '#ca005d', '#007bc7',
  '#f9e11e', '#d52b1e', '#84cc16', '#6366f1', '#14b8a6',
];

// ── API ────────────────────────────────────────────────────────────

let cache = null;

async function fetchAnalyses() {
  if (cache) return cache;
  try {
    const res = await fetch('/api/analyses');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    cache = await res.json();
    return cache;
  } catch (err) {
    console.warn('Could not fetch analyses:', err);
    return { analyses: [] };
  }
}

// ── Source badge ────────────────────────────────────────────────────

function badge(toolName) {
  const tc = TOOL_COLORS[toolName] || { bg: '#6b7280', label: toolName };
  return `<span class="agent-source-badge" style="background:${tc.bg}">${tc.label}</span>`;
}

// ── Simple markdown ────────────────────────────────────────────────

function md(text) {
  if (!text) return '';
  return text
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/^### (.+)$/gm, '<h4>$1</h4>')
    .replace(/^## (.+)$/gm, '<h3>$1</h3>')
    .replace(/^# (.+)$/gm, '<h2>$1</h2>')
    .replace(/^- (.+)$/gm, '<li>$1</li>')
    .replace(/\n/g, '<br>');
}

// ── Table renderer ─────────────────────────────────────────────────

function renderTable(data, columns) {
  if (!data.length) return '<p class="agent-empty">Geen rijen</p>';

  const cols = columns.length
    ? columns.filter(c => c.type !== 'text_snippet' && !c.key.startsWith('_'))
    : Object.keys(data[0]).map(k => ({ key: k, label: k, type: 'string' }));

  const maxRows = Math.min(data.length, 50);
  let html = '<table class="agent-table"><thead><tr>';
  cols.forEach(c => {
    html += `<th>${c.label}${c.unit ? ` <span class="agent-unit">(${c.unit})</span>` : ''}</th>`;
  });
  html += '</tr></thead><tbody>';

  for (let i = 0; i < maxRows; i++) {
    html += '<tr>';
    cols.forEach(c => {
      const val = data[i][c.key];
      if (val === null || val === undefined) { html += '<td class="agent-null">—</td>'; return; }
      if (c.type === 'url') { html += `<td><a href="${val}" target="_blank" class="agent-link">link</a></td>`; return; }
      if (c.type === 'number') { html += `<td class="agent-num">${typeof val === 'number' ? val.toLocaleString('nl-NL') : val}</td>`; return; }
      html += `<td>${String(val).substring(0, 120)}</td>`;
    });
    html += '</tr>';
  }
  html += '</tbody></table>';
  if (data.length > 50) html += `<p class="agent-truncated">${data.length - 50} extra rijen niet getoond</p>`;
  return html;
}

// ── Search results renderer ────────────────────────────────────────

function renderSearchResults(data) {
  return '<div class="agent-search-results">' + data.map((item, idx) => {
    const score = item.score !== undefined ? `<span class="agent-score">${(item.score * 100).toFixed(0)}%</span>` : '';
    const docType = item.doc_type ? `<span class="agent-doc-type">${item.doc_type}</span>` : '';
    const year = item.year ? `<span class="agent-year">${item.year}</span>` : '';
    const url = item.source_url ? `<a href="${item.source_url}" target="_blank" class="agent-link">bron</a>` : '';
    const text = item.text || '';
    return `
      <div class="agent-search-card">
        <div class="agent-search-header">
          <span class="agent-search-idx">#${idx + 1}</span>
          ${score} ${docType} ${year} ${url}
        </div>
        <div class="agent-search-doc">${item.document || ''}</div>
        <div class="agent-search-text">${text.substring(0, 400)}${text.length > 400 ? '...' : ''}</div>
      </div>`;
  }).join('') + '</div>';
}

// ── Chart renderer ─────────────────────────────────────────────────

function renderChart(data, hint, canvas, columns) {
  if (!data.length || !canvas) return;
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();

  const ctx = canvas.getContext('2d');
  const xKey = hint.x;
  const yKeys = Array.isArray(hint.y) ? hint.y : [hint.y];
  const groupBy = hint.group_by;
  const colMap = {};
  (columns || []).forEach(c => { colMap[c.key] = c; });

  let config;
  if (groupBy) {
    const groups = [...new Set(data.map(r => r[groupBy]))];
    const xLabels = [...new Set(data.map(r => r[xKey]))].sort();
    config = {
      type: hint.type === 'line' ? 'line' : 'bar',
      data: {
        labels: xLabels,
        datasets: groups.map((g, i) => {
          const gd = data.filter(r => r[groupBy] === g);
          return {
            label: g,
            data: xLabels.map(x => { const m = gd.find(r => r[xKey] === x); return m ? m[yKeys[0]] : null; }),
            backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length] + '90',
            borderColor: CHART_PALETTE[i % CHART_PALETTE.length],
            borderWidth: hint.type === 'line' ? 2.5 : 1,
            tension: 0.3, spanGaps: true,
          };
        }),
      },
    };
  } else {
    config = {
      type: hint.type === 'line' ? 'line' : 'bar',
      data: {
        labels: data.map(r => r[xKey]),
        datasets: yKeys.filter(Boolean).map((yKey, i) => ({
          label: colMap[yKey]?.label || yKey,
          data: data.map(r => r[yKey]),
          backgroundColor: CHART_PALETTE[i % CHART_PALETTE.length] + '90',
          borderColor: CHART_PALETTE[i % CHART_PALETTE.length],
          borderWidth: hint.type === 'line' ? 2.5 : 1,
          tension: 0.3, spanGaps: true,
        })),
      },
    };
  }

  config.options = {
    responsive: true, maintainAspectRatio: false,
    animation: { duration: 600 },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: { display: true, position: 'top', labels: { color: '#6b6b80', font: { size: 11 }, usePointStyle: true } },
      tooltip: { backgroundColor: '#1e1e2e', titleColor: '#e5e5e5', bodyColor: '#e5e5e5', padding: 10, cornerRadius: 6 },
    },
    scales: {
      x: { ticks: { color: '#6b6b80', font: { size: 11 } }, grid: { color: '#e5e7eb30' } },
      y: { ticks: { color: '#6b6b80', font: { size: 11 } }, grid: { color: '#e5e7eb20' } },
    },
  };

  new Chart(ctx, config);
}

// ── Tool output dispatcher ─────────────────────────────────────────

function renderToolOutput(to, el) {
  if (!to || !to.data) { el.innerHTML = '<p class="agent-empty">Geen data</p>'; return; }

  let html = '';
  const chartHint = (to.chart_hints || []).find(h => h.type !== 'table');
  const chartId = 'chart-' + Math.random().toString(36).slice(2, 8);

  if (chartHint && to.data.length > 1) {
    html += `<div class="agent-chart-card"><h4 class="agent-chart-title">${chartHint.title || ''}</h4><div class="agent-chart-wrap"><canvas id="${chartId}"></canvas></div></div>`;
  }

  if (to.result_type === 'search_results') {
    html += renderSearchResults(to.data);
  } else {
    html += renderTable(to.data, to.columns || []);
  }

  el.innerHTML = html;

  if (chartHint && to.data.length > 1) {
    setTimeout(() => {
      const canvas = document.getElementById(chartId);
      if (canvas) renderChart(to.data, chartHint, canvas, to.columns);
    }, 50);
  }
}

// ── Analysis card ──────────────────────────────────────────────────

function renderCard(a) {
  const date = new Date(a.timestamp).toLocaleDateString('nl-NL', { year: 'numeric', month: 'short', day: 'numeric' });
  const sources = (a.sections || []).map(s => badge(s.source)).join(' ');
  return `
    <div class="agent-analysis-card" data-id="${a.id}">
      <div class="agent-card-header">
        <h3 class="agent-card-title">${a.query}</h3>
        <span class="agent-card-date">${date}</span>
      </div>
      <div class="agent-card-sources">${sources}</div>
      <p class="agent-card-summary">${(a.summary || '').substring(0, 200)}${(a.summary || '').length > 200 ? '...' : ''}</p>
      <div class="agent-card-footer">
        <span>${(a.sections || []).length} bronnen</span>
        ${a.data_gaps?.length ? `<span class="agent-card-gaps">${a.data_gaps.length} data gaps</span>` : ''}
      </div>
    </div>`;
}

// ── Detail view ────────────────────────────────────────────────────

function renderDetail(a, container) {
  const date = new Date(a.timestamp).toLocaleDateString('nl-NL', { year: 'numeric', month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' });

  let html = `
    <div class="agent-detail">
      <button class="agent-back-btn" id="agent-back">Terug naar overzicht</button>
      <div class="agent-detail-header">
        <h2>${a.query}</h2>
        <span class="agent-detail-date">${date}</span>
      </div>
      <div class="agent-summary-card">
        <h3>Samenvatting</h3>
        <div class="agent-summary-body">${md(a.summary)}</div>
      </div>`;

  (a.sections || []).forEach((s, i) => {
    html += `
      <div class="agent-section">
        <div class="agent-section-header">${badge(s.source)}<h3>${s.heading}</h3></div>
        <div class="agent-section-content">${md(s.content)}</div>
        <div id="section-data-${i}"></div>
      </div>`;
  });

  if (a.data_gaps?.length) {
    html += `<div class="agent-gaps-card"><h3>Data Gaps</h3><ul>${a.data_gaps.map(g => `<li>${g}</li>`).join('')}</ul></div>`;
  }
  if (a.follow_up?.length) {
    html += `<div class="agent-followup-card"><h3>Vervolgvragen</h3><ul>${a.follow_up.map(f => `<li>${f}</li>`).join('')}</ul></div>`;
  }

  html += '</div>';
  container.innerHTML = html;

  (a.sections || []).forEach((s, i) => {
    if (s.tool_output) renderToolOutput(s.tool_output, container.querySelector(`#section-data-${i}`));
  });

  container.querySelector('#agent-back').addEventListener('click', () => renderList(container));
}

// ── List view ──────────────────────────────────────────────────────

async function renderList(container) {
  const { analyses } = await fetchAnalyses();

  if (!analyses.length) {
    container.innerHTML = `
      <div class="agent-empty-state">
        <h3>Geen analyses beschikbaar</h3>
        <p>Start een analyse via ohrs:</p>
        <code>ohrs -p "/capelle-analyse Hoe veilig is Capelle?"</code>
      </div>`;
    return;
  }

  container.innerHTML = `<div class="agent-cards-grid">${analyses.map(renderCard).join('')}</div>`;

  container.querySelectorAll('.agent-analysis-card').forEach(card => {
    card.addEventListener('click', async () => {
      const id = card.dataset.id;
      try {
        const res = await fetch(`/api/analyses/${id}`);
        const analysis = await res.json();
        renderDetail(analysis, container);
      } catch (err) {
        console.error('Failed to load analysis:', err);
      }
    });
  });
}

// ── Boot ───────────────────────────────────────────────────────────

renderList(document.getElementById('app'));
