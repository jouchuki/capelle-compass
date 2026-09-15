import { domainColor, domainIcon } from '../utils/format.js';
import { QOL_DIMENSIONS, matchPolicyToQol } from '../data/qol.js';
import { Chart, DoughnutController, ArcElement, Tooltip, Legend } from 'chart.js';
Chart.register(DoughnutController, ArcElement, Tooltip, Legend);

function computeStatsFromPolicies(policies) {
  const domains = {};
  const impactScores = [];
  const targetGroups = {};
  const legalBases = {};
  const approvedBy = {};
  const dates = {};
  let noBudgetCount = 0;
  let v2Count = 0;

  for (const m of policies) {
    const d = m.domain || 'Onbekend';
    domains[d] = (domains[d] || 0) + 1;

    const s = m.impact_score;
    if (typeof s === 'number') impactScores.push(s);

    const t = m.target_group || 'Onbekend';
    targetGroups[t] = (targetGroups[t] || 0) + 1;

    const lb = m.legal_basis || 'Onbekend';
    legalBases[lb] = (legalBases[lb] || 0) + 1;

    const ab = m.approved_by || 'Onbekend';
    approvedBy[ab] = (approvedBy[ab] || 0) + 1;

    const dt = m.date || 'unknown';
    const yr = dt && dt.length >= 4 && /^\d{4}/.test(dt) ? dt.substring(0, 4) : 'onbekend';
    dates[yr] = (dates[yr] || 0) + 1;

    const budget = String(m.budget_spent || '').toLowerCase();
    if (budget.includes('niet vermeld') || budget.includes('unknown') || budget === '') noBudgetCount++;

    if (m.priority_score !== null && m.priority_score !== undefined) v2Count++;
  }

  const total = policies.length;
  const avgImpact = impactScores.length > 0 ? Math.round(10 * impactScores.reduce((a, b) => a + b, 0) / impactScores.length) / 10 : 0;
  const unknownLB = (legalBases['Niet vermeld'] || 0) + (legalBases['Onbekend'] || 0);
  const lbPct = total > 0 ? Math.round(100 * (total - unknownLB) / total) : 0;

  const sortDesc = obj => Object.fromEntries(Object.entries(obj).sort((a, b) => b[1] - a[1]));

  return {
    total,
    domains: sortDesc(domains),
    avg_impact_score: avgImpact,
    legal_basis_with_data_percentage: lbPct,
    target_groups: Object.fromEntries(Object.entries(targetGroups).sort((a, b) => b[1] - a[1]).slice(0, 15)),
    legal_bases: Object.fromEntries(Object.entries(legalBases).sort((a, b) => b[1] - a[1]).slice(0, 15)),
    approved_by: Object.fromEntries(Object.entries(approvedBy).sort((a, b) => b[1] - a[1]).slice(0, 10)),
    years: Object.fromEntries(Object.entries(dates).sort((a, b) => a[0].localeCompare(b[0]))),
    v2_count: v2Count,
  };
}

