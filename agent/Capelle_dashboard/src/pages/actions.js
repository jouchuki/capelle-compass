// Action Priority Matrix — sortable table driven by LIVE CBS DATA
import { FEATURES, getLatest, getTrend } from '../data/features.js';
import { domainIcon, domainColor, formatValue, trendArrow, qolIcon, qolColor } from '../utils/format.js';
import { QOL_DIMENSIONS } from '../data/qol.js';

let sortCol = 'indicator';
let sortAsc = true;
let searchTerm = '';

// Map features to QoL dimensions
function getFeatureQol(featureKey) {
  for (const dim of QOL_DIMENSIONS) {
    if (dim.cbsFeatures.includes(featureKey)) return dim;
  }
  return null;
}

function getTableData() {
  const data = [];
  Object.keys(FEATURES).forEach(key => {
    const feat = FEATURES[key];
    const latest = getLatest(key);
    if (!latest) return;
    
    const trendNum = getTrend(key);
    const tInfo = trendArrow(trendNum);
    const qol = getFeatureQol(key);
    
    // Suggest an action based on the trend
    let action = 'MONITOR';
    let actionColor = 'var(--text-muted)';
    let actionBg = 'var(--bg-primary)';
    let actionBorder = 'var(--border)';
    if (trendNum > 10) { action = 'URGENT'; actionColor = '#991b1b'; actionBg = '#fef2f2'; actionBorder = '#fecaca'; }
    else if (trendNum > 5) { action = 'EVALUEER BELEID'; actionColor = 'var(--accent-primary)'; actionBg = 'rgba(21,66,115,0.1)'; actionBorder = 'var(--accent-primary)'; }
    else if (trendNum < -5) { action = 'ONDERZOEK OPZET'; actionColor = '#16a34a'; actionBg = '#f0fdf4'; actionBorder = '#bbf7d0'; }
    
    let format = 'number';
    if(feat.unit === 'k€') format = 'decimal1';
    else if(feat.unit === 'pers') format = 'decimal2';
    
    data.push({
      indicator: feat.label,
      domain: feat.domain,
      qol: qol,
      valueNum: latest.value,
      valueStr: formatValue(latest.value, format, feat.unit),
      year: latest.year,
      trendNum: trendNum || 0,
      trendStr: `${tInfo.arrow} ${tInfo.text || '—'}`,
      trendCls: tInfo.cls,
      action, actionColor, actionBg, actionBorder,
    });
  });
  return data;
}

function getSorted(data) {
  let filtered = data;
  if (searchTerm) {
    const q = searchTerm.toLowerCase();
    filtered = data.filter(a => 
      a.indicator.toLowerCase().includes(q) || 
      a.domain.toLowerCase().includes(q) ||
      a.action.toLowerCase().includes(q) ||
      (a.qol && a.qol.label.toLowerCase().includes(q))
    );
  }
  return [...filtered].sort((a, b) => {
    let va = a[sortCol], vb = b[sortCol];
    if (typeof va === 'string') va = va.toLowerCase();
    if (typeof vb === 'string') vb = vb.toLowerCase();
    if (va < vb) return sortAsc ? -1 : 1;
    if (va > vb) return sortAsc ? 1 : -1;
    return 0;
  });
}

function renderTableBody(container) {
  const tbody = container.querySelector('.action-tbody');
  const tableData = getTableData();
  const sorted = getSorted(tableData);
  
  tbody.innerHTML = sorted.map(a => `
    <tr>
      <td>
        <div style="font-weight: 600; color: var(--text-secondary);">${a.indicator}</div>
      </td>
      <td>
        <span class="domain-tag" style="background: var(--bg-primary); border: 1px solid var(--border); color: ${domainColor(a.domain)}">
          ${domainIcon(a.domain)} ${a.domain}
        </span>
      </td>
      <td>
        ${a.qol ? `<span class="qol-tag" style="background:${a.qol.color}15;color:${a.qol.color};font-size:11px;padding:2px 8px;">${a.qol.icon} ${a.qol.label}</span>` : '<span style="font-size:11px;color:var(--text-muted);">—</span>'}
      </td>
      <td style="font-family: monospace; font-weight: 600; font-size: 14px;">
        ${a.valueStr} <span style="font-size: 11px; color: var(--text-muted); font-weight: normal;">(${a.year})</span>
      </td>
      <td>
        <span class="kpi-trend trend-${a.trendCls}" style="font-weight:700;">${a.trendStr}</span>
      </td>
      <td>
        <span class="action-badge" style="background:${a.actionBg};color:${a.actionColor};border:1px solid ${a.actionBorder}">${a.action}</span>
      </td>
    </tr>
  `).join('');
}

export function renderActions(container) {
  const columns = [
    { key: 'indicator', label: 'Indicator' },
    { key: 'domain', label: 'Domein' },
    { key: 'qol', label: 'Leefkwaliteit' },
    { key: 'valueNum', label: 'Actuele Waarde' },
    { key: 'trendNum', label: 'Trend (5jr)' },
    { key: 'action', label: 'Actie' },
  ];

  container.innerHTML = `
    <div class="page-header">
      <h1 class="page-title">Actiematrix</h1>
      <p class="page-subtitle">Overzicht van gemeentelijke kernindicatoren, vijfjarige trends en beleidssignalering per leefbaarheidsdimensie — sorteer en filter om prioriteiten te identificeren.</p>
    </div>
    <div class="table-container">
      <div class="table-toolbar" style="display:flex;justify-content:space-between;align-items:center;">
        <input type="text" class="search-input" id="action-search" placeholder="Zoek op indicator, domein of dimensie..." />
        <div style="font-size:12px;color:var(--text-muted);">CBS Open Data • ${Object.keys(FEATURES).length} indicatoren</div>
      </div>
      <div style="overflow-x: auto;">
        <table class="data-table">
          <thead>
            <tr>
              ${columns.map(c => `
                <th data-col="${c.key}" class="${c.key === sortCol ? 'sorted' : ''}" style="cursor: pointer;">
                  ${c.label}
                  <span class="sort-arrow">${c.key === sortCol ? (sortAsc ? '▲' : '▼') : ''}</span>
                </th>
              `).join('')}
            </tr>
          </thead>
          <tbody class="action-tbody"></tbody>
        </table>
      </div>
    </div>
  `;

  renderTableBody(container);

  container.querySelectorAll('th[data-col]').forEach(th => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (sortCol === col) { sortAsc = !sortAsc; } 
      else { sortCol = col; sortAsc = true; }
      container.querySelectorAll('th').forEach(h => { h.classList.remove('sorted'); h.querySelector('.sort-arrow').textContent = ''; });
      th.classList.add('sorted');
      th.querySelector('.sort-arrow').textContent = sortAsc ? '▲' : '▼';
      renderTableBody(container);
    });
  });

  container.querySelector('#action-search').addEventListener('input', (e) => {
    searchTerm = e.target.value;
    renderTableBody(container);
  });
}
