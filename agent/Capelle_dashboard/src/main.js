import './styles/index.css';
import { fetchCapelleData } from './data/features.js';
import { fetchWijkData } from './data/wijken.js';
import { renderOverview } from './pages/overview.js';
import { renderTrends } from './pages/trends.js';
import { renderPolicy } from './pages/policy.js';
import { renderInsights } from './pages/insights.js';
import { renderNeighbourhood } from './pages/neighbourhood.js';
import { renderActions } from './pages/actions.js';
import { renderPriority } from './pages/priority.js';
import { renderIntegrity } from './pages/integrity.js';
import { renderBenchmark } from './pages/benchmark.js';

const pages = {
  overview: { render: renderOverview, loaded: false },
  trends: { render: renderTrends, loaded: false },
  policy: { render: renderPolicy, loaded: false },
  insights: { render: renderInsights, loaded: false },
  neighbourhood: { render: renderNeighbourhood, loaded: false },
  actions: { render: renderActions, loaded: false },
  priority: { render: renderPriority, loaded: false },
  benchmark: { render: renderBenchmark, loaded: false },
  integrity: { render: renderIntegrity, loaded: false },
};

let currentPage = 'overview';

function navigateTo(page) {
  if (!pages[page]) return;

  // Update nav
  document.querySelectorAll('.nav-link').forEach(link => {
    link.classList.toggle('active', link.dataset.page === page);
  });

  // Update pages
  document.querySelectorAll('.page').forEach(p => {
    p.classList.remove('active');
  });

  const pageEl = document.getElementById(`page-${page}`);
  pageEl.classList.add('active');

  // Render if not loaded
  if (!pages[page].loaded) {
    pages[page].render(pageEl);
    pages[page].loaded = true;
  }

  currentPage = page;

  // Close mobile menu
  const navMenu = document.getElementById('nav-menu');
  if(navMenu) navMenu.classList.remove('open');

  // Scroll to top
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

async function init() {
  try {
    // Fetch CBS municipal data and wijk data in parallel
    await Promise.all([
      fetchCapelleData(),
      fetchWijkData(),
    ]);
  } catch (err) {
    console.error("Failed to fetch initial CBS data", err);
  }

  // Render default page
  navigateTo('overview');

  // Navigation handlers
  document.querySelectorAll('.nav-link').forEach(link => {
    link.addEventListener('click', (e) => {
      e.preventDefault();
      navigateTo(link.dataset.page);
    });
  });

  // Mobile menu toggle
  const menuToggle = document.getElementById('menu-toggle');
  const navMenu = document.getElementById('nav-menu');

  if(menuToggle && navMenu) {
    menuToggle.addEventListener('click', () => {
      navMenu.classList.toggle('open');
    });
  }

  // Handle hash navigation
  const hash = window.location.hash.replace('#', '');
  if (hash && pages[hash]) {
    navigateTo(hash);
  }

  window.addEventListener('hashchange', () => {
    const h = window.location.hash.replace('#', '');
    if (h && pages[h]) navigateTo(h);
  });

  // Global PDF export handler
  document.addEventListener('click', async (e) => {
    if (e.target.matches('.export-pdf-btn')) {
      const { exportPageToPDF } = await import('./utils/pdf-export.js');
      const pageId = e.target.dataset.page;
      const titles = {
        overview: 'Overzicht — Compass municipal dashboard',
        trends: 'Trends Verkenner',
        policy: 'Beleidsdata & Vector Zoeken',
        insights: 'Beleidsinzichten',
        neighbourhood: 'Wijkverkenner',
        actions: 'Actiematrix',
        priority: 'Prioriteiten',
        benchmark: 'Vergelijking Peer Gemeenten',
        integrity: 'Data Integriteit',
      };
      await exportPageToPDF(pageId, titles[pageId] || pageId);
    }
  });

  console.log('Compass municipal dashboard loaded successfully');
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
