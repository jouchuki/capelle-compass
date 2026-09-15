// Data Integrity page — validates live metrics against CBS API responses + Policy API health
import { FEATURES, YEARS, isDataLoaded } from '../data/features.js';
import { formatValue } from '../utils/format.js';

function validateData() {
  const results = [];
  let passCount = 0;
  let warnCount = 0;
  let totalDataPoints = 0;
  let missingDataPoints = 0;

  Object.entries(FEATURES).forEach(([key, feat]) => {
    let hasLatest = false;
    let latestValue = null;
    let latestYear = null;

    feat.values.forEach((v, i) => {
      totalDataPoints++;
      if (v === null) missingDataPoints++;
      else { hasLatest = true; latestValue = v; latestYear = YEARS[i]; }
    });

    const hasDataAtAll = feat.values.some(v => v !== null);
    const pass = hasDataAtAll;
    if (pass) passCount++; else warnCount++;

    results.push({
      key, label: feat.label, domain: feat.domain,
      latestValue, latestYear,
      displayedValue: latestValue !== null ? formatValue(latestValue, 'number') : '—',
      dataYears: feat.values.map((v, i) => ({ year: YEARS[i], hasData: v !== null })),
      missingYears: feat.values.reduce((acc, v, i) => v === null ? [...acc, YEARS[i]] : acc, []),
      pass
    });
  });

  return { results, passCount, warnCount, totalDataPoints, missingDataPoints, completeness: totalDataPoints > 0 ? ((totalDataPoints - missingDataPoints) / totalDataPoints * 100).toFixed(1) : 0 };
}

