// Priority Engine — Prioriteiten: Waar moet Capelle eerst handelen?
// Decision support: worsening indicators, weak policy coverage, high-priority policies
import { FEATURES, DOMAINS, getTrend, getLatest } from '../data/features.js';
import { QOL_DIMENSIONS, getQolSummary, matchPolicyToQol } from '../data/qol.js';
import { formatValue, trendArrow, qolColor, qolIcon, statusBadge, priorityBadge, confidenceBadge } from '../utils/format.js';
import { pdfExportButton } from '../utils/pdf-export.js';

export async function renderPriority(container) {
  const qolSummary = getQolSummary();

  // Load policies
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

  // --- Worsening indicators ---
  const worseningIndicators = [];
  Object.keys(FEATURES).forEach(key => {
    const feat = FEATURES[key];
    if (!feat) return;
    const trend = getTrend(key);
    const latest = getLatest(key);
    if (trend !== null && latest) {
      // For some indicators, "up" is bad (unemployment, benefits)
      const isNegativeMetric = ['benefits', 'unemployment'].includes(key);
      const isBadTrend = isNegativeMetric ? trend > 3 : false;
      // For demographics losing population sub-groups etc
      if (Math.abs(trend) > 5 || isBadTrend) {
        worseningIndicators.push({
          key, label: feat.label, domain: feat.domain,
          trend, latest: latest.value, year: latest.year,
          severity: Math.abs(trend),
          isBad: isBadTrend || trend > 10,
        });
      }
    }
  });
  worseningIndicators.sort((a, b) => b.severity - a.severity);

  // --- Policy coverage per QoL ---
  const coverageData = QOL_DIMENSIONS.map(dim => {
    const linkedCount = policies.filter(p => {
      const matched = matchPolicyToQol(p);
      return matched.some(m => m.id === dim.id);
    }).length;
    return { dim, linkedCount, pct: policies.length > 0 ? Math.round((linkedCount / policies.length) * 100) : 0 };
  }).sort((a, b) => a.linkedCount - b.linkedCount);

  // --- High priority policies ---
  const priorityPolicies = [...policies]
    .filter(p => p.priority_score !== undefined)
    .sort((a, b) => (b.priority_score || 0) - (a.priority_score || 0));

  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Prioriteiten</h1>
        <p class="page-subtitle">Beslissingsondersteunend overzicht op basis van trendanalyse, beleidsdekking en prioriteitsscores — om beleidsaandacht gericht te kunnen inzetten.</p>
      </div>
      ${pdfExportButton('priority')}
    </div>

    <!-- Worsening Indicators -->
    <h2 class="section-heading">Signalering — Sterkste Veranderingen (CBS Data)</h2>
    ${worseningIndicators.length > 0 ? `
      <div class="priority-grid">
        ${worseningIndicators.slice(0, 8).map(ind => {
          const tInfo = trendArrow(ind.trend);
          const bgColor = ind.isBad ? 'rgba(239,68,68,0.04)' : 'rgba(21,66,115,0.02)';
          const borderColor = ind.isBad ? '#fecaca' : 'var(--border)';
          return `
            <div class="priority-card" style="background:${bgColor};border-color:${borderColor}">
              <div class="priority-card-header">
                <div class="priority-card-title">${ind.label}</div>
                <span class="kpi-trend trend-${tInfo.cls}" style="font-size:14px;font-weight:800;">${tInfo.arrow} ${tInfo.text}</span>
              </div>
              <div class="priority-card-body">
                <div style="font-size:13px;color:var(--text-muted);margin-bottom:4px;">${ind.domain}</div>
                <div style="font-size:20px;font-weight:800;color:var(--accent-primary);">${formatValue(ind.latest, 'number')}</div>
                <div style="font-size:12px;color:var(--text-muted);">Jaar: ${ind.year} • 5-jaars verandering</div>
              </div>
            </div>
          `;
        }).join('')}
      </div>
    ` : `
      <div style="padding:32px;text-align:center;color:var(--text-muted);background:#fff;border-radius:var(--radius-lg);border:1px solid var(--border);margin-bottom:32px;">
        Geen sterke trendveranderingen gedetecteerd in de huidige CBS data.
      </div>
    `}

    <!-- Policy Coverage Gaps -->
    <h2 class="section-heading mt-32">Beleidsdekking per Leefbaarheidsdimensie</h2>
    <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:24px;margin-bottom:32px;box-shadow:var(--shadow-sm);">
      ${coverageData.map(c => `
        <div style="display:flex;align-items:center;gap:16px;padding:12px 0;border-bottom:1px solid #f3f4f6;">
          <div style="width:24px;text-align:center;font-size:18px;">${c.dim.icon}</div>
          <div style="flex:1;min-width:140px;">
            <div style="font-size:14px;font-weight:600;color:var(--text-primary);">${c.dim.label}</div>
          </div>
          <div class="coverage-bar" style="flex:2;">
            <div class="coverage-bar-track">
              <div class="coverage-bar-fill" style="width:${Math.min(c.pct * 3, 100)}%;background:${c.dim.color};"></div>
            </div>
          </div>
          <div style="width:90px;text-align:right;font-size:13px;font-weight:700;color:${c.linkedCount === 0 ? '#dc2626' : c.dim.color}">
            ${c.linkedCount} ${c.linkedCount === 1 ? 'beleid' : 'beleid'}
          </div>
          ${c.linkedCount === 0 ? '<span style="font-size:10px;font-weight:700;color:#dc2626;background:#fef2f2;padding:2px 8px;border-radius:100px;">LACUNE</span>' : ''}
        </div>
      `).join('')}
    </div>

    <!-- High Priority Policies -->
    <h2 class="section-heading mt-32">Beleid met Hoogste Prioriteitsscore</h2>
    ${priorityPolicies.length > 0 ? `
      <div style="display:flex;flex-direction:column;gap:16px;margin-bottom:32px;">
        ${priorityPolicies.slice(0, 5).map((p, i) => {
          const qolDims = matchPolicyToQol(p);
          return `
            <div class="priority-card">
              <div class="priority-card-header">
                <div style="flex:1;">
                  <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
                    <span style="font-size:20px;font-weight:800;color:var(--accent-primary);">#${i + 1}</span>
                    ${statusBadge(p.status)}
                    ${priorityBadge(p.priority_score)}
                  </div>
                  <div class="priority-card-title" style="font-size:17px;">
                    <a href="${p.url}" target="_blank" style="color:inherit;text-decoration:none;">${p.title} ↗</a>
                  </div>
                </div>
              </div>
              <div class="priority-card-body">
                <div style="margin-bottom:12px;font-size:14px;color:var(--text-secondary);line-height:1.6;">${p.summary}</div>

                <!-- QoL Dimension Tags -->
                <div style="margin-bottom:12px;">
                  ${qolDims.map(d => `
                    <span class="qol-tag" style="background:${d.color}15;color:${d.color};">${d.icon} ${d.label}</span>
                  `).join('')}
                </div>

                ${p.priority_explanation ? `
                  <!-- Priority Breakdown -->
                  <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:8px;margin-top:12px;">
                    ${Object.entries(p.priority_explanation).map(([key, val]) => {
                      const labels = {
                        trend_urgency: 'Trendurgentie',
                        population_affected: 'Bevolking geraakt',
                        policy_relevance: 'Beleidsrelevantie',
                        implementation_maturity: 'Implementatierijpheid',
                      };
                      const pct = Math.round(val * 100);
                      const barColor = pct >= 80 ? '#dc2626' : pct >= 60 ? '#E17000' : '#39870c';
                      return `
                        <div>
                          <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:4px;">${labels[key] || key}</div>
                          <div class="score-bar">
                            <div class="score-bar-fill" style="width:${pct}%;background:${barColor};"></div>
                          </div>
                          <div style="font-size:11px;font-weight:700;color:var(--text-secondary);margin-top:2px;">${pct}%</div>
                        </div>
                      `;
                    }).join('')}
                  </div>
                ` : ''}

                ${p.expected_outcomes && p.expected_outcomes.length > 0 ? `
                  <div style="margin-top:12px;padding-top:12px;border-top:1px solid #f3f4f6;">
                    <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin-bottom:6px;">Verwachte uitkomsten</div>
                    <div style="display:flex;flex-wrap:wrap;gap:6px;">
                      ${p.expected_outcomes.map(o => `
                        <span style="font-size:12px;padding:4px 10px;background:#f0fdf4;color:#166534;border-radius:100px;border:1px solid #bbf7d0;">✓ ${o}</span>
                      `).join('')}
                    </div>
                  </div>
                ` : ''}

                ${p.evidence_confidence !== undefined ? `
                  <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">
                    Bewijsvertrouwen: ${confidenceBadge(p.evidence_confidence)}
                  </div>
                ` : ''}
              </div>
            </div>
          `;
        }).join('')}
      </div>
    ` : `
      <div style="padding:32px;text-align:center;color:var(--text-muted);background:#fff;border-radius:var(--radius-lg);border:1px solid var(--border);margin-bottom:32px;">
        Geen beleidsstukken met prioriteitsscores gevonden. Voeg V2-beleidsdata toe om de prioriteitsengine te activeren.
      </div>
    `}

    <!-- Recommended Themes -->
    <h2 class="section-heading mt-32">Aanbevelingen voor Verdere Analyse</h2>
    <div style="background:#fff;border:1px solid var(--border);border-radius:var(--radius-lg);padding:24px;margin-bottom:32px;box-shadow:var(--shadow-sm);">
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:24px;font-size:14px;line-height:1.6;color:var(--text-secondary);">
        <div>
          <div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--accent-primary);margin-bottom:6px;">Data-uitbreiding</div>
          Voeg wijk-specifieke CBS-data toe (dataset 84583NED) om per buurt te kunnen vergelijken met het gemeentelijk gemiddelde.
        </div>
        <div>
          <div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--accent-primary);margin-bottom:6px;">Beleids-indicator koppeling</div>
          Koppel beleidsdocumenten aan meetbare CBS-uitkomsten om de effectiviteit van ingezet beleid te kunnen monitoren.
        </div>
        <div>
          <div style="font-size:13px;font-weight:700;text-transform:uppercase;letter-spacing:0.5px;color:var(--accent-primary);margin-bottom:6px;">Doelgroepanalyse</div>
          Combineer demografische trends met beleidsdoelgroepen om te identificeren welke groepen de meeste beleidsaandacht vereisen.
        </div>
      </div>
    </div>
  `;

  container.innerHTML = html;
}