export async function renderInsights(container) {
  container.innerHTML = `<div class="page-header"><h1 class="page-title">Beleidsinzichten</h1><p class="page-subtitle">Laden...</p></div><div style="padding:40px;text-align:center;color:var(--text-muted);"><div style="width:24px;height:24px;border:3px solid var(--border);border-top-color:var(--accent-primary);border-radius:50%;animation:spin 1s linear infinite;display:inline-block;"></div></div>`;

  // Load policies from static JSON
  let policies = [];
  try {
    const r = await fetch('/data/policy_database.json');
    if (r.ok) { const d = await r.json(); policies = d.policies || []; }
  } catch (e) {}
  if (!policies.length) {
    try { const f = await fetch('./data/policy_database.json'); if (f.ok) { const d = await f.json(); policies = d.policies || []; } } catch (e2) {}
  }

  if (!policies.length) {
    container.innerHTML = `<div class="page-header"><h1 class="page-title">Beleidsinzichten</h1></div><div style="margin:24px;padding:20px;background:#fef2f2;border:1px solid #fecaca;border-radius:var(--radius-lg);color:#991b1b;"><strong>Geen beleidsdata beschikbaar.</strong> Voer eerst <code>python scripts/export_db_to_ui.py</code> uit om de database te exporteren.</div>`;
    return;
  }

  // Try API stats first, fall back to client-side computation
  let stats;
  try {
    const res = await fetch('/api/policies/stats');
    if (!res.ok) throw new Error(`Status ${res.status}`);
    stats = await res.json();
  } catch (err) {
    stats = computeStatsFromPolicies(policies);
  }

  const qolCounts = {};
  QOL_DIMENSIONS.forEach(d => { qolCounts[d.id] = 0; });
  policies.forEach(p => { matchPolicyToQol(p).forEach(d => { qolCounts[d.id] = (qolCounts[d.id] || 0) + 1; }); });

  if (!document.getElementById('insights-styles')) {
    const s = document.createElement('style');
    s.id = 'insights-styles';
    s.innerHTML = `.insight-stat-card{background:white;padding:24px;border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);border-left:5px solid var(--accent-primary);transition:transform .15s,box-shadow .15s}.insight-stat-card:hover{transform:translateY(-2px);box-shadow:var(--shadow-md)}.insight-stat-label{font-size:12px;text-transform:uppercase;font-weight:700;color:var(--text-muted);letter-spacing:.5px}.insight-stat-value{font-size:32px;font-weight:800;color:var(--text-primary);margin-top:6px}.insight-stat-sub{font-size:13px;color:var(--text-muted);margin-top:4px}.takeaway-card{background:white;border-radius:var(--radius-lg);padding:20px 24px;box-shadow:var(--shadow-sm);border:1px solid var(--border-light);display:flex;align-items:flex-start;gap:14px;transition:border-color .2s}.takeaway-card:hover{border-color:var(--accent-primary)}.takeaway-icon{font-size:28px;flex-shrink:0;margin-top:2px}.takeaway-text{font-size:15px;color:var(--text-secondary);line-height:1.6}.takeaway-text strong{color:var(--text-primary)}.group-section{background:white;border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);border:1px solid var(--border-light);overflow:hidden;margin-bottom:24px}.group-header{padding:16px 24px;font-size:16px;font-weight:700;color:var(--text-primary);border-bottom:1px solid var(--border-light);display:flex;align-items:center;gap:10px;background:var(--bg-primary)}.group-row{padding:12px 24px;display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #f3f4f6;cursor:pointer;transition:background .15s}.group-row:last-child{border-bottom:none}.group-row:hover{background:#f8fafc}.group-name{font-size:14px;color:var(--text-secondary);font-weight:500}.group-count{font-size:13px;font-weight:700;color:var(--accent-primary);background:rgba(21,66,115,.08);padding:4px 12px;border-radius:100px}`;
    document.head.appendChild(s);
  }

  const { total, domains, avg_impact_score, legal_basis_with_data_percentage, target_groups, legal_bases, approved_by, years } = stats;
  const de = Object.entries(domains), td = de[0], tp = Math.round(100 * td[1] / total);
  const ye = Object.entries(years).filter(([y]) => y !== 'onbekend' && y !== 'unknown').sort((a, b) => a[0].localeCompare(b[0]));
  const myc = Math.max(...ye.map(([, c]) => c));
  const te = Object.entries(target_groups).slice(0, 8), le = Object.entries(legal_bases).slice(0, 8), ae = Object.entries(approved_by).slice(0, 6);
  container.innerHTML = buildInsightsHTML(total, de, td, tp, avg_impact_score, legal_basis_with_data_percentage, ye, myc, te, le, ae);
  renderCharts(de, total, qolCounts);
}

