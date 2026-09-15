// Chart.js helper utilities
import { Chart, registerables } from 'chart.js';
Chart.register(...registerables);

const CHART_COLORS = [
  '#154273', '#E17000', '#39870c', '#ca005d', '#007bc7',
  '#f9e11e', '#d52b1e', '#84cc16', '#6366f1', '#14b8a6',
];

export function createLineChart(canvas, datasets, years, options = {}) {
  const ctx = canvas.getContext('2d');
  
  // Destroy existing chart if any
  const existing = Chart.getChart(canvas);
  if (existing) existing.destroy();
  
  return new Chart(ctx, {
    type: 'line',
    data: {
      labels: years,
      datasets: datasets.map((ds, i) => ({
        label: ds.label,
        data: ds.data,
        borderColor: ds.color || CHART_COLORS[i % CHART_COLORS.length],
        backgroundColor: (ds.color || CHART_COLORS[i % CHART_COLORS.length]) + '20',
        borderWidth: 2.5,
        pointRadius: 4,
        pointHoverRadius: 7,
        pointBackgroundColor: ds.color || CHART_COLORS[i % CHART_COLORS.length],
        pointBorderColor: '#ffffff',
        pointBorderWidth: 2,
        tension: 0.3,
        fill: datasets.length === 1,
        spanGaps: true,
        ...ds.extra
      }))
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: {
        duration: 800,
        easing: 'easeOutQuart'
      },
      interaction: {
        mode: 'index',
        intersect: false
      },
      plugins: {
        legend: {
          display: datasets.length > 1,
          position: 'top',
          labels: {
            color: '#6b6b80',
            font: { family: "'Inter', sans-serif", size: 12 },
            usePointStyle: true,
            padding: 16
          }
        },
        tooltip: {
          backgroundColor: '#ffffff',
          titleColor: '#154273',
          bodyColor: '#333344',
          borderColor: '#d1d1dc',
          borderWidth: 1,
          padding: 12,
          titleFont: { family: "'Inter', sans-serif", weight: '700' },
          bodyFont: { family: "'Inter', sans-serif", weight: '500' },
          cornerRadius: 8,
          displayColors: true,
          callbacks: {
            label: function(context) {
              let val = context.parsed.y;
              if (val === null) return context.dataset.label + ': N/A';
              if (Math.abs(val) >= 1000000) val = (val/1000000).toFixed(1) + 'M';
              else if (Math.abs(val) >= 1000) val = val.toLocaleString('nl-NL');
              else if (val % 1 !== 0) val = val.toFixed(2);
              const unit = options.unit || '';
              return context.dataset.label + ': ' + val + (unit ? ' ' + unit : '');
            }
          }
        }
      },
      scales: {
        x: {
          grid: { color: '#e0e0e8', drawBorder: false },
          ticks: { color: '#6b6b80', font: { family: "'Inter', sans-serif", size: 12 } }
        },
        y: {
          grid: { color: '#e0e0e8', drawBorder: false },
          ticks: { 
            color: '#6b6b80', 
            font: { family: "'Inter', sans-serif", size: 12 },
            callback: function(value) {
              if (Math.abs(value) >= 1000000) return (value/1000000).toFixed(1) + 'M';
              if (Math.abs(value) >= 1000) return value.toLocaleString('nl-NL');
              return value;
            }
          }
        }
      },
      ...options.chartOptions
    }
  });
}

export { Chart, CHART_COLORS };
