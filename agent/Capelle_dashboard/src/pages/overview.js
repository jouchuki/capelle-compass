// Overview page — Leefkompas: QoL dimensions + KPI cards + Composite Scores
import { FEATURES, DOMAINS, getTrend, getLatest } from '../data/features.js';
import { QOL_DIMENSIONS, getQolSummary } from '../data/qol.js';
import { computeLiveabilityIndex, computePolicyCoverage, computeTrendVelocity, computeEarlyWarnings } from '../data/composites.js';
import { formatValue, trendArrow, domainIcon, domainColor, qolColor } from '../utils/format.js';
import { pdfExportButton } from '../utils/pdf-export.js';

export function renderOverview(container) {
  // Determine the most recent year among all features
  let latestGlobalYear = 2015;
  Object.keys(FEATURES).forEach(key => {
    const l = getLatest(key);
    if (l && l.year > latestGlobalYear) latestGlobalYear = l.year;
  });

  const qolSummary = getQolSummary();

  // Composite scores
  const liveability = computeLiveabilityIndex();
  const velocity = computeTrendVelocity();
  const warnings = computeEarlyWarnings(4);

  // Load policies for coverage (async, but we'll render what we can sync first)
  let policies = [];

  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Overzicht</h1>
        <p class="page-subtitle">Kwaliteit van leven in Capelle aan den IJssel — kernindicatoren, dimensies en trendanalyse per beleidsdomein.</p>
      </div>
      ${pdfExportButton('overview')}
    </div>

    <!-- Composite Scores Dashboard -->
    <h2 class="section-heading">Samengestelde Scores</h2>
    <div class="composite-dashboard">
      <!-- Liveability Gauge -->
      <div class="composite-gauge-card">
        <div class="composite-gauge">
          <svg viewBox="0 0 120 120" class="gauge-svg">
            <circle cx="60" cy="60" r="52" fill="none" stroke="#e5e7eb" stroke-width="8" />
            <circle cx="60" cy="60" r="52" fill="none" stroke="${liveability.overall >= 60 ? '#16a34a' : liveability.overall >= 40 ? '#E17000' : '#dc2626'}" stroke-width="8" stroke-dasharray="${(liveability.overall || 0) * 3.27} 327" stroke-linecap="round" transform="rotate(-90 60 60)" />
            <text x="60" y="55" text-anchor="middle" font-size="28" font-weight="800" fill="var(--accent-primary)">${liveability.overall !== null ? liveability.overall : '—'}</text>
            <text x="60" y="72" text-anchor="middle" font-size="10" fill="var(--text-muted)" font-weight="600">/ 100</text>
          </svg>
        </div>
        <div class="composite-gauge-label">Leefbaarheidsindex</div>
        <div class="composite-gauge-sub">Gewogen score over alle dimensies</div>
      </div>

      <!-- Trend Summary -->
      <div class="composite-stats-card">
        <div class="composite-stats-title">Trendsamenvatting</div>
        <div class="composite-stats-grid">
          <div class="composite-stat">
            <div class="composite-stat-value" style="color:#16a34a">${velocity.filter(v => !v.isNegativeMetric && v.fiveYearTrend > 2).length + velocity.filter(v => v.isNegativeMetric && v.fiveYearTrend < -2).length}</div>
            <div class="composite-stat-label">Verbeterend</div>
          </div>
          <div class="composite-stat">
            <div class="composite-stat-value" style="color:var(--text-muted)">${velocity.filter(v => v.fiveYearTrend !== null && Math.abs(v.fiveYearTrend) <= 2).length}</div>
            <div class="composite-stat-label">Stabiel</div>
          </div>
          <div class="composite-stat">
            <div class="composite-stat-value" style="color:#dc2626">${velocity.filter(v => !v.isNegativeMetric && v.fiveYearTrend < -2).length + velocity.filter(v => v.isNegativeMetric && v.fiveYearTrend > 2).length}</div>
            <div class="composite-stat-label">Verslechterend</div>
          </div>
          <div class="composite-stat">
            <div class="composite-stat-value" style="color:#E17000">${warnings.length}</div>
            <div class="composite-stat-label">Waarschuwingen</div>
          </div>
        </div>
      </div>

      <!-- Early Warnings -->
      <div class="composite-warnings-card">
        <div class="composite-stats-title">Snelste Veranderingen</div>
        ${warnings.length > 0 ? warnings.map(w => `
          <div class="composite-warning-row">
            <div class="composite-warning-dot" style="background:${w.warningColor}"></div>
            <div class="composite-warning-text">
              <div style="font-weight:600;font-size:13px;">${w.label}</div>
              <div style="font-size:11px;color:var(--text-muted);">${w.domain} • ${w.fiveYearTrend > 0 ? '+' : ''}${w.fiveYearTrend.toFixed(1)}% (5jr)</div>
            </div>
          </div>
        `).join('') : '<div style="font-size:13px;color:var(--text-muted);padding:12px 0;">Geen significante waarschuwingen</div>'}
      </div>
    </div>

    <!-- Liveability per Dimension -->
    <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:24px;margin-bottom:32px;box-shadow:var(--shadow-sm);">
      <div style="font-size:16px;font-weight:700;color:var(--accent-primary);margin-bottom:16px;">Leefbaarheid per Dimensie</div>
      ${liveability.dimensions.map(d => {
        const score = d.score !== null ? d.score : 0;
        const barColor = score >= 60 ? '#16a34a' : score >= 40 ? '#E17000' : '#dc2626';
        return `
          <div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid #f3f4f6;">
            <div style="width:28px;text-align:center;font-size:18px;">${d.icon}</div>
            <div style="flex:1;min-width:120px;font-size:14px;font-weight:600;color:var(--text-primary);">${d.label}</div>
            <div style="flex:2;">
              <div class="score-bar" style="height:8px;">
                <div class="score-bar-fill" style="width:${score}%;background:${barColor};"></div>
              </div>
            </div>
            <div style="width:50px;text-align:right;font-size:14px;font-weight:800;color:${barColor};">${d.score !== null ? d.score : '—'}</div>
            <div style="width:60px;text-align:right;font-size:11px;color:var(--text-muted);">${d.indicators.length} ind.</div>
          </div>
        `;
      }).join('')}
    </div>

    <!-- QoL Dimension Cards -->
    <h2 class="section-heading">Leefbaarheidsdimensies</h2>
    <div class="qol-grid">
      ${qolSummary.map(q => {
        const dim = q.dimension;
        const trendBg = q.trendDirection === 'stijgend' ? 'rgba(239,68,68,0.1)' :
                        q.trendDirection === 'dalend' ? 'rgba(16,185,129,0.1)' :
                        'rgba(107,107,128,0.08)';
        const trendColor = q.trendDirection === 'stijgend' ? '#dc2626' :
                           q.trendDirection === 'dalend' ? '#16a34a' : '#6b7280';
        return `
          <div class="qol-card" style="border-top: 4px solid ${dim.color}">
            <div class="qol-card-icon">${dim.icon}</div>
            <div class="qol-card-label">${dim.label}</div>
            <div class="qol-card-desc">${dim.description}</div>
            <div class="qol-card-stats">
              <span class="qol-card-count">${q.indicatorCount} indicator${q.indicatorCount !== 1 ? 'en' : ''}</span>
              ${q.avgTrend !== null ? `
                <span style="font-size:12px;font-weight:700;padding:2px 8px;border-radius:100px;background:${trendBg};color:${trendColor}">
                  ${q.trendDirection === 'stijgend' ? '↗' : q.trendDirection === 'dalend' ? '↘' : '→'} ${q.avgTrend.toFixed(1)}%
                </span>
              ` : `<span style="font-size:11px;color:#9ca3af;">Geen data</span>`}
            </div>
          </div>
        `;
      }).join('')}
    </div>

    <!-- Hero: Population -->
    <div class="hero-card">
      <div class="hero-label">Totale Bevolking — Capelle aan den IJssel</div>
      `;

  const popLatest = getLatest('population');
  if (popLatest) {
    const popTrend = getTrend('population');
    const tInfo = trendArrow(popTrend);
    html += `
      <div class="hero-stat">
        <span class="hero-value">${formatValue(popLatest.value, 'number')}</span>
        <span class="hero-unit">inwoners (${popLatest.year})</span>
      </div>
      <span class="hero-trend trend-${tInfo.cls}" style="background: ${tInfo.cls.includes('up') ? 'rgba(239,68,68,0.12)' : tInfo.cls.includes('down') ? 'rgba(16,185,129,0.12)' : 'rgba(107,107,128,0.12)'}">
        ${tInfo.arrow} Groei: ${tInfo.text} (5 jaar)
      </span>
    `;
  }

  html += `
    </div>

    <!-- All features by domain -->
    <h2 class="section-heading mt-32">Kernindicatoren per Beleidsdomein</h2>
  `;

  Object.entries(DOMAINS).forEach(([domain, featureKeys]) => {
    const hasData = featureKeys.some(key => getLatest(key));
    if (!hasData) return;

    html += `<h3 style="font-size: 16px; font-weight: 700; color: ${domainColor(domain)}; margin: 24px 0 16px; display: flex; align-items: center; gap: 8px;">${domainIcon(domain)} ${domain}</h3>`;
    html += `<div class="kpi-grid">`;

    featureKeys.forEach(key => {
      const feat = FEATURES[key];
      if (!feat) return;
      const latest = getLatest(key);
      if (!latest) return;
      const fTrend = getTrend(key);
      const tInfo = trendArrow(fTrend);

      let format = 'number';
      if(feat.unit === 'k€') format = 'decimal1';
      else if(feat.unit === 'pers') format = 'decimal2';
      else if(feat.unit === '%') format = 'percent';

      let displayVal = formatValue(latest.value, format, feat.unit);

      html += `
        <div class="kpi-card">
          <div class="kpi-header">
            <span class="kpi-label">${feat.label}</span>
          </div>
          <div class="kpi-value">${displayVal}</div>
          <div class="kpi-footer">
            <span class="kpi-year">Jaar: ${latest.year}</span>
            <span class="kpi-trend trend-${tInfo.cls}">${tInfo.arrow} 5jr: ${tInfo.text || '—'}</span>
          </div>
        </div>
      `;
    });

    html += `</div>`;
  });

  container.innerHTML = html;
}
