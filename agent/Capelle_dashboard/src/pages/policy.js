import { domainColor, domainIcon, statusBadge, priorityBadge, confidenceBadge, qolColor } from '../utils/format.js';
import { matchPolicyToQol, QOL_DIMENSIONS } from '../data/qol.js';

// --- Vector Math ---
function cosineSimilarity(vecA, vecB) {
  if (!vecA || !vecB || vecA.length !== vecB.length) return 0;
  let dotProduct = 0;
  let normA = 0;
  let normB = 0;
  for (let i = 0; i < vecA.length; i++) {
     dotProduct += vecA[i] * vecB[i];
     normA += vecA[i] * vecA[i];
     normB += vecB[i] * vecB[i];
  }
  if (normA === 0 || normB === 0) return 0;
  return dotProduct / (Math.sqrt(normA) * Math.sqrt(normB));
}

function getRelatedPolicies(targetPolicy, allPolicies, limit = 2) {
  const scored = allPolicies
    .filter(p => p.url !== targetPolicy.url)
    .map(p => ({
      ...p,
      similarity: cosineSimilarity(targetPolicy.embedding, p.embedding)
    }))
    .sort((a, b) => b.similarity - a.similarity);
  return scored.slice(0, limit);
}

// --- State ---
let allPolicies = [];
let filteredPolicies = [];
let currentPage = 1;
let currentDomain = '';
let currentQol = '';
let currentSort = 'title';
let searchQuery = '';
const PER_PAGE = 20;

function applyFilters() {
  let result = allPolicies;

  // Domain filter
  if (currentDomain) {
    result = result.filter(p => (p.domain || '').toLowerCase() === currentDomain.toLowerCase());
  }

  // QoL dimension filter
  if (currentQol) {
    result = result.filter(p => {
      const dims = matchPolicyToQol(p);
      return dims.some(d => d.id === currentQol);
    });
  }

  // Text search
  if (searchQuery) {
    const q = searchQuery.toLowerCase();
    result = result.filter(p =>
      (p.title || '').toLowerCase().includes(q) ||
      (p.target_group || '').toLowerCase().includes(q) ||
      (p.summary || '').toLowerCase().includes(q) ||
      (p.domain || '').toLowerCase().includes(q) ||
      (p.legal_basis || '').toLowerCase().includes(q) ||
      (p.implications || '').toLowerCase().includes(q) ||
      (p.benefits || '').toLowerCase().includes(q) ||
      (p.expected_outcomes || []).join(' ').toLowerCase().includes(q) ||
      (p.linked_indicators || []).join(' ').toLowerCase().includes(q)
    );
  }

  // Sort
  if (currentSort === 'impact') {
    result.sort((a, b) => (b.impact_score || 0) - (a.impact_score || 0));
  } else if (currentSort === 'priority') {
    result.sort((a, b) => (b.priority_score || 0) - (a.priority_score || 0));
  } else if (currentSort === 'date') {
    result.sort((a, b) => (b.date || '').localeCompare(a.date || ''));
  } else {
    result.sort((a, b) => (a.title || '').localeCompare(b.title || ''));
  }

  filteredPolicies = result;
  currentPage = 1;
}

function getTotalPages() {
  return Math.max(1, Math.ceil(filteredPolicies.length / PER_PAGE));
}

function getPageSlice() {
  const start = (currentPage - 1) * PER_PAGE;
  return filteredPolicies.slice(start, start + PER_PAGE);
}

