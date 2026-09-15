// Benchmark page — Vergelijking: Capelle vs peer municipalities
import {
  fetchPeerData, isPeerDataLoaded, PEER_MUNICIPALITIES, PEER_COLORS, PEER_YEARS,
  BENCHMARK_THEMES, getPeerLatest, getPeerRanking, getPeerTimeSeries, getPeerIndicatorMeta
} from '../data/peers.js';
import { createLineChart } from '../utils/charts.js';
import { formatValue, domainIcon, domainColor } from '../utils/format.js';
import { pdfExportButton } from '../utils/pdf-export.js';

let currentCharts = [];

function destroyCharts() {
  currentCharts.forEach(c => { try { c.destroy(); } catch(e) {} });
  currentCharts = [];
}

function renderRankingTable(indicatorId, higherIsBetter = true) {
  const meta = getPeerIndicatorMeta(indicatorId);
  if (!meta) return '';
  const ranking = getPeerRanking(indicatorId, higherIsBetter);
  if (ranking.length === 0) return '';

  const capelleRank = ranking.find(r => r.isSelf);
  const capellePos = capelleRank ? capelleRank.rank : '?';

  let format = 'number';
  if (meta.unit === 'k€') format = 'decimal1';
  else if (meta.unit === 'pers') format = 'decimal2';

  return `
    <div class="benchmark-ranking-card">
      <div class="benchmark-ranking-header">
        <span class="benchmark-ranking-title">${meta.label}</span>
        <span class="benchmark-capelle-rank" style="background:${capellePos <= 2 ? '#dcfce7' : capellePos >= ranking.length - 1 ? '#fef2f2' : '#fef3c7'};color:${capellePos <= 2 ? '#166534' : capellePos >= ranking.length - 1 ? '#991b1b' : '#92400e'}">
          Capelle: #${capellePos}
        </span>
      </div>
      <div class="benchmark-ranking-list">
        ${ranking.map((r, i) => {
          const barWidth = ranking[0].value > 0 ? Math.round((r.value / ranking[0].value) * 100) : 0;
          return `
            <div class="benchmark-ranking-row ${r.isSelf ? 'is-self' : ''}">
              <div class="benchmark-rank-num">${r.rank}</div>
              <div class="benchmark-rank-name">${r.name}</div>
              <div class="benchmark-rank-bar-container">
                <div class="benchmark-rank-bar" style="width:${barWidth}%;background:${PEER_COLORS[r.code] || '#6b7280'}"></div>
              </div>
              <div class="benchmark-rank-value">${formatValue(r.value, format, meta.unit)}</div>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `;
}

function renderTrendCharts(container, indicatorId) {
  const meta = getPeerIndicatorMeta(indicatorId);
  if (!meta) return;

  const series = getPeerTimeSeries(indicatorId);
  if (series.length === 0) return;

  // Find the common years
  const allYears = [...new Set(series.flatMap(s => s.years))].sort((a, b) => a - b);

  const chartContainer = document.createElement('div');
  chartContainer.className = 'chart-container';
  chartContainer.innerHTML = `
    <div class="chart-title">${meta.label} — Tijdreeks Vergelijking</div>
    <div class="chart-wrapper" style="height:350px;">
      <canvas></canvas>
    </div>
  `;
  container.appendChild(chartContainer);

  const canvas = chartContainer.querySelector('canvas');

  // Align data to common years
  const datasets = series.map(s => {
    const dataByYear = {};
    s.years.forEach((y, i) => { dataByYear[y] = s.data[i]; });
    return {
      label: s.name,
      data: allYears.map(y => dataByYear[y] !== undefined ? dataByYear[y] : null),
      color: s.color,
      extra: s.isSelf ? { borderWidth: 4, pointRadius: 5 } : { borderWidth: 1.5, borderDash: [4, 2], pointRadius: 3 },
    };
  });

  const chart = createLineChart(canvas, datasets, allYears, { unit: meta.unit });
  currentCharts.push(chart);
}

export async function renderBenchmark(container) {
  // Show loading state
  container.innerHTML = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Vergelijking Peer Gemeenten</h1>
        <p class="page-subtitle">Benchmarkanalyse: prestaties van Capelle aan den IJssel ten opzichte van vergelijkbare gemeenten, op basis van CBS kerncijfers.</p>
      </div>
      ${pdfExportButton('benchmark')}
    </div>
    <div style="text-align:center;padding:60px;color:var(--text-muted);">
      <div style="font-size:24px;margin-bottom:12px;">Laden...</div>
      <div>Peer gemeente data ophalen van CBS...</div>
    </div>
  `;

  // Fetch peer data (lazy load)
  const success = await fetchPeerData();
  if (!success) {
    container.innerHTML = `
      <div class="page-header">
        <h1 class="page-title">Vergelijking met Peer Gemeenten</h1>
      </div>
      <div style="padding:40px;text-align:center;color:#991b1b;background:#fef2f2;border:1px solid #fecaca;border-radius:var(--radius-lg);">
        Kan peer gemeente data niet laden van CBS. Controleer uw internetverbinding.
      </div>
    `;
    return;
  }

  // Municipality legend
  const legendHtml = PEER_MUNICIPALITIES.map(p => `
    <span style="display:inline-flex;align-items:center;gap:6px;margin-right:16px;font-size:13px;font-weight:${p.isSelf ? '800' : '500'};">
      <span style="width:12px;height:12px;border-radius:50%;background:${PEER_COLORS[p.code]}"></span>
      ${p.name}${p.isSelf ? ' (eigen)' : ''}
    </span>
  `).join('');

  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Vergelijking Peer Gemeenten</h1>
        <p class="page-subtitle">Benchmarkanalyse: prestaties van Capelle aan den IJssel ten opzichte van vergelijkbare gemeenten, op basis van CBS kerncijfers.</p>
      </div>
      ${pdfExportButton('benchmark')}
    </div>

    <!-- Legend -->
    <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 20px;margin-bottom:24px;display:flex;flex-wrap:wrap;align-items:center;gap:8px;">
      <span style="font-size:12px;font-weight:700;color:var(--text-muted);text-transform:uppercase;margin-right:8px;">Gemeenten:</span>
      ${legendHtml}
    </div>

    <!-- Quick Overview -->
    <h2 class="section-heading">Overzicht — Positie Capelle</h2>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:16px;margin-bottom:32px;">
  `;

  // Quick ranking cards for key indicators
  const keyIndicators = [
    { id: 'population', higher: true, icon: '👥' },
    { id: 'avg_woz', higher: true, icon: '🏠' },
    { id: 'businesses', higher: true, icon: '💼' },
    { id: 'benefits', higher: false, icon: '📋' },
    { id: 'cars_per_1000', higher: true, icon: '🚗' },
    { id: 'household_size', higher: true, icon: '👨‍👩‍👧‍👦' },
  ];

  keyIndicators.forEach(ki => {
    const ranking = getPeerRanking(ki.id, ki.higher);
    const capelle = ranking.find(r => r.isSelf);
    const meta = getPeerIndicatorMeta(ki.id);
    if (!capelle || !meta) return;

    let format = 'number';
    if (meta.unit === 'k€') format = 'decimal1';
    else if (meta.unit === 'pers') format = 'decimal2';

    const isTop = capelle.rank <= 2;
    const isBottom = capelle.rank >= ranking.length - 1;

    html += `
      <div class="kpi-card" style="border-top:3px solid ${isTop ? '#16a34a' : isBottom ? '#dc2626' : '#E17000'}">
        <div class="kpi-header">
          <span class="kpi-label">${ki.icon} ${meta.label}</span>
          <span style="font-size:11px;font-weight:700;padding:2px 8px;border-radius:100px;background:${isTop ? '#dcfce7' : isBottom ? '#fef2f2' : '#fef3c7'};color:${isTop ? '#166534' : isBottom ? '#991b1b' : '#92400e'}">
            #${capelle.rank}/${ranking.length}
          </span>
        </div>
        <div class="kpi-value">${formatValue(capelle.value, format, meta.unit)}</div>
        <div class="kpi-footer">
          <span class="kpi-year">${capelle.year}</span>
          <span style="font-size:11px;color:var(--text-muted);">
            ${ki.higher ? 'Hoogste' : 'Laagste'}: ${formatValue(ranking[0].value, format, meta.unit)}
          </span>
        </div>
      </div>
    `;
  });

  html += `</div>`;

  // Detailed rankings per theme
  Object.entries(BENCHMARK_THEMES).forEach(([theme, indicators]) => {
    html += `<h2 class="section-heading">${domainIcon(theme)} ${theme} — Gedetailleerde Ranking</h2>`;
    html += `<div class="benchmark-rankings-grid">`;
    indicators.forEach(id => {
      const higherIsBetter = !['benefits'].includes(id);
      html += renderRankingTable(id, higherIsBetter);
    });
    html += `</div>`;
  });

  // Trend charts section - placeholder divs
  html += `
    <h2 class="section-heading mt-32">Historische Vergelijking</h2>
    <div class="filter-tabs" id="benchmark-tabs">
      ${Object.keys(BENCHMARK_THEMES).map((t, i) => `
        <button class="filter-tab ${i === 0 ? 'active' : ''}" data-theme="${t}">
          ${domainIcon(t)} ${t}
        </button>
      `).join('')}
    </div>
    <div id="benchmark-charts"></div>
  `;

  container.innerHTML = html;

  // Render initial trend charts
  const chartsContainer = container.querySelector('#benchmark-charts');
  const firstTheme = Object.keys(BENCHMARK_THEMES)[0];
  renderThemeCharts(chartsContainer, BENCHMARK_THEMES[firstTheme]);

  // Tab switching for charts
  container.querySelectorAll('#benchmark-tabs .filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('#benchmark-tabs .filter-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const theme = tab.dataset.theme;
      renderThemeCharts(chartsContainer, BENCHMARK_THEMES[theme]);
    });
  });
}

function renderThemeCharts(container, indicatorIds) {
  destroyCharts();
  container.innerHTML = '';
  indicatorIds.forEach(id => {
    renderTrendCharts(container, id);
  });
}
