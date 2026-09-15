// Peer municipality benchmarking data from CBS Open Data API (70072ned)
// Compares Capelle aan den IJssel with similar municipalities

export let PEER_DATA = {};
export let PEER_YEARS = [];
export let isPeerDataLoaded = false;

// Peer municipalities selected for similarity to Capelle:
// - Dense suburban, near Rotterdam, comparable population/income/housing profile
export const PEER_MUNICIPALITIES = [
  { code: 'GM0502', name: 'Capelle aan den IJssel', isSelf: true },
  { code: 'GM0597', name: 'Ridderkerk' },
  { code: 'GM0542', name: 'Krimpen aan den IJssel' },
  { code: 'GM1930', name: 'Nissewaard' },
  { code: 'GM1621', name: 'Lansingerland' },
  { code: 'GM0588', name: 'Barendrecht' },
];

export const PEER_COLORS = {
  'GM0502': '#154273',  // Capelle - dark blue (primary)
  'GM0597': '#E17000',  // Ridderkerk - orange
  'GM0542': '#39870c',  // Krimpen - green
  'GM1930': '#ca005d',  // Nissewaard - pink
  'GM1621': '#6366f1',  // Lansingerland - purple
  'GM0588': '#0891b2',  // Barendrecht - cyan
};

// Same indicators as features.js uses from 70072ned
const PEER_MAPPING = {
  'TotaleBevolking_1': { id: 'population', label: 'Totale Bevolking', unit: '' },
  'Mannen_2': { id: 'men', label: 'Mannen', unit: '' },
  'Vrouwen_3': { id: 'women', label: 'Vrouwen', unit: '' },
  'JongerDan5Jaar_4': { id: 'youth_0_4', label: 'Jonger dan 5 jaar', unit: '' },
  'k_65Tot80Jaar_11': { id: 'seniors_65_80', label: '65 tot 80 jaar', unit: '' },
  'k_80JaarOfOuder_12': { id: 'seniors_80_plus', label: '80 jaar of ouder', unit: '' },
  'TotaalParticuliereHuishoudens_82': { id: 'households', label: 'Particuliere Huishoudens', unit: '' },
  'GemiddeldeHuishoudensgrootte_89': { id: 'household_size', label: 'Gem. Huishoudensgrootte', unit: 'pers' },
  'VoorraadOp1Januari_90': { id: 'housing_stock', label: 'Woningvoorraad', unit: '' },
  'GemiddeldeWOZWaardeVanWoningen_98': { id: 'avg_woz', label: 'Gem. WOZ-waarde', unit: 'k€' },
  'BedrijfsvestigingenTotaal_168': { id: 'businesses', label: 'Bedrijfsvestigingen', unit: '' },
  'UitkeringsontvangersTotaal_156': { id: 'benefits', label: 'Uitkeringsontvangers', unit: '' },
  'PersonenautoSRelatief_202': { id: 'cars_per_1000', label: 'Auto\'s per 1000 inw.', unit: '' },
};

// Indicators grouped by theme for the benchmark view
export const BENCHMARK_THEMES = {
  'Demografie': ['population', 'youth_0_4', 'seniors_65_80', 'seniors_80_plus'],
  'Wonen': ['households', 'household_size', 'housing_stock', 'avg_woz'],
  'Economie & Werk': ['businesses', 'benefits'],
  'Mobiliteit': ['cars_per_1000'],
};

export async function fetchPeerData() {
  if (isPeerDataLoaded) return true;

  try {
    const filter = PEER_MUNICIPALITIES.map(p => `startswith(RegioS,'${p.code}')`).join(' or ');
    const currentYear = new Date().getFullYear();
    const startYear = currentYear - 10;

    const url = `https://opendata.cbs.nl/ODataApi/odata/70072ned/TypedDataSet?$format=json&$filter=(${filter})&$orderby=Perioden asc`;
    console.log('Fetching peer municipality data from CBS...');

    const response = await fetch(url);
    if (!response.ok) throw new Error('Peer data fetch failed');

    const data = await response.json();
    if (!data.value || data.value.length === 0) throw new Error('No peer data returned');

    const yearsSet = new Set();

    // Initialize PEER_DATA structure
    PEER_MUNICIPALITIES.forEach(p => {
      PEER_DATA[p.code] = { name: p.name, isSelf: p.isSelf || false, indicators: {} };
      Object.values(PEER_MAPPING).forEach(m => {
        PEER_DATA[p.code].indicators[m.id] = { label: m.label, unit: m.unit, values: [], years: [] };
      });
    });

    // Process records
    data.value.forEach(record => {
      const regioStr = record.RegioS ? record.RegioS.trim() : '';
      const municipality = PEER_MUNICIPALITIES.find(p => regioStr.startsWith(p.code));
      if (!municipality) return;

      const yearStr = record.Perioden ? record.Perioden.substring(0, 4) : null;
      if (!yearStr) return;
      const year = parseInt(yearStr, 10);
      if (isNaN(year) || year < startYear) return;

      yearsSet.add(year);

      Object.keys(PEER_MAPPING).forEach(cbsKey => {
        const meta = PEER_MAPPING[cbsKey];
        const val = record[cbsKey];
        PEER_DATA[municipality.code].indicators[meta.id].values.push(val !== undefined ? val : null);
        PEER_DATA[municipality.code].indicators[meta.id].years.push(year);
      });
    });

    PEER_YEARS = [...yearsSet].sort((a, b) => a - b);
    isPeerDataLoaded = true;

    console.log(`Peer data loaded: ${Object.keys(PEER_DATA).length} municipalities, ${PEER_YEARS.length} years`);
    return true;
  } catch (error) {
    console.error('Failed to load peer data:', error);
    return false;
  }
}

// Get latest value for a municipality and indicator
export function getPeerLatest(municipalityCode, indicatorId) {
  const muni = PEER_DATA[municipalityCode];
  if (!muni || !muni.indicators[indicatorId]) return null;
  const ind = muni.indicators[indicatorId];
  for (let i = ind.values.length - 1; i >= 0; i--) {
    if (ind.values[i] !== null) return { value: ind.values[i], year: ind.years[i] };
  }
  return null;
}

// Get ranking of all peers for a given indicator (latest values)
export function getPeerRanking(indicatorId, higherIsBetter = true) {
  const ranking = [];
  PEER_MUNICIPALITIES.forEach(p => {
    const latest = getPeerLatest(p.code, indicatorId);
    if (latest) {
      ranking.push({
        code: p.code,
        name: p.name,
        isSelf: p.isSelf || false,
        value: latest.value,
        year: latest.year,
      });
    }
  });

  ranking.sort((a, b) => higherIsBetter ? b.value - a.value : a.value - b.value);

  // Add rank
  ranking.forEach((item, i) => { item.rank = i + 1; });
  return ranking;
}

// Get time series for all peers for a given indicator
export function getPeerTimeSeries(indicatorId) {
  const series = [];
  PEER_MUNICIPALITIES.forEach(p => {
    const muni = PEER_DATA[p.code];
    if (!muni || !muni.indicators[indicatorId]) return;
    const ind = muni.indicators[indicatorId];
    series.push({
      code: p.code,
      name: p.name,
      isSelf: p.isSelf || false,
      color: PEER_COLORS[p.code] || '#6b7280',
      data: ind.values,
      years: ind.years,
    });
  });
  return series;
}

// Get indicator metadata
export function getPeerIndicatorMeta(indicatorId) {
  for (const meta of Object.values(PEER_MAPPING)) {
    if (meta.id === indicatorId) return meta;
  }
  return null;
}
