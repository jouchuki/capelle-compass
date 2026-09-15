// Composite Scores — Computed quality-of-life metrics for decision support
// Derives actionable indices from CBS data + policy data

import { FEATURES, getTrend, getLatest, YEARS } from './features.js';
import { QOL_DIMENSIONS, getQolSummary, matchPolicyToQol } from './qol.js';

/**
 * Normalize a value to 0-100 scale using min-max normalization
 * If invert=true, higher raw values = lower score (e.g. unemployment)
 */
function normalize(value, min, max, invert = false) {
  if (value === null || value === undefined || min === max) return null;
  const clamped = Math.max(min, Math.min(max, value));
  const norm = ((clamped - min) / (max - min)) * 100;
  return invert ? 100 - norm : norm;
}

/**
 * Get historical range (min, max) for a feature across all years
 */
function getRange(featureKey) {
  if (!FEATURES[featureKey]) return { min: 0, max: 100 };
  const vals = FEATURES[featureKey].values.filter(v => v !== null);
  if (vals.length === 0) return { min: 0, max: 100 };
  return { min: Math.min(...vals), max: Math.max(...vals) };
}

/**
 * Compute Liveability Index per QoL dimension and overall
 * Returns: { overall: number, dimensions: [{ id, label, icon, color, score, indicators }] }
 */
export function computeLiveabilityIndex() {
  const weights = {
    'veiligheid': 0.18,
    'gezondheid': 0.15,
    'wonen': 0.15,
    'mobiliteit': 0.10,
    'groen': 0.10,
    'participatie': 0.15,
    'werk': 0.17,
  };

  // Indicators where higher = worse
  const invertedIndicators = new Set(['benefits', 'unemployment', 'seniors_80_plus']);

  const dimensions = QOL_DIMENSIONS.map(dim => {
    const scores = [];

    dim.cbsFeatures.forEach(key => {
      const latest = getLatest(key);
      if (!latest) return;
      const range = getRange(key);
      const invert = invertedIndicators.has(key);
      const score = normalize(latest.value, range.min, range.max, invert);
      if (score !== null) {
        scores.push({ key, label: FEATURES[key]?.label || key, score, value: latest.value });
      }
    });

    const avgScore = scores.length > 0
      ? scores.reduce((sum, s) => sum + s.score, 0) / scores.length
      : null;

    return {
      id: dim.id,
      label: dim.label,
      icon: dim.icon,
      color: dim.color,
      score: avgScore !== null ? Math.round(avgScore) : null,
      indicators: scores,
      weight: weights[dim.id] || 0.1,
    };
  });

  // Weighted overall score
  let weightedSum = 0, totalWeight = 0;
  dimensions.forEach(d => {
    if (d.score !== null) {
      weightedSum += d.score * d.weight;
      totalWeight += d.weight;
    }
  });
  const overall = totalWeight > 0 ? Math.round(weightedSum / totalWeight) : null;

  return { overall, dimensions };
}

/**
 * Compute Policy Coverage Ratio
 * For each QoL dimension: what % of CBS indicators have a matching policy?
 * Returns: [{ dimension, totalIndicators, coveredIndicators, ratio, gaps }]
 */
export function computePolicyCoverage(policies) {
  return QOL_DIMENSIONS.map(dim => {
    const totalIndicators = dim.cbsFeatures.length;

    // Count policies linked to this dimension
    const linkedPolicies = policies.filter(p => {
      const matched = matchPolicyToQol(p);
      return matched.some(m => m.id === dim.id);
    });

    const hasPolicies = linkedPolicies.length > 0;
    const ratio = totalIndicators > 0 && hasPolicies
      ? Math.min(100, Math.round((linkedPolicies.length / Math.max(totalIndicators, 1)) * 100))
      : 0;

    // Identify which indicators are worsening but have no policy
    const gaps = [];
    dim.cbsFeatures.forEach(key => {
      const trend = getTrend(key);
      const isNegativeMetric = ['benefits', 'unemployment'].includes(key);
      const isWorsening = isNegativeMetric ? (trend !== null && trend > 3) : (trend !== null && trend < -5);
      if (isWorsening && !hasPolicies) {
        gaps.push({ key, label: FEATURES[key]?.label || key, trend });
      }
    });

    return {
      dimension: dim,
      totalIndicators,
      linkedPolicies: linkedPolicies.length,
      ratio,
      gaps,
      status: ratio >= 70 ? 'goed' : ratio >= 40 ? 'matig' : 'onvoldoende',
    };
  });
}

/**
 * Compute Trend Velocity — rate of change and acceleration
 * Returns indicators sorted by urgency with velocity metrics
 */