export async function renderIntegrity(container) {
  const validation = validateData();

  // Check policy API health
  let apiHealth = null;
  try {
    const res = await fetch('/api/health');
    if (res.ok) apiHealth = await res.json();
  } catch (e) {}

  let html = `
    <div class="page-header">
      <h1 class="page-title">Data Integriteit & Systeemstatus</h1>
      <p class="page-subtitle">Verbindingsstatus met de CBS Open Data API en de beleidsdatabase, met een volledigheidscontrole van alle Capelse meetwaarden.</p>
    </div>

    <!-- API Status Cards -->
    <div class="integrity-summary" style="margin-bottom: 24px;">
      <div class="integrity-stat" style="background: ${isDataLoaded ? 'rgba(16,185,129,0.05)' : 'rgba(239,68,68,0.05)'}; border-color: ${isDataLoaded ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'};">
        <div class="integrity-stat-value" style="color: ${isDataLoaded ? 'var(--accent-green)' : 'var(--accent-red)'}">${isDataLoaded ? 'VERBONDEN' : 'FOUT'}</div>
        <div class="integrity-stat-label">CBS OData API Status (70072ned → GM0502)</div>
      </div>
      <div class="integrity-stat" style="background: ${apiHealth ? 'rgba(16,185,129,0.05)' : 'rgba(239,68,68,0.05)'}; border-color: ${apiHealth ? 'rgba(16,185,129,0.2)' : 'rgba(239,68,68,0.2)'};">
        <div class="integrity-stat-value" style="color: ${apiHealth ? 'var(--accent-green)' : 'var(--accent-red)'}">${apiHealth ? 'VERBONDEN' : 'OFFLINE'}</div>
        <div class="integrity-stat-label">Beleids-API (Flask → SQLite)</div>
      </div>
    </div>

    ${apiHealth ? `
    <!-- Policy API Details -->
    <div class="chart-container mb-24">
      <div class="chart-title">Beleidsdatabase Status</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;">
        <div style="text-align:center;padding:16px;background:var(--bg-primary);border-radius:var(--radius-md);">
          <div style="font-size:28px;font-weight:800;color:var(--accent-primary);">${apiHealth.total_policies}</div>
          <div style="font-size:12px;font-weight:600;color:var(--text-muted);">Totaal Beleidsregels</div>
        </div>
        <div style="text-align:center;padding:16px;background:var(--bg-primary);border-radius:var(--radius-md);">
          <div style="font-size:28px;font-weight:800;color:var(--accent-purple);">${apiHealth.v2_policies}</div>
          <div style="font-size:12px;font-weight:600;color:var(--text-muted);">V2 Format (met QoL)</div>
        </div>
        <div style="text-align:center;padding:16px;background:var(--bg-primary);border-radius:var(--radius-md);">
          <div style="font-size:28px;font-weight:800;color:${apiHealth.db_exists ? 'var(--accent-green)' : 'var(--accent-red)'};">${apiHealth.db_exists ? '✓' : '✗'}</div>
          <div style="font-size:12px;font-weight:600;color:var(--text-muted);">Database Actief</div>
        </div>
      </div>
    </div>
    ` : `
    <div style="margin-bottom:24px;padding:20px;background:#fef3c7;border:1px solid #fde68a;border-radius:var(--radius-lg);color:#92400e;">
      ⚠️ <strong>Beleids-API is niet bereikbaar.</strong> Start de Flask API: <code>cd scripts && python api.py</code>
    </div>
    `}

    <!-- CBS Data Stats -->
    <div class="integrity-summary">
      <div class="integrity-stat">
        <div class="integrity-stat-value" style="color: var(--accent-green);">${validation.passCount}</div>
        <div class="integrity-stat-label">Actieve Indicatoren ✓</div>
      </div>
      <div class="integrity-stat">
        <div class="integrity-stat-value" style="color: ${validation.warnCount > 0 ? 'var(--accent-secondary)' : 'var(--accent-green)'};">${validation.warnCount}</div>
        <div class="integrity-stat-label">Lege Indicatoren</div>
      </div>
      <div class="integrity-stat">
        <div class="integrity-stat-value" style="color: var(--accent-primary);">${validation.completeness}%</div>
        <div class="integrity-stat-label">Data Volledigheid</div>
      </div>
      <div class="integrity-stat">
        <div class="integrity-stat-value" style="color: var(--text-secondary);">${validation.totalDataPoints - validation.missingDataPoints}</div>
        <div class="integrity-stat-label">Geldige Datapunten (van ${validation.totalDataPoints})</div>
      </div>
    </div>

    <!-- Methodologie -->
    <div class="chart-container mb-24">
      <div class="chart-title">Databronnen & Methodologie</div>
      <div style="font-size: 14px; color: var(--text-secondary); line-height: 1.6;">
        <p><strong>CBS Open Data:</strong> Dataset <code>70072ned</code> (Regionale kerncijfers) → Filter <code>GM0502</code></p>
        <p style="margin-top:8px;"><strong>Beleidsdata:</strong> Gescrapt van <code>lokaleregelgeving.overheid.nl</code>, verwerkt door lokale AI (Qwen2.5 7B + Nomic Embed Text)</p>
        <p style="margin-top:8px;"><strong>Periode:</strong> ${YEARS.length > 0 ? `${YEARS[0]} t/m ${YEARS[YEARS.length - 1]}` : 'Laden...'}</p>
      </div>
    </div>

    <!-- Validation Table -->
    <h2 class="section-heading">Inspectie Meetwaarden Capelle</h2>
    <div class="table-container">
      <div style="overflow-x: auto;">
        <table class="data-table">
          <thead>
            <tr>
              <th>Status</th><th>Indicator</th><th>Domein</th><th>Laatste Waarde</th><th>Jaar</th><th>Compleetheid</th><th>Ontbrekend</th>
            </tr>
          </thead>
          <tbody>
            ${validation.results.map(r => `
              <tr>
                <td style="font-size:16px;">${r.pass ? '✅' : '⚠️'}</td>
                <td style="font-weight:600;color:var(--text-primary);">${r.label}</td>
                <td style="font-size:12px;">${r.domain}</td>
                <td style="font-family:monospace;">${r.displayedValue}</td>
                <td>${r.latestYear || '—'}</td>
                <td><div style="display:flex;gap:2px;">${r.dataYears.map(dy => `<div class="completeness-cell ${dy.hasData ? 'has-data' : 'no-data'}" title="${dy.year}: ${dy.hasData ? 'Beschikbaar' : 'Ontbreekt'}">${dy.hasData ? '✓' : '✗'}</div>`).join('')}</div></td>
                <td style="font-size:11px;color:var(--text-muted);">${r.missingYears.length > 0 ? r.missingYears.join(', ') : 'Geen'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    </div>
  `;

  container.innerHTML = html;
}
