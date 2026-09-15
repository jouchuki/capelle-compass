// Quality-of-Life Dimensions Model for Compass municipal dashboard
// Maps CBS indicators and policy domains to civic quality-of-life themes

import { FEATURES, getTrend, getLatest } from './features.js';

export const QOL_DIMENSIONS = [
  {
    id: 'veiligheid',
    label: 'Veilig & Leefbaar',
    icon: '🛡️',
    color: '#dc2626',
    description: 'Veilige en leefbare wijken met sociale cohesie',
    cbsFeatures: ['pop_density'],
    policyDomains: ['Veiligheid', 'Openbare Orde', 'Handhaving'],
  },
  {
    id: 'gezondheid',
    label: 'Gezond & Vitaal',
    icon: '❤️',
    color: '#e11d48',
    description: 'Gezonde en vitale inwoners met toegang tot zorg',
    cbsFeatures: ['births', 'birth_rate', 'deaths', 'death_rate'],
    policyDomains: ['Zorg & Jeugd', 'Gezondheid', 'Sport & Recreatie', 'Welzijn'],
  },
  {
    id: 'wonen',
    label: 'Wonen & Bereikbaarheid',
    icon: '🏠',
    color: '#E17000',
    description: 'Passende woningen en goede bereikbaarheid',
    cbsFeatures: ['households', 'single_households', 'family_households', 'household_size', 'housing_stock', 'avg_woz'],
    policyDomains: ['Wonen', 'Wonen & Milieu', 'Ruimtelijke Ordening'],
  },
  {
    id: 'mobiliteit',
    label: 'Mobiliteit & Openbare Ruimte',
    icon: '🚗',
    color: '#ca005d',
    description: 'Goede mobiliteit en kwalitatieve openbare ruimte',
    cbsFeatures: ['cars', 'cars_per_1000', 'motorcycles'],
    policyDomains: ['Mobiliteit', 'Verkeer', 'Infrastructuur'],
  },
  {
    id: 'groen',
    label: 'Groen & Klimaat',
    icon: '🌿',
    color: '#16a34a',
    description: 'Groene en klimaatbestendige leefomgeving',
    cbsFeatures: ['pop_density'],
    policyDomains: ['Milieu', 'Duurzaamheid', 'Klimaat'],
  },
  {
    id: 'participatie',
    label: 'Meedoen & Erbij horen',
    icon: '🤝',
    color: '#6366f1',
    description: 'Participatie, sociale inclusie en gemeenschapszin',
    cbsFeatures: ['population', 'men', 'women', 'youth_0_4', 'youth_5_9', 'youth_10_14', 'youth_15_24', 'seniors_65_80', 'seniors_80_plus'],
    policyDomains: ['Sociaal', 'Participatie', 'Cultuur', 'Onderwijs', 'Bestuur & Organisatie'],
  },
  {
    id: 'werk',
    label: 'Werk & Bestaanszekerheid',
    icon: '💼',
    color: '#39870c',
    description: 'Werkgelegenheid en financiële zekerheid voor iedereen',
    cbsFeatures: ['avg_income', 'businesses', 'benefits', 'unemployment'],
    policyDomains: ['Economie', 'Economie & Werk', 'Inkomen & Economie', 'Financiën', 'Belastingen'],
  },
];

/**
 * Get a summary of each QoL dimension based on live CBS data.
 * Returns an array of objects: { dimension, indicatorCount, avgTrend, trendDirection, latestIndicators }
 */
export function getQolSummary() {
  return QOL_DIMENSIONS.map(dim => {
    const indicators = [];
    let trendSum = 0;
    let trendCount = 0;

    dim.cbsFeatures.forEach(key => {
      const feat = FEATURES[key];
      if (!feat) return;
      const latest = getLatest(key);
      const trend = getTrend(key);
      if (latest) {
        indicators.push({ key, label: feat.label, value: latest.value, year: latest.year, trend });
        if (trend !== null) { trendSum += trend; trendCount++; }
      }
    });

    const avgTrend = trendCount > 0 ? trendSum / trendCount : null;
    let trendDirection = 'stabiel';
    if (avgTrend !== null) {
      if (avgTrend > 3) trendDirection = 'stijgend';
      else if (avgTrend < -3) trendDirection = 'dalend';
    }

    return {
      dimension: dim,
      indicatorCount: indicators.length,
      avgTrend,
      trendDirection,
      latestIndicators: indicators,
    };
  });
}

/**
 * Map a policy's domain and QoL dimensions to the QOL_DIMENSIONS model.
 */
export function matchPolicyToQol(policy) {
  const matched = [];
  // If policy has V2 quality_of_life_dimensions, use those
  if (policy.quality_of_life_dimensions && policy.quality_of_life_dimensions.length > 0) {
    policy.quality_of_life_dimensions.forEach(qolId => {
      const dim = QOL_DIMENSIONS.find(d => d.id === qolId || d.label.toLowerCase().includes(qolId.toLowerCase()));
      if (dim) matched.push(dim);
    });
  }
  // Also check policy domain
  if (policy.domain) {
    QOL_DIMENSIONS.forEach(dim => {
      if (dim.policyDomains.some(pd => pd.toLowerCase() === policy.domain.toLowerCase())) {
        if (!matched.find(m => m.id === dim.id)) {
          matched.push(dim);
        }
      }
    });
  }
  return matched.length > 0 ? matched : [QOL_DIMENSIONS[5]]; // default to participatie
}
