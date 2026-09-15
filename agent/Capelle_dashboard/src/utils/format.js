// Number and display formatting utilities for Compass municipal dashboard
export function formatValue(value, format, unit) {
  if (value === null || value === undefined) return '—';
  
  let formatted;
  switch (format) {
    case 'number':
      formatted = value >= 1000000 
        ? (value / 1000000).toFixed(1) + 'M'
        : value >= 1000 
          ? value.toLocaleString('nl-NL') 
          : String(value);
      break;
    case 'decimal1':
      formatted = value.toFixed(1);
      break;
    case 'decimal2':
      formatted = value.toFixed(2);
      break;
    case 'percent':
      formatted = value.toFixed(1) + '%';
      break;
    default:
      formatted = String(value);
  }
  
  if (unit && unit !== '%' && unit !== '‰') {
    return formatted + ' ' + unit;
  }
  if (unit === '%' || unit === '‰') {
    return formatted + unit;
  }
  return formatted;
}

export function trendArrow(pct) {
  if (pct === null || pct === undefined) return { arrow: '—', cls: 'neutral' };
  const abs = Math.abs(pct);
  if (abs < 1.5) return { arrow: '→', cls: 'neutral', text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` };
  if (abs < 5) return { arrow: pct > 0 ? '↗' : '↘', cls: pct > 0 ? 'up-mild' : 'down-mild', text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` };
  if (abs < 15) return { arrow: pct > 0 ? '↗' : '↘', cls: pct > 0 ? 'up' : 'down', text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` };
  return { arrow: pct > 0 ? '▲' : '▼', cls: pct > 0 ? 'up-strong' : 'down-strong', text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%` };
}

// --- Policy domain icons & colors ---
export function domainIcon(domain) {
  const icons = {
    'Demografie': '👥',
    'Bevolkingsdynamiek': '📈',
    'Wonen & Huishoudens': '🏠',
    'Inkomen & Economie': '💰',
    'Economie & Werk': '💼',
    'Mobiliteit': '🚗',
    'Voorzieningen': '🏥',
    'Leefbaarheid': '🌳',
    'Wonen': '🏠',
    'Wonen & Milieu': '🏠',
    'Sociaal': '🤝',
    'Veiligheid': '🛡️',
    'Economie': '💼',
    'Zorg & Jeugd': '❤️',
    'Sport & Recreatie': '⚽',
    'Onderwijs': '📚',
    'Cultuur': '🎭',
    'Milieu': '🌿',
    'Duurzaamheid': '♻️',
    'Bestuur & Organisatie': '🏛️',
    'Financiën': '💰',
    'Openbare Orde': '🚨',
  };
  return icons[domain] || '📊';
}

export function domainColor(domain) {
  const colors = {
    'Demografie': '#154273',
    'Bevolkingsdynamiek': '#0369a1',
    'Wonen & Huishoudens': '#E17000',
    'Inkomen & Economie': '#b45309',
    'Economie & Werk': '#39870c',
    'Mobiliteit': '#ca005d',
    'Voorzieningen': '#0891b2',
    'Leefbaarheid': '#16a34a',
    'Wonen': '#E17000',
    'Wonen & Milieu': '#E17000',
    'Sociaal': '#6366f1',
    'Veiligheid': '#dc2626',
    'Economie': '#39870c',
    'Zorg & Jeugd': '#e11d48',
    'Sport & Recreatie': '#0891b2',
    'Onderwijs': '#7c3aed',
    'Cultuur': '#a855f7',
    'Milieu': '#16a34a',
    'Duurzaamheid': '#059669',
    'Bestuur & Organisatie': '#475569',
    'Financiën': '#b45309',
    'Openbare Orde': '#be123c',
  };
  return colors[domain] || '#6b7280';
}

// --- QoL dimension icons & colors ---
export function qolIcon(dimensionId) {
  const icons = {
    'veiligheid': '🛡️',
    'gezondheid': '❤️',
    'wonen': '🏠',
    'mobiliteit': '🚗',
    'groen': '🌿',
    'participatie': '🤝',
    'werk': '💼',
  };
  return icons[dimensionId] || '📊';
}

export function qolColor(dimensionId) {
  const colors = {
    'veiligheid': '#dc2626',
    'gezondheid': '#e11d48',
    'wonen': '#E17000',
    'mobiliteit': '#ca005d',
    'groen': '#16a34a',
    'participatie': '#6366f1',
    'werk': '#39870c',
  };
  return colors[dimensionId] || '#6b7280';
}

// --- Status & priority badges ---
export function statusBadge(status) {
  if (!status) return '';
  const map = {
    'actief': { bg: '#dcfce7', color: '#166534', label: 'Actief' },
    'in uitvoering': { bg: '#dbeafe', color: '#1e40af', label: 'In uitvoering' },
    'concept': { bg: '#fef3c7', color: '#92400e', label: 'Concept' },
    'verlopen': { bg: '#fee2e2', color: '#991b1b', label: 'Verlopen' },
    'ingetrokken': { bg: '#f3f4f6', color: '#6b7280', label: 'Ingetrokken' },
  };
  const s = map[status.toLowerCase()] || { bg: '#f3f4f6', color: '#6b7280', label: status };
  return `<span class="status-badge" style="background:${s.bg};color:${s.color}">${s.label}</span>`;
}

export function priorityBadge(score) {
  if (score === null || score === undefined) return '';
  const pct = Math.round(score * 100);
  let bg, color, label;
  if (pct >= 80) { bg = '#fef2f2'; color = '#991b1b'; label = 'Hoog'; }
  else if (pct >= 60) { bg = '#fef3c7'; color = '#92400e'; label = 'Middel'; }
  else { bg = '#f0fdf4'; color = '#166534'; label = 'Laag'; }
  return `<span class="priority-badge" style="background:${bg};color:${color}">${label} (${pct}%)</span>`;
}

export function confidenceBadge(score) {
  if (score === null || score === undefined) return '';
  const pct = Math.round(score * 100);
  let label;
  if (pct >= 75) label = 'Hoog';
  else if (pct >= 50) label = 'Redelijk';
  else label = 'Laag';
  return `<span class="confidence-badge">${label} (${pct}%)</span>`;
}