export function computeTrendVelocity() {
  const results = [];

  Object.keys(FEATURES).forEach(key => {
    const feat = FEATURES[key];
    if (!feat) return;
    const vals = feat.values.filter(v => v !== null);
    if (vals.length < 3) return;

    const latest = vals[vals.length - 1];
    const prev = vals[vals.length - 2];
    const oldest = vals[0];

    // Overall trend (full period)
    const fullTrend = oldest !== 0 ? ((latest - oldest) / Math.abs(oldest)) * 100 : null;

    // Recent trend (last 2 points)
    const recentTrend = prev !== 0 ? ((latest - prev) / Math.abs(prev)) * 100 : null;

    // 3-year trend
    const threeYearsAgo = vals.length >= 4 ? vals[vals.length - 4] : vals[0];
    const threeYearTrend = threeYearsAgo !== 0 ? ((latest - threeYearsAgo) / Math.abs(threeYearsAgo)) * 100 : null;

    // 5-year trend
    const fiveAgo = vals.length >= 6 ? vals[vals.length - 6] : vals[0];
    const fiveYearTrend = fiveAgo !== 0 ? ((latest - fiveAgo) / Math.abs(fiveAgo)) * 100 : null;

    // Acceleration: is the recent trend faster than the long-term trend?
    let acceleration = null;
    if (recentTrend !== null && fullTrend !== null) {
      acceleration = recentTrend - (fullTrend / Math.max(vals.length - 1, 1));
    }

    // Negative metrics where "up" is bad
    const isNegativeMetric = ['benefits', 'unemployment'].includes(key);

    // Urgency score: combines magnitude and acceleration
    let urgency = 0;
    if (fiveYearTrend !== null) {
      urgency = Math.abs(fiveYearTrend);
      if (acceleration !== null && Math.abs(acceleration) > 2) {
        urgency += Math.abs(acceleration) * 0.5;
      }
      // Boost urgency for negative indicators going up
      if (isNegativeMetric && fiveYearTrend > 0) urgency *= 1.5;
    }

    results.push({
      key,
      label: feat.label,
      domain: feat.domain,
      latestValue: latest,
      latestYear: YEARS[YEARS.length - 1],
      fullTrend,
      threeYearTrend,
      fiveYearTrend,
      recentTrend,
      acceleration,
      urgency,
      isNegativeMetric,
      direction: fiveYearTrend > 0 ? 'stijgend' : fiveYearTrend < 0 ? 'dalend' : 'stabiel',
      isAccelerating: acceleration !== null && Math.abs(acceleration) > 2,
    });
  });

  return results.sort((a, b) => b.urgency - a.urgency);
}

/**
 * Compute Early Warning indicators — those deteriorating fastest
 * Returns top N indicators with warning level
 */
export function computeEarlyWarnings(topN = 5) {
  const velocity = computeTrendVelocity();

  return velocity
    .filter(v => {
      // Only flag indicators where the trend is moving in a bad direction
      if (v.isNegativeMetric) return v.fiveYearTrend !== null && v.fiveYearTrend > 2;
      return v.fiveYearTrend !== null && Math.abs(v.fiveYearTrend) > 5;
    })
    .slice(0, topN)
    .map(v => ({
      ...v,
      warningLevel: v.urgency > 20 ? 'hoog' : v.urgency > 10 ? 'middel' : 'laag',
      warningColor: v.urgency > 20 ? '#dc2626' : v.urgency > 10 ? '#E17000' : '#d97706',
    }));
}

/**
 * Compute overall dashboard summary metrics
 */
export function computeDashboardSummary(policies) {
  const liveability = computeLiveabilityIndex();
  const coverage = computePolicyCoverage(policies);
  const warnings = computeEarlyWarnings();
  const velocity = computeTrendVelocity();

  // Count improving vs declining indicators
  const improving = velocity.filter(v => {
    if (v.isNegativeMetric) return v.fiveYearTrend !== null && v.fiveYearTrend < -2;
    return v.fiveYearTrend !== null && v.fiveYearTrend > 2;
  }).length;

  const declining = velocity.filter(v => {
    if (v.isNegativeMetric) return v.fiveYearTrend !== null && v.fiveYearTrend > 2;
    return v.fiveYearTrend !== null && v.fiveYearTrend < -2;
  }).length;

  const avgCoverage = coverage.length > 0
    ? Math.round(coverage.reduce((s, c) => s + c.ratio, 0) / coverage.length)
    : 0;

  return {
    liveabilityScore: liveability.overall,
    liveabilityDimensions: liveability.dimensions,
    policyCoverage: avgCoverage,
    coverageDetails: coverage,
    earlyWarnings: warnings,
    trendSummary: {
      total: velocity.length,
      improving,
      declining,
      stable: velocity.length - improving - declining,
    },
  };
}
