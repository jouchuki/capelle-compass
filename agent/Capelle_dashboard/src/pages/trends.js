// Trends Explorer — interactive charts by domain using Live Capelle Data
import { FEATURES, DOMAINS, YEARS } from '../data/features.js';
import { createLineChart } from '../utils/charts.js';
import { domainIcon, domainColor } from '../utils/format.js';
import { pdfExportButton } from '../utils/pdf-export.js';

let currentCharts = [];

function destroyAllCharts() {
  currentCharts.forEach(c => { try { c.destroy(); } catch(e) {} });
  currentCharts = [];
}

function renderDomainCharts(container, domain, featureKeys) {
  const contentEl = container.querySelector('.charts-content');
  contentEl.innerHTML = '';
  destroyAllCharts();

  // Create individual clickable mini charts for all available metrics
  const miniGrid = document.createElement('div');
  miniGrid.style.cssText = 'display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 24px; margin-top: 16px;';
  
  featureKeys.forEach(key => {
    const feat = FEATURES[key];
    if (!feat || !feat.values.some(v => v !== null)) return; // Skip if no data
    
    const card = document.createElement('div');
    card.className = 'chart-container';
    
    const title = document.createElement('div');
    title.className = 'chart-title';
    title.style.fontSize = '15px';
    title.textContent = feat.label;
    card.appendChild(title);
    
    const wrapper = document.createElement('div');
    wrapper.className = 'chart-wrapper';
    wrapper.style.height = '250px';
    const canvas = document.createElement('canvas');
    wrapper.appendChild(canvas);
    card.appendChild(wrapper);
    miniGrid.appendChild(card);
    
    const chart = createLineChart(canvas, [{
      label: feat.label,
      data: [...feat.values],
      color: domainColor(feat.domain) || '#154273'
    }], YEARS, { unit: feat.unit });
    currentCharts.push(chart);
  });

  if (miniGrid.children.length > 0) {
    contentEl.appendChild(miniGrid);
  } else {
    contentEl.innerHTML = '<div style="padding: 40px; text-align: center; color: var(--text-muted);">Geen gegevens beschikbaar voor dit domein in de live CBS feed.</div>';
  }
}

export function renderTrends(container) {
  const domainNames = Object.keys(DOMAINS);
  
  let html = `
    <div class="page-header" style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <h1 class="page-title">Trendanalyse</h1>
        <p class="page-subtitle">Interactieve tijdreeksen voor Capelle aan den IJssel op basis van CBS Open Data — selecteer een beleidsdomein om indicatoren te verkennen.</p>
      </div>
      ${pdfExportButton('trends')}
    </div>

    <div class="filter-tabs" id="domain-tabs">
      ${domainNames.map((d, i) => `
        <button class="filter-tab ${i === 0 ? 'active' : ''}" data-domain="${d}">
          ${domainIcon(d)} ${d}
        </button>
      `).join('')}
    </div>

    <div class="charts-content"></div>
  `;

  container.innerHTML = html;

  // Render initial domain
  renderDomainCharts(container, domainNames[0], DOMAINS[domainNames[0]]);

  // Tab switching
  container.querySelectorAll('.filter-tab').forEach(tab => {
    tab.addEventListener('click', () => {
      container.querySelectorAll('.filter-tab').forEach(t => t.classList.remove('active'));
      tab.classList.add('active');
      const domain = tab.dataset.domain;
      renderDomainCharts(container, domain, DOMAINS[domain]);
    });
  });
}