function buildInsightsHTML(total, de, td, tp, avg, lbp, ye, myc, te, le, ae) {
  return `
<div class="page-header"><h1 class="page-title">Beleidsinzichten</h1><p class="page-subtitle">Analytisch overzicht van alle geïndexeerde regelgeving van de gemeente Capelle aan den IJssel — verdeeld naar domein, doelgroep en rechtsgrondslag.</p></div>
<div style="margin:0 0 24px;padding:14px 18px;background:#f8fafc;border:1px solid var(--border);border-left:3px solid var(--accent-primary);border-radius:var(--radius-md);font-size:13px;color:var(--text-secondary);line-height:1.6;">
  <strong>Databron:</strong> ${total} regelgevingen van <a href="https://lokaleregelgeving.overheid.nl" target="_blank" style="color:var(--accent-blue);">lokaleregelgeving.overheid.nl</a>. Samenvattingen en categorisering zijn AI-geassisteerd — raadpleeg altijd de originele tekst voor juridische zekerheid.
</div>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:20px;margin-bottom:32px;">
  <div class="insight-stat-card" style="border-left-color:var(--accent-primary);"><div class="insight-stat-label">Totaal regelgevingen</div><div class="insight-stat-value">${total}</div><div class="insight-stat-sub">geïndexeerde documenten</div></div>
  <div class="insight-stat-card" style="border-left-color:#E17000;"><div class="insight-stat-label">Beleidsdomeinen</div><div class="insight-stat-value">${de.length}</div><div class="insight-stat-sub">categorieën</div></div>
  <div class="insight-stat-card" style="border-left-color:#39870c;"><div class="insight-stat-label">Met rechtsgrondslag</div><div class="insight-stat-value">${lbp}%</div><div class="insight-stat-sub">verwijst naar wet of verordening</div></div>
  <div class="insight-stat-card" style="border-left-color:var(--accent-blue);"><div class="insight-stat-label">Gem. relevantiescore</div><div class="insight-stat-value">${avg}<span style="font-size:16px;color:var(--text-muted);"> /10</span></div><div class="insight-stat-sub">AI-geassisteerde inschatting</div></div>
</div>
<h2 style="font-size:18px;font-weight:700;color:var(--text-primary);margin:0 0 16px;">Belangrijkste Bevindingen</h2>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px;margin-bottom:40px;">
  <div class="takeaway-card"><div class="takeaway-text"><strong>${total} regelgevingen</strong> zijn geïndexeerd en doorzoekbaar via de Beleidsdata-module.</div></div>
  <div class="takeaway-card"><div class="takeaway-text"><strong>${tp}% van alle regelgeving</strong> valt onder het domein ${td[0]} (${td[1]} documenten).</div></div>
  <div class="takeaway-card"><div class="takeaway-text"><strong>${lbp}%</strong> van de regelgevingen verwijst naar een specifieke wettelijke rechtsgrondslag.</div></div>
  <div class="takeaway-card"><div class="takeaway-text">Gemiddelde relevantiescore: <strong>${avg}/10</strong> (AI-geassisteerde inschatting van beleidsrelevantie).</div></div>
</div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:40px;">
  <div style="background:white;border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);padding:24px;border:1px solid var(--border-light);"><div style="font-size:15px;font-weight:700;color:var(--text-primary);margin-bottom:16px;">Verdeling per Beleidsdomein</div><div style="max-width:280px;margin:0 auto;"><canvas id="domain-chart" width="280" height="280"></canvas></div><div id="domain-legend" style="margin-top:20px;display:flex;flex-direction:column;gap:8px;"></div></div>
  <div style="background:white;border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);padding:24px;border:1px solid var(--border-light);"><div style="font-size:15px;font-weight:700;color:var(--text-primary);margin-bottom:16px;">Verdeling per Leefbaarheidsdimensie</div><div style="max-width:280px;margin:0 auto;"><canvas id="qol-chart" width="280" height="280"></canvas></div><div id="qol-legend" style="margin-top:20px;display:flex;flex-direction:column;gap:8px;"></div></div>
</div>
<div style="margin-bottom:40px;"><div class="group-section"><div class="group-header">Goedkeuringsorgaan</div>${ae.map(([n, c]) => `<div class="group-row"><span class="group-name">${n}</span><span class="group-count">${c}</span></div>`).join('')}</div></div>
<h2 style="font-size:18px;font-weight:700;color:var(--text-primary);margin:0 0 16px;">Tijdlijn van Vastgestelde Regelgeving</h2>
<div style="background:white;border-radius:var(--radius-lg);box-shadow:var(--shadow-sm);padding:24px;margin-bottom:40px;border:1px solid var(--border-light);overflow-x:auto;"><div style="display:flex;align-items:flex-end;gap:4px;min-height:160px;padding-bottom:28px;">${ye.map(([y, c]) => { const p = myc > 0 ? Math.round((c / myc) * 140) : 0; return `<div style="display:flex;flex-direction:column;align-items:center;flex:1;min-width:32px;" title="${y}: ${c}"><div style="font-size:11px;font-weight:600;color:var(--text-primary);margin-bottom:4px;">${c}</div><div style="width:100%;max-width:36px;height:${Math.max(p, 4)}px;background:var(--accent-primary);border-radius:3px 3px 0 0;opacity:0.85;"></div><div style="font-size:10px;color:var(--text-muted);margin-top:6px;transform:rotate(-45deg);white-space:nowrap;">${y}</div></div>`; }).join('')}</div></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;margin-bottom:40px;">
  <div class="group-section"><div class="group-header">Beleid per Doelgroep</div>${te.map(([n, c]) => `<div class="group-row" onclick="window.location.hash='policy';setTimeout(()=>{const s=document.getElementById('policy-search');if(s){s.value='${n.replace(/'/g, "\\'")}';s.dispatchEvent(new Event('input'))}},300)"><span class="group-name">${n}</span><span class="group-count">${c}</span></div>`).join('')}</div>
  <div class="group-section"><div class="group-header">Beleid per Rechtsgrondslag</div>${le.map(([n, c]) => `<div class="group-row" onclick="window.location.hash='policy';setTimeout(()=>{const s=document.getElementById('policy-search');if(s){s.value='${n.replace(/'/g, "\\'")}';s.dispatchEvent(new Event('input'))}},300)"><span class="group-name">${n}</span><span class="group-count">${c}</span></div>`).join('')}</div>