export async function renderPolicy(container) {
  container.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Beleidsdata</h1>
      <p class="page-subtitle">Doorzoek alle geïndexeerde regelgeving van de gemeente Capelle aan den IJssel. Samenvattingen zijn AI-geassisteerd — raadpleeg altijd de originele tekst.</p>
    </div>
    <div style="padding: 40px; text-align: center; color: var(--text-muted);">
      <div style="width:28px;height:28px;border:3px solid var(--border);border-top-color:var(--accent-primary);border-radius:50%;animation:spin 1s linear infinite;display:inline-block;margin-bottom:16px;"></div>
      <div style="font-size:15px;">Beleidsdatabase laden...</div>
    </div>
  `;

  // Fetch data
  let policies = [];
  try {
    const res = await fetch('/data/policy_database.json');
    if (!res.ok) throw new Error("Not found");
    const db = await res.json();
    policies = db.policies || [];
  } catch (error) {
    console.warn("Could not load /data/policy_database.json. Trying import fallback.");
  }

  if (policies.length === 0) {
    try {
      const res2 = await fetch('./data/policy_database.json');
      if (res2.ok) { const db2 = await res2.json(); policies = db2.policies || []; }
    } catch (e) {
      console.log("No fallback found.");
    }
  }

  allPolicies = policies;

  // Inject styles
  if (!document.getElementById('policy-db-styles')) {
    const style = document.createElement('style');
    style.id = 'policy-db-styles';
    style.innerHTML = `
      #policy-search:focus { border-color: var(--accent-primary); box-shadow: 0 0 0 3px rgba(21, 66, 115, 0.1); }
      .policy-card { background: white; border-radius: var(--radius-lg); box-shadow: var(--shadow-sm); border: 1px solid var(--border-light); transition: transform 0.2s; overflow: hidden; }
      .policy-card:hover { border-color: var(--accent-primary); box-shadow: var(--shadow-md); }
      .metadata-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 16px; margin-top: 16px; }
      .meta-item { display: flex; flex-direction: column; gap: 4px; }
      .meta-label { font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700; }
      .meta-value { font-size: 14px; color: var(--text-primary); line-height: 1.4; }
      .domain-tab { padding: 8px 16px; border-radius: 100px; border: 1px solid var(--border-light); background: white; cursor: pointer; font-size: 13px; font-weight: 600; color: var(--text-muted); transition: all 0.2s; }
      .domain-tab:hover { border-color: var(--accent-primary); color: var(--accent-primary); }
      .domain-tab.active { background: var(--accent-primary); color: white; border-color: var(--accent-primary); }
      .sort-btn { padding: 6px 14px; border-radius: 6px; border: 1px solid var(--border-light); background: white; cursor: pointer; font-size: 12px; font-weight: 600; color: var(--text-muted); transition: all 0.2s; }
      .sort-btn.active { background: #f0f4f8; color: var(--accent-primary); border-color: var(--accent-primary); }
      .pagination { display: flex; align-items: center; justify-content: center; gap: 8px; margin-top: 24px; }
      .page-btn { width: 36px; height: 36px; border-radius: 8px; border: 1px solid var(--border-light); background: white; cursor: pointer; font-size: 14px; font-weight: 600; color: var(--text-muted); display: flex; align-items: center; justify-content: center; transition: all 0.15s; }
      .page-btn:hover:not(:disabled) { border-color: var(--accent-primary); color: var(--accent-primary); }
      .page-btn.active { background: var(--accent-primary); color: white; border-color: var(--accent-primary); }
      .page-btn:disabled { opacity: 0.4; cursor: default; }
      .ai-tag { display: inline-flex; align-items: center; gap: 3px; font-size: 10px; font-weight: 700; color: #6366f1; background: #eef2ff; padding: 2px 7px; border-radius: 100px; text-transform: uppercase; vertical-align: middle; margin-left: 4px; }
    `;
    document.head.appendChild(style);
  }

  // Get unique domains
  const domains = [...new Set(allPolicies.map(p => p.domain).filter(Boolean))].sort();

  applyFilters();
  renderFullPage(container, domains);
}

function renderFullPage(container, domains) {
  const pageSlice = getPageSlice();
  const totalPages = getTotalPages();

  let html = `
    <div class="page-header">
      <h1 class="page-title">Beleidsdata</h1>
      <p class="page-subtitle">${allPolicies.length} geïndexeerde regelgevingen van de gemeente Capelle aan den IJssel. Samenvattingen zijn AI-geassisteerd — raadpleeg altijd de originele tekst voor juridische zekerheid.</p>
    </div>

    <!-- Controls -->
    <div style="margin: 0 24px 16px;">
      <!-- QoL Dimension Filter -->
      <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 12px;">
        <button class="domain-tab ${!currentQol ? 'active' : ''}" data-qol="" style="font-size:12px;padding:6px 12px;">🧭 Alle dimensies</button>
        ${QOL_DIMENSIONS.map(d => `
          <button class="domain-tab ${currentQol === d.id ? 'active' : ''}" data-qol="${d.id}" style="font-size:12px;padding:6px 12px;">${d.icon} ${d.label}</button>
        `).join('')}
      </div>

      <!-- Domain Tabs -->
      <div style="display: flex; flex-wrap: wrap; gap: 8px; margin-bottom: 16px;">
        <button class="domain-tab ${!currentDomain ? 'active' : ''}" data-domain="">Alle (${allPolicies.length})</button>
        ${domains.map(d => {
          const count = allPolicies.filter(p => p.domain === d).length;
          return `<button class="domain-tab ${currentDomain === d ? 'active' : ''}" data-domain="${d}">${domainIcon(d)} ${d} (${count})</button>`;
        }).join('')}
      </div>

      <!-- Search + Sort Row -->
      <div style="display: flex; gap: 12px; align-items: center;">
        <input type="text" id="policy-search" value="${searchQuery}" placeholder="Zoek op doelgroep, trefwoord of rechtsgrondslag — bijv. kinderen, Wmo, parkeren..." style="flex: 1; padding: 14px 16px; border: 1px solid var(--border-light); border-radius: var(--radius-md); font-size: 15px; outline: none; transition: border-color 0.2s; font-family: inherit;">
        <div style="display: flex; gap: 6px; flex-shrink: 0;">
          <button class="sort-btn ${currentSort === 'title' ? 'active' : ''}" data-sort="title">A-Z</button>
          <button class="sort-btn ${currentSort === 'priority' ? 'active' : ''}" data-sort="priority">Prioriteit</button>
          <button class="sort-btn ${currentSort === 'impact' ? 'active' : ''}" data-sort="impact">Relevantie</button>
          <button class="sort-btn ${currentSort === 'date' ? 'active' : ''}" data-sort="date">Datum</button>
        </div>
      </div>
    </div>

    <!-- Results Info -->
    <div style="margin: 0 24px 16px; font-size: 13px; color: var(--text-muted);">
      ${filteredPolicies.length} resultaten gevonden${currentDomain ? ` in ${currentDomain}` : ''}${currentQol ? ` (${QOL_DIMENSIONS.find(d=>d.id===currentQol)?.label || currentQol})` : ''}${searchQuery ? ` voor "${searchQuery}"` : ''} — pagina ${currentPage} van ${totalPages}
    </div>

    <!-- Policy List -->
    <div id="policy-list" style="display: flex; flex-direction: column; gap: 16px; margin: 0 24px;">
      ${renderPolicyCards(pageSlice, allPolicies)}
    </div>

    <!-- Pagination -->
    ${totalPages > 1 ? `
      <div class="pagination">
        <button class="page-btn" data-page="prev" ${currentPage <= 1 ? 'disabled' : ''}>‹</button>
        ${generatePageButtons(currentPage, totalPages)}
        <button class="page-btn" data-page="next" ${currentPage >= totalPages ? 'disabled' : ''}>›</button>
      </div>
    ` : ''}
  `;

  container.innerHTML = html;
  attachListeners(container, domains);
}

function generatePageButtons(current, total) {
  const buttons = [];
  const range = 2;

  for (let i = 1; i <= total; i++) {
    if (i === 1 || i === total || (i >= current - range && i <= current + range)) {
      buttons.push(`<button class="page-btn ${i === current ? 'active' : ''}" data-page="${i}">${i}</button>`);
    } else if (buttons.length > 0 && !buttons[buttons.length - 1].includes('…')) {
      buttons.push(`<span style="color: var(--text-muted); font-size: 14px;">…</span>`);
    }
  }
  return buttons.join('');
}

function attachListeners(container, domains) {
  // QoL dimension tabs
  container.querySelectorAll('[data-qol]').forEach(btn => {
    btn.addEventListener('click', () => {
      currentQol = btn.dataset.qol;
      applyFilters();
      renderFullPage(container, domains);
    });
  });

  // Domain tabs
  container.querySelectorAll('[data-domain]').forEach(btn => {
    if (btn.dataset.qol !== undefined) return; // skip QoL buttons
    btn.addEventListener('click', () => {
      currentDomain = btn.dataset.domain;
      applyFilters();
      renderFullPage(container, domains);
    });
  });

  // Search
  const searchInput = container.querySelector('#policy-search');
  let searchTimeout;
  searchInput.addEventListener('input', (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => {
      searchQuery = e.target.value;
      applyFilters();
      renderFullPage(container, domains);
      const newInput = container.querySelector('#policy-search');
      if (newInput) {
        newInput.focus();
        newInput.setSelectionRange(newInput.value.length, newInput.value.length);
      }
    }, 300);
  });

  // Sort
  container.querySelectorAll('.sort-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      currentSort = btn.dataset.sort;
      applyFilters();
      renderFullPage(container, domains);
    });
  });

  // Pagination
  container.querySelectorAll('.page-btn').forEach(btn => {
    btn.addEventListener('click', () => {
      const p = btn.dataset.page;
      if (p === 'prev') currentPage = Math.max(1, currentPage - 1);
      else if (p === 'next') currentPage = Math.min(getTotalPages(), currentPage + 1);
      else currentPage = parseInt(p);
      renderFullPage(container, domains);
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
  });
}

function renderPolicyCards(subset, allPolicies) {
  if (subset.length === 0) {
    return `<div style="padding: 40px; text-align: center; color: var(--text-muted);">Geen beleidsstukken gevonden voor deze zoekopdracht.</div>`;
  }

  return subset.map(p => {
    const related = getRelatedPolicies(p, allPolicies);
    const hasImplications = p.implications && p.implications.toLowerCase() !== 'niet vermeld';
    const hasBenefits = p.benefits && p.benefits.toLowerCase() !== 'niet vermeld';
    const hasBudget = p.budget_spent && !p.budget_spent.toLowerCase().includes('niet vermeld');
    const qolDims = matchPolicyToQol(p);
    const hasV2 = p.priority_score !== undefined;

    return `
      <div class="policy-card">
        <div style="padding: 24px;">
          <!-- Header: Domain + Status + Title -->
          <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 16px;">
            <div style="flex:1;">
              <div style="display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-bottom: 12px;">
                <span style="display: inline-block; padding: 4px 12px; background: ${domainColor(p.domain)}20; color: ${domainColor(p.domain)}; border-radius: 100px; font-size: 12px; font-weight: 700;">
                  ${domainIcon(p.domain)} ${p.domain}
                </span>
                ${p.status ? statusBadge(p.status) : ''}
                ${p.policy_type ? `<span style="font-size:11px;padding:3px 8px;background:#f1f5f9;color:#475569;border-radius:100px;font-weight:600;">${p.policy_type}</span>` : ''}
              </div>
              <h3 style="margin: 0; font-size: 18px; color: var(--accent-primary); line-height: 1.3;">
                <a href="${p.url}" target="_blank" style="color: inherit; text-decoration: none;">${p.title} ↗</a>
              </h3>
            </div>
            <div style="text-align: right; flex-shrink: 0; margin-left: 16px;">
              ${hasV2 ? `
                <div style="font-size: 24px; font-weight: 700; color: var(--text-primary);">${Math.round(p.priority_score * 100)}%</div>
                <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); font-weight: 600;">Prioriteit</div>
              ` : `
                <div style="font-size: 24px; font-weight: 700; color: var(--text-primary);">${p.impact_score}</div>
                <div style="font-size: 10px; text-transform: uppercase; color: var(--text-muted); font-weight: 600;">Relevantie</div>
              `}
            </div>
          </div>

          <!-- QoL Dimension Tags -->
          <div style="margin-bottom: 12px;">
            ${qolDims.map(d => `
              <span class="qol-tag" style="background:${d.color}15;color:${d.color};">${d.icon} ${d.label}</span>
            `).join('')}
          </div>

          <!-- Summary -->
          <div style="font-size: 15px; color: var(--text-secondary); line-height: 1.6; margin-bottom: 16px; position: relative; padding-left: 12px; border-left: 3px solid var(--border);">
            ${p.summary}
          </div>

          ${hasImplications || hasBenefits ? `
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 16px;">
            ${hasImplications ? `
              <div style="background: #fef3c7; padding: 14px; border-radius: var(--radius-md); border-left: 3px solid #f59e0b;">
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #92400e; margin-bottom: 6px;">Implicaties</div>
                <div style="font-size: 13px; color: #78350f; line-height: 1.5;">${p.implications}</div>
              </div>
            ` : ''}
            ${hasBenefits ? `
              <div style="background: #ecfdf5; padding: 14px; border-radius: var(--radius-md); border-left: 3px solid #10b981;">
                <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: #065f46; margin-bottom: 6px;">Voordelen</div>
                <div style="font-size: 13px; color: #064e3b; line-height: 1.5;">${p.benefits}</div>
              </div>
            ` : ''}
          </div>
          ` : ''}

          ${p.expected_outcomes && p.expected_outcomes.length > 0 ? `
          <div style="margin-bottom: 16px;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Verwachte Uitkomsten</div>
            <div style="display: flex; flex-wrap: wrap; gap: 6px;">
              ${p.expected_outcomes.map(o => `
                <span style="font-size: 12px; padding: 4px 10px; background: #f0fdf4; color: #166534; border-radius: 100px; border: 1px solid #bbf7d0;">✓ ${o}</span>
              `).join('')}
            </div>
          </div>
          ` : ''}

          ${p.linked_indicators && p.linked_indicators.length > 0 ? `
          <div style="margin-bottom: 16px;">
            <div style="font-size: 11px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px;">Gekoppelde Indicatoren</div>
            <div style="display: flex; flex-wrap: wrap; gap: 6px;">
              ${p.linked_indicators.map(ind => `
                <span style="font-size: 12px; padding: 4px 10px; background: #eff6ff; color: #1e40af; border-radius: 100px; border: 1px solid #bfdbfe;">📊 ${ind}</span>
              `).join('')}
            </div>
          </div>
          ` : ''}

          <div style="background: var(--bg-primary); padding: 16px; border-radius: var(--radius-md); border-left: 3px solid var(--border-light);">
            <div style="margin-bottom: 16px;">
              <span class="meta-label">Doelgroep</span>
              <div class="meta-value" style="font-weight: 600;">${p.target_group}</div>
            </div>

            <div class="metadata-grid">
              ${hasBudget ? `
              <div class="meta-item">
                <span class="meta-label">Budget</span>
                <span class="meta-value" style="color: #F43F5E; font-weight: 600;">${p.budget_spent}</span>
              </div>
              ` : ''}
              <div class="meta-item">
                <span class="meta-label">Opgesteld door</span>
                <span class="meta-value">${p.author}</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Goedgekeurd</span>
                <span class="meta-value">${p.approved_by} (${p.date})</span>
              </div>
              <div class="meta-item">
                <span class="meta-label">Juridische Basis</span>
                <span class="meta-value">${p.legal_basis}</span>
              </div>
              ${p.time_horizon ? `
              <div class="meta-item">
                <span class="meta-label">Tijdshorizon</span>
                <span class="meta-value">${p.time_horizon}</span>
              </div>
              ` : ''}
              ${p.geographic_scope ? `
              <div class="meta-item">
                <span class="meta-label">Geografisch bereik</span>
                <span class="meta-value">${p.geographic_scope.join(', ')}</span>
              </div>
              ` : ''}
            </div>

            ${hasV2 && p.priority_explanation ? `
            <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--border);">
              <div style="font-size:11px;font-weight:700;text-transform:uppercase;color:var(--text-muted);margin-bottom:8px;">Prioriteitsuitsplitsing</div>
              <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;">
                ${Object.entries(p.priority_explanation).map(([key, val]) => {
                  const labels = { trend_urgency: 'Trendurgentie', population_affected: 'Bevolking geraakt', policy_relevance: 'Beleidsrelevantie', implementation_maturity: 'Implementatierijpheid' };
                  const pct = Math.round(val * 100);
                  const barColor = pct >= 80 ? '#dc2626' : pct >= 60 ? '#E17000' : '#39870c';
                  return `
                    <div>
                      <div style="font-size:11px;font-weight:600;color:var(--text-muted);margin-bottom:3px;">${labels[key] || key}</div>
                      <div class="score-bar"><div class="score-bar-fill" style="width:${pct}%;background:${barColor};"></div></div>
                      <div style="font-size:10px;font-weight:700;color:var(--text-secondary);margin-top:2px;">${pct}%</div>
                    </div>
                  `;
                }).join('')}
              </div>
            </div>
            ` : ''}

            ${p.evidence_confidence !== undefined ? `
            <div style="margin-top:8px;font-size:12px;color:var(--text-muted);">Bewijsvertrouwen: ${confidenceBadge(p.evidence_confidence)}</div>
            ` : ''}
          </div>
        </div>

        <!-- Related Policies Footer -->
        <div style="background: #f8fafc; padding: 16px 24px; border-top: 1px solid var(--border-light);">
          <div style="font-size: 12px; font-weight: 700; text-transform: uppercase; color: var(--text-muted); letter-spacing:0.5px; margin-bottom: 12px;">
            Gerelateerde Regelgevingen
          </div>
          <div style="display: flex; gap: 16px; flex-wrap: wrap;">
            ${related.map(r => `
              <div style="flex: 1; min-width: 250px; background: white; padding: 12px; border-radius: var(--radius-sm); border: 1px solid #e2e8f0;">
                <div style="font-size: 11px; float: right; color: var(--accent-primary); font-weight: 600;">${(r.similarity * 100).toFixed(0)}% overeenkomst</div>
                <div style="font-size: 12px; color: var(--text-muted); font-weight: 600; margin-bottom: 4px;">${r.domain}</div>
                <div style="font-size: 13px; font-weight: 600; color: var(--text-primary);"><a href="${r.url}" target="_blank" style="color: inherit; text-decoration: none;">${r.title}</a></div>
              </div>
            `).join('')}
          </div>
        </div>
      </div>
    `;
  }).join('');
}
