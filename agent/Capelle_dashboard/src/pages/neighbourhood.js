// Neighbourhood Explorer — Wijkverkenner
// Wijk-level CBS data (84583NED) with comparison view + QoL dimension breakdown
import { FEATURES, DOMAINS, getTrend, getLatest } from '../data/features.js';
import { QOL_DIMENSIONS, getQolSummary, matchPolicyToQol } from '../data/qol.js';
import {
  WIJK_DATA, WIJK_CODES, WIJK_YEARS, WIJK_DOMAINS,
  isWijkDataLoaded, getWijkLatest, getWijkAverage, getWijkComparison,
  getWijkIndicatorMeta, getWijkIndicatorIds, fetchWijkData
} from '../data/wijken.js';
import { createLineChart, Chart, CHART_COLORS } from '../utils/charts.js';
import { formatValue, trendArrow, domainColor, qolColor } from '../utils/format.js';
import { pdfExportButton } from '../utils/pdf-export.js';

let currentCharts = [];
let selectedWijken = new Set();

function destroyCharts() {
  currentCharts.forEach(c => { try { c.destroy(); } catch(e) {} });
  currentCharts = [];
}

function renderWijkSelector(container) {
  const selectorEl = container.querySelector('#wijk-selector');
  if (!selectorEl) return;

  selectorEl.innerHTML = Object.entries(WIJK_CODES).map(([code, name]) => {
    const pop = getWijkLatest(code, 'population');
    const isSelected = selectedWijken.has(code);
    return `
      <button class="wijk-pill ${isSelected ? 'active' : ''}" data-code="${code}">
        <span class="wijk-pill-name">${name}</span>
        ${pop ? `<span class="wijk-pill-pop">${formatValue(pop.value, 'number')} inw.</span>` : ''}
      </button>
    `;
  }).join('');

  // Add click handlers
  selectorEl.querySelectorAll('.wijk-pill').forEach(pill => {
    pill.addEventListener('click', () => {
      const code = pill.dataset.code;
      if (selectedWijken.has(code)) {
        selectedWijken.delete(code);
      } else {
        selectedWijken.add(code);
      }
      renderWijkSelector(container);
      renderComparisonView(container);
    });
  });
}