</div>`;
}

function renderCharts(de, total, qolCounts) {
  const ctx = document.getElementById('domain-chart');
  if (ctx) {
    new Chart(ctx, { type: 'doughnut', data: { labels: de.map(([d]) => d), datasets: [{ data: de.map(([, c]) => c), backgroundColor: de.map(([d]) => domainColor(d)), borderWidth: 2, borderColor: '#fff' }] }, options: { responsive: true, maintainAspectRatio: true, cutout: '55%', plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.label}: ${c.parsed} (${Math.round(100 * c.parsed / total)}%)` } } } } });
    const leg = document.getElementById('domain-legend');
    if (leg) leg.innerHTML = de.map(([d, c]) => `<div style="display:flex;align-items:center;justify-content:space-between;"><div style="display:flex;align-items:center;gap:10px;"><div style="width:14px;height:14px;border-radius:4px;background:${domainColor(d)};"></div><span style="font-size:14px;font-weight:500;color:var(--text-secondary);">${domainIcon(d)} ${d}</span></div><span style="font-size:14px;font-weight:700;color:var(--text-primary);">${c} <span style="font-size:12px;color:var(--text-muted);font-weight:400;">(${Math.round(100 * c / total)}%)</span></span></div>`).join('');
  }
  const qctx = document.getElementById('qol-chart');
  if (qctx) {
    const ql = QOL_DIMENSIONS.map(d => d.label), qv = QOL_DIMENSIONS.map(d => qolCounts[d.id] || 0), qc = QOL_DIMENSIONS.map(d => d.color);
    new Chart(qctx, { type: 'doughnut', data: { labels: ql, datasets: [{ data: qv, backgroundColor: qc, borderWidth: 2, borderColor: '#fff' }] }, options: { responsive: true, maintainAspectRatio: true, cutout: '55%', plugins: { legend: { display: false }, tooltip: { callbacks: { label: c => ` ${c.label}: ${c.parsed} beleidsstukken` } } } } });
    const qleg = document.getElementById('qol-legend'), tq = qv.reduce((a, b) => a + b, 0) || 1;
    if (qleg) qleg.innerHTML = QOL_DIMENSIONS.map(d => { const c = qolCounts[d.id] || 0; return `<div style="display:flex;align-items:center;justify-content:space-between;"><div style="display:flex;align-items:center;gap:10px;"><div style="width:14px;height:14px;border-radius:4px;background:${d.color};"></div><span style="font-size:14px;font-weight:500;color:var(--text-secondary);">${d.icon} ${d.label}</span></div><span style="font-size:14px;font-weight:700;color:var(--text-primary);">${c} <span style="font-size:12px;color:var(--text-muted);font-weight:400;">(${Math.round(100 * c / tq)}%)</span></span></div>`; }).join('');
  }
}