function renderComparisonView(container) {
  const comparisonEl = container.querySelector('#wijk-comparison');
  if (!comparisonEl) return;
  destroyCharts();

  if (selectedWijken.size === 0) {
    // Show overview of all wijken
    comparisonEl.innerHTML = renderAllWijkenOverview();
    return;
  }

  const selectedCodes = [...selectedWijken];
  let html = '';

  // Comparison per domain
  Object.entries(WIJK_DOMAINS).forEach(([domain, indicatorIds]) => {
    html += `<h3 style="font-size:16px;font-weight:700;color:${domainColor(domain)};margin:24px 0 16px;display:flex;align-items:center;gap:8px;">${domain}</h3>`;

    // Comparison table
    html += `
      <div class="table-container mb-24">
        <div style="overflow-x:auto;">
          <table class="data-table">
            <thead>
              <tr>
                <th>Indicator</th>
                ${selectedCodes.map(code => `
                  <th style="color:${CHART_COLORS[selectedCodes.indexOf(code) % CHART_COLORS.length]}">${WIJK_CODES[code]}</th>
                `).join('')}
                <th style="color:var(--text-muted);">Gem. Capelle</th>
              </tr>
            </thead>
            <tbody>
    `;

    indicatorIds.forEach(id => {
      const meta = getWijkIndicatorMeta(id);
      if (!meta) return;

      let format = 'number';
      if (meta.unit === 'k€') format = 'decimal1';
      else if (meta.unit === 'pers' || meta.unit === 'km') format = 'decimal2';
      else if (meta.unit === '%' || meta.unit === 'per km²') format = 'decimal1';

      // Get latest year that has data
      const latestYear = WIJK_YEARS[WIJK_YEARS.length - 1];
      const avg = getWijkAverage(id, latestYear);

      html += `<tr><td style="font-weight:600;">${meta.label}</td>`;
      selectedCodes.forEach(code => {
        const val = getWijkLatest(code, id);
        const isHigh = val && avg && val.value > avg * 1.1;
        const isLow = val && avg && val.value < avg * 0.9;
        html += `<td style="font-family:monospace;${isHigh ? 'color:#166534;font-weight:700;' : isLow ? 'color:#991b1b;font-weight:700;' : ''}">
          ${val ? formatValue(val.value, format, meta.unit) : '—'}
        </td>`;
      });
      html += `<td style="font-family:monospace;color:var(--text-muted);">${avg !== null ? formatValue(avg, format, meta.unit) : '—'}</td>`;
      html += `</tr>`;
    });

    html += `</tbody></table></div></div>`;

    // Bar charts for key indicators in this domain
    indicatorIds.forEach(id => {
      const meta = getWijkIndicatorMeta(id);
      if (!meta) return;

      const comparison = getWijkComparison(id);
      if (comparison.length === 0) return;

      html += `
        <div class="chart-container" id="wijk-chart-${id}">
          <div class="chart-title">${meta.label} per Wijk</div>
          <div style="height:250px;"><canvas id="canvas-wijk-${id}"></canvas></div>
        </div>
      `;
    });
  });

  comparisonEl.innerHTML = html;

  // Render bar charts after DOM is ready
  requestAnimationFrame(() => {
    Object.entries(WIJK_DOMAINS).forEach(([domain, indicatorIds]) => {
      indicatorIds.forEach(id => {
        const canvas = comparisonEl.querySelector(`#canvas-wijk-${id}`);
        if (!canvas) return;

        const comparison = getWijkComparison(id);
        if (comparison.length === 0) return;

        const existing = Chart.getChart(canvas);
        if (existing) existing.destroy();

        const chart = new Chart(canvas.getContext('2d'), {
          type: 'bar',
          data: {
            labels: comparison.map(c => c.name),
            datasets: [{
              data: comparison.map(c => c.value),
              backgroundColor: comparison.map((c, i) =>
                selectedWijken.has(c.code) ? CHART_COLORS[selectedCodes.indexOf(c.code) % CHART_COLORS.length] : '#cbd5e1'
              ),
              borderRadius: 4,
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: { legend: { display: false } },
            scales: {
              y: { grid: { color: '#e0e0e8' }, ticks: { color: '#6b6b80', font: { family: "'Inter', sans-serif", size: 12 } } },
              x: { grid: { display: false }, ticks: { color: '#6b6b80', font: { family: "'Inter', sans-serif", size: 11 } } }
            }
          }
        });
        currentCharts.push(chart);
      });
    });
  });
}

function renderAllWijkenOverview() {
  const codes = Object.keys(WIJK_CODES);
  const keyIndicators = ['population', 'households', 'avg_woz', 'household_size', 'owner_occupied_pct', 'distance_gp', 'distance_supermarket'];

  let html = `
    <div style="padding:14px 18px;background:#f8fafc;border:1px solid var(--border);border-left:3px solid var(--accent-primary);border-radius:var(--radius-md);margin-bottom:24px;font-size:13px;color:var(--text-secondary);line-height:1.6;">
      Selecteer één of meer wijken hierboven voor een gedetailleerde vergelijking per beleidsdomein. Hieronder vindt u het gemeentebrede overzicht met kerncijfers, rankings en visualisaties.
    </div>

    <!-- Full Comparison Table -->
    <h2 class="section-heading">Alle Wijken — Kerncijfers</h2>
    <div class="table-container mb-24">
      <div style="overflow-x:auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Wijk</th>
              ${keyIndicators.map(id => {
                const meta = getWijkIndicatorMeta(id);
                return `<th>${meta ? meta.label : id}</th>`;
              }).join('')}
            </tr>
          </thead>
          <tbody>
  `;

  const latestYear = WIJK_YEARS[WIJK_YEARS.length - 1];

  codes.forEach(code => {
    const name = WIJK_CODES[code];
    html += `<tr><td style="font-weight:700;color:var(--accent-primary);">${name}</td>`;
    keyIndicators.forEach(id => {
      const val = getWijkLatest(code, id);
      const meta = getWijkIndicatorMeta(id);
      const avg = getWijkAverage(id, latestYear);
      let format = 'number';
      if (meta && (meta.unit === 'k€' || meta.unit === '%' || meta.unit === 'per km²')) format = 'decimal1';
      else if (meta && (meta.unit === 'pers' || meta.unit === 'km')) format = 'decimal2';
      const isHigh = val && avg && val.value > avg * 1.1;
      const isLow = val && avg && val.value < avg * 0.9;
      html += `<td style="font-family:monospace;${isHigh ? 'color:#166534;font-weight:700;' : isLow ? 'color:#991b1b;font-weight:700;' : ''}">${val ? formatValue(val.value, format, meta ? meta.unit : '') : '—'}</td>`;
    });
    html += `</tr>`;
  });

  html += `
          </tbody>
        </table>
      </div>
      <div style="padding:8px 24px;font-size:11px;color:var(--text-muted);border-top:1px solid var(--border);">
        <span style="color:#166534;font-weight:700;">Groen</span> = meer dan 10% boven gemiddelde &nbsp;|&nbsp;
        <span style="color:#991b1b;font-weight:700;">Rood</span> = meer dan 10% onder gemiddelde
      </div>
    </div>
  `;

  // Rankings section: which wijk is best/worst per indicator
  html += `<h2 class="section-heading">Rankings per Indicator</h2>`;
  html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:32px;">`;

  const rankIndicators = [
    { id: 'population', label: 'Meeste Inwoners', icon: '👥', higher: true },
    { id: 'avg_woz', label: 'Hoogste WOZ-waarde', icon: '🏠', higher: true },
    { id: 'household_size', label: 'Grootste Huishoudens', icon: '👨‍👩‍👧‍👦', higher: true },
    { id: 'distance_gp', label: 'Dichtste Huisarts', icon: '🏥', higher: false },
    { id: 'distance_supermarket', label: 'Dichtste Supermarkt', icon: '🛒', higher: false },
    { id: 'owner_occupied_pct', label: 'Meeste Koopwoningen', icon: '🔑', higher: true },
  ];

  rankIndicators.forEach(ri => {
    const comparison = getWijkComparison(ri.id);
    if (comparison.length === 0) return;
    const sorted = ri.higher ? comparison : [...comparison].sort((a, b) => a.value - b.value);
    const meta = getWijkIndicatorMeta(ri.id);
    let format = 'number';
    if (meta && (meta.unit === 'k€' || meta.unit === '%' || meta.unit === 'per km²')) format = 'decimal1';
    else if (meta && (meta.unit === 'pers' || meta.unit === 'km')) format = 'decimal2';

    const best = sorted[0];
    const worst = sorted[sorted.length - 1];

    html += `
      <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 20px;box-shadow:var(--shadow-sm);">
        <div style="font-size:14px;font-weight:700;color:var(--text-primary);margin-bottom:12px;">${ri.icon} ${ri.label}</div>
        ${sorted.map((s, i) => {
          const barMax = ri.higher ? sorted[0].value : sorted[sorted.length - 1].value;
          const barWidth = barMax > 0 ? Math.round((ri.higher ? s.value / barMax : barMax / s.value) * 100) : 0;
          return `
            <div style="display:flex;align-items:center;gap:8px;padding:4px 0;">
              <div style="width:20px;font-size:12px;font-weight:800;color:var(--text-muted);text-align:center;">${i + 1}</div>
              <div style="width:100px;font-size:13px;font-weight:600;color:var(--text-secondary);">${s.name}</div>
              <div style="flex:1;height:6px;background:#e5e7eb;border-radius:3px;overflow:hidden;">
                <div style="height:100%;width:${barWidth}%;background:${CHART_COLORS[i % CHART_COLORS.length]};border-radius:3px;"></div>
              </div>
              <div style="width:70px;text-align:right;font-size:12px;font-family:monospace;">${formatValue(s.value, format, meta ? meta.unit : '')}</div>
            </div>
          `;
        }).join('')}
      </div>
    `;
  });

  html += `</div>`;

  // Bar charts for key indicators
  const chartIndicators = ['population', 'avg_woz', 'households', 'household_size'];
  html += `<h2 class="section-heading">Visuele Vergelijking</h2>`;
  html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(400px,1fr));gap:20px;margin-bottom:32px;">`;

  chartIndicators.forEach(id => {
    const meta = getWijkIndicatorMeta(id);
    if (!meta) return;
    html += `
      <div class="chart-container">
        <div class="chart-title">${meta.label} per Wijk</div>
        <div style="height:280px;"><canvas id="canvas-all-wijken-${id}"></canvas></div>
      </div>
    `;
  });

  html += `</div>`;

  // Inequality metrics
  html += `<h2 class="section-heading">Ongelijkheid tussen Wijken</h2>`;
  html += `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:16px;margin-bottom:32px;">`;

  const inequalityIndicators = ['population', 'avg_woz', 'household_size', 'distance_gp'];
  inequalityIndicators.forEach(id => {
    const comparison = getWijkComparison(id);
    if (comparison.length < 2) return;
    const meta = getWijkIndicatorMeta(id);
    let format = 'number';
    if (meta && (meta.unit === 'k€' || meta.unit === '%')) format = 'decimal1';
    else if (meta && (meta.unit === 'pers' || meta.unit === 'km')) format = 'decimal2';

    const highest = comparison[0];
    const lowest = comparison[comparison.length - 1];
    const avg = getWijkAverage(id, latestYear);
    const ratio = lowest.value > 0 ? (highest.value / lowest.value).toFixed(1) : '—';

    html += `
      <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:20px;box-shadow:var(--shadow-sm);">
        <div style="font-size:13px;font-weight:700;color:var(--text-primary);margin-bottom:12px;">${meta ? meta.label : id}</div>
        <div style="display:flex;justify-content:space-between;margin-bottom:8px;">
          <div>
            <div style="font-size:11px;color:var(--text-muted);font-weight:600;">Hoogste</div>
            <div style="font-size:16px;font-weight:800;color:#166534;">${formatValue(highest.value, format, meta ? meta.unit : '')}</div>
            <div style="font-size:11px;color:var(--text-muted);">${highest.name}</div>
          </div>
          <div style="text-align:center;">
            <div style="font-size:11px;color:var(--text-muted);font-weight:600;">Ratio</div>
            <div style="font-size:20px;font-weight:800;color:var(--accent-primary);">${ratio}x</div>
          </div>
          <div style="text-align:right;">
            <div style="font-size:11px;color:var(--text-muted);font-weight:600;">Laagste</div>
            <div style="font-size:16px;font-weight:800;color:#991b1b;">${formatValue(lowest.value, format, meta ? meta.unit : '')}</div>
            <div style="font-size:11px;color:var(--text-muted);">${lowest.name}</div>
          </div>
        </div>
        ${avg !== null ? `<div style="font-size:11px;color:var(--text-muted);text-align:center;padding-top:8px;border-top:1px solid #f3f4f6;">Gemiddelde: <strong>${formatValue(avg, format, meta ? meta.unit : '')}</strong></div>` : ''}
      </div>
    `;
  });

  html += `</div>`;

  // Render bar charts after DOM is ready
  requestAnimationFrame(() => {
    chartIndicators.forEach(id => {
      const canvas = document.querySelector(`#canvas-all-wijken-${id}`);
      if (!canvas) return;

      const comparison = getWijkComparison(id);
      if (comparison.length === 0) return;

      const existing = Chart.getChart(canvas);
      if (existing) existing.destroy();

      const chart = new Chart(canvas.getContext('2d'), {
        type: 'bar',
        data: {
          labels: comparison.map(c => c.name),
          datasets: [{
            label: getWijkIndicatorMeta(id)?.label || id,
            data: comparison.map(c => c.value),
            backgroundColor: comparison.map((_, i) => CHART_COLORS[i % CHART_COLORS.length]),
            borderRadius: 4,
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: {
            legend: { display: false },
            tooltip: {
              backgroundColor: '#ffffff',
              titleColor: '#154273',
              bodyColor: '#333344',
              borderColor: '#d1d1dc',
              borderWidth: 1,
              padding: 12,
              cornerRadius: 8,
            }
          },
          scales: {
            y: { grid: { color: '#e0e0e8' }, ticks: { color: '#6b6b80', font: { family: "'Inter', sans-serif" } } },
            x: { grid: { display: false }, ticks: { color: '#6b6b80', font: { family: "'Inter', sans-serif", size: 11 } } }
          }
        }
      });
      currentCharts.push(chart);
    });
  });

  return html;
}

export async function renderNeighbourhood(container) {
  // Load policies for coverage info
  let policies = [];
  try {
    const res = await fetch('/data/policy_database.json');
    if (res.ok) {
      const db = await res.json();
      policies = db.policies || [];
    }
  } catch (e) {
    try {
      const res2 = await fetch('./data/policy_database.json');
      if (res2.ok) { const db2 = await res2.json(); policies = db2.policies || []; }
    } catch (e2) {}
  }

  // Check if wijk data is loaded
  if (!isWijkDataLoaded) {
    await fetchWijkData();
  }

  const qolSummary = getQolSummary();
  const policyCounts = {};
  QOL_DIMENSIONS.forEach(d => { policyCounts[d.id] = []; });
  policies.forEach(p => {
    const matched = matchPolicyToQol(p);
    matched.forEach(dim => {
      if (policyCounts[dim.id]) policyCounts[dim.id].push(p);
    });
  });

  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Wijkverkenner</h1>
        <p class="page-subtitle">Vergelijk wijken in Capelle aan den IJssel op basis van CBS buurtdata (dataset 84583NED) — selecteer wijken voor gedetailleerde vergelijkingen per beleidsdomein.</p>
      </div>
      ${pdfExportButton('neighbourhood')}
    </div>

    <!-- Data source badge -->
    <div style="display:flex;gap:12px;margin-bottom:24px;flex-wrap:wrap;">
      <div style="display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:${isWijkDataLoaded ? '#dcfce7' : '#fef2f2'};border:1px solid ${isWijkDataLoaded ? '#bbf7d0' : '#fecaca'};border-radius:100px;font-size:12px;font-weight:600;color:${isWijkDataLoaded ? '#166534' : '#991b1b'};">
        ${isWijkDataLoaded ? '✓' : '✗'} CBS 84583NED Wijkdata
        ${isWijkDataLoaded ? `• ${Object.keys(WIJK_DATA).length} wijken • ${WIJK_YEARS.length} jaar` : '• Niet beschikbaar'}
      </div>
      <div style="display:inline-flex;align-items:center;gap:6px;padding:6px 14px;background:#dbeafe;border:1px solid #bfdbfe;border-radius:100px;font-size:12px;font-weight:600;color:#1e40af;">
        📊 ${Object.keys(WIJK_CODES).length} wijken in Capelle
      </div>
    </div>
  `;

  if (isWijkDataLoaded) {
    html += `
      <!-- Wijk Selector -->
      <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:16px 20px;margin-bottom:24px;">
        <div style="font-size:13px;font-weight:700;color:var(--text-primary);margin-bottom:12px;">Selecteer Wijken voor Vergelijking</div>
        <div id="wijk-selector" style="display:flex;flex-wrap:wrap;gap:8px;"></div>
      </div>

      <!-- Comparison View -->
      <div id="wijk-comparison"></div>
    `;
  }

  // QoL Dimension sections (existing functionality)
  html += `
    <h2 class="section-heading mt-32">Leefbaarheidsdimensies — Gemeentebreed Overzicht</h2>
    ${qolSummary.map(q => {
      const dim = q.dimension;
      const linkedPolicies = policyCounts[dim.id] || [];
      const coveragePct = policies.length > 0 ? Math.round((linkedPolicies.length / policies.length) * 100) : 0;

      return `
        <div class="neighbourhood-section">
          <div class="neighbourhood-header">
            <div class="neighbourhood-header-icon">${dim.icon}</div>
            <div class="neighbourhood-header-text">
              <div class="neighbourhood-header-title">${dim.label}</div>
              <div class="neighbourhood-header-sub">${dim.description}</div>
            </div>
            <div style="text-align:right;flex-shrink:0;">
              <div style="font-size:20px;font-weight:800;color:${dim.color}">${q.indicatorCount}</div>
              <div style="font-size:11px;color:var(--text-muted);font-weight:600;">indicatoren</div>
            </div>
          </div>
          <div class="neighbourhood-body">
            ${q.latestIndicators.length > 0 ? `
              <div class="neighbourhood-indicators">
                ${q.latestIndicators.map(ind => {
                  const tInfo = trendArrow(ind.trend);
                  const feat = FEATURES[ind.key];
                  let format = 'number';
                  if (feat && feat.unit === 'k€') format = 'decimal1';
                  else if (feat && feat.unit === 'pers') format = 'decimal2';
                  return `
                    <div class="neighbourhood-indicator">
                      <div class="neighbourhood-indicator-label">${ind.label}</div>
                      <div class="neighbourhood-indicator-value">${formatValue(ind.value, format, feat ? feat.unit : '')}</div>
                      <div class="neighbourhood-indicator-meta">
                        <span>${ind.year}</span>
                        <span class="kpi-trend trend-${tInfo.cls}">${tInfo.arrow} ${tInfo.text || '—'}</span>
                      </div>
                    </div>
                  `;
                }).join('')}
              </div>
            ` : `
              <div style="padding:24px;text-align:center;color:var(--text-muted);font-size:14px;">
                Nog geen CBS-indicatoren gekoppeld aan deze dimensie.
              </div>
            `}

            <div style="margin-top:20px;padding-top:20px;border-top:1px solid var(--border);">
              <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
                <span style="font-size:13px;font-weight:700;color:var(--text-primary);">Beleidsdekking</span>
                <span style="font-size:12px;font-weight:600;color:${dim.color}">${linkedPolicies.length} beleidsstuk${linkedPolicies.length !== 1 ? 'ken' : ''}</span>
              </div>
              <div class="coverage-bar">
                <div class="coverage-bar-track">
                  <div class="coverage-bar-fill" style="width:${Math.min(coveragePct * 3, 100)}%;background:${dim.color};"></div>
                </div>
                <span>${coveragePct}% van totaal</span>
              </div>
              ${linkedPolicies.length > 0 ? `
                <div style="margin-top:12px;display:flex;flex-direction:column;gap:8px;">
                  ${linkedPolicies.slice(0, 3).map(p => `
                    <div style="display:flex;align-items:center;gap:8px;padding:8px 12px;background:var(--bg-primary);border-radius:var(--radius-sm);border:1px solid var(--border);">
                      <span style="font-size:13px;flex:1;font-weight:500;color:var(--text-secondary);">
                        <a href="${p.url}" target="_blank" style="color:inherit;text-decoration:none;">${p.title}</a>
                      </span>
                      ${p.priority_score ? `<span style="font-size:11px;font-weight:700;color:${dim.color}">${Math.round(p.priority_score*100)}%</span>` : ''}
                    </div>
                  `).join('')}
                </div>
              ` : ''}
            </div>
          </div>
        </div>
      `;
    }).join('')}
  `;

  container.innerHTML = html;

  // Initialize wijk selector and comparison view
  if (isWijkDataLoaded) {
    renderWijkSelector(container);
    renderComparisonView(container);
  }
}
