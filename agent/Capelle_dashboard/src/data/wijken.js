// Wijk-level data from CBS Open Data API (Kerncijfers wijken en buurten - 84583NED)
// Provides neighbourhood-level indicators for Capelle aan den IJssel

export let WIJK_DATA = {};
export let WIJK_YEARS = [];
export let isWijkDataLoaded = false;

// Capelle aan den IJssel wijk codes and names
export const WIJK_CODES = {
  'WK050200': 'Capelle-West',
  'WK050201': 'Middelwatering',
  'WK050202': 'Oostgaarde',
  'WK050203': 'Schollevaar',
  'WK050204': 'Schenkel',
  'WK050205': '\'s-Gravenland',
  'WK050206': 'Fascinatio',
  'WK050207': 'Rivium',
};

// CBS column mapping for 84583NED
const WIJK_MAPPING = {
  'AantalInwoners_5': { id: 'population', label: 'Inwoners', unit: '', domain: 'Demografie' },
  'Mannen_6': { id: 'men', label: 'Mannen', unit: '', domain: 'Demografie' },
  'Vrouwen_7': { id: 'women', label: 'Vrouwen', unit: '', domain: 'Demografie' },
  'k_0Tot15Jaar_8': { id: 'youth_0_14', label: '0 tot 15 jaar', unit: '', domain: 'Demografie' },
  'k_15Tot25Jaar_9': { id: 'youth_15_24', label: '15 tot 25 jaar', unit: '', domain: 'Demografie' },
  'k_25Tot45Jaar_10': { id: 'adults_25_44', label: '25 tot 45 jaar', unit: '', domain: 'Demografie' },
  'k_45Tot65Jaar_11': { id: 'adults_45_64', label: '45 tot 65 jaar', unit: '', domain: 'Demografie' },
  'k_65JaarOfOuder_12': { id: 'seniors_65_plus', label: '65 jaar of ouder', unit: '', domain: 'Demografie' },
  'Huishoudens_28': { id: 'households', label: 'Huishoudens', unit: '', domain: 'Wonen' },
  'GemiddeldeHuishoudensgrootte_32': { id: 'household_size', label: 'Gem. huishoudensgrootte', unit: 'pers', domain: 'Wonen' },
  'Koopwoningen_40': { id: 'owner_occupied_pct', label: 'Koopwoningen', unit: '%', domain: 'Wonen' },
  'Huurwoningen_41': { id: 'rental_pct', label: 'Huurwoningen', unit: '%', domain: 'Wonen' },
  'GemiddeldeWOZWaardeVanWoningen_44': { id: 'avg_woz', label: 'Gem. WOZ-waarde', unit: 'k€', domain: 'Wonen' },
  'PersonenautoSTotaal_58': { id: 'cars', label: 'Personenauto\'s', unit: '', domain: 'Mobiliteit' },
  'AfstandTotHuisartsenpraktijk_109': { id: 'distance_gp', label: 'Afstand huisarts', unit: 'km', domain: 'Voorzieningen' },
  'AfstandTotGroteSupermarkt_110': { id: 'distance_supermarket', label: 'Afstand supermarkt', unit: 'km', domain: 'Voorzieningen' },
  'AfstandTotKinderdagverblijf_111': { id: 'distance_childcare', label: 'Afstand kinderopvang', unit: 'km', domain: 'Voorzieningen' },
  'AfstandTotSchool_112': { id: 'distance_school', label: 'Afstand basisschool', unit: 'km', domain: 'Voorzieningen' },
  'OmgevingsadressenDichtheid_105': { id: 'address_density', label: 'Adressendichtheid', unit: 'per km²', domain: 'Leefbaarheid' },
};

export const WIJK_DOMAINS = {
  'Demografie': ['population', 'men', 'women', 'youth_0_14', 'youth_15_24', 'adults_25_44', 'adults_45_64', 'seniors_65_plus'],
  'Wonen': ['households', 'household_size', 'owner_occupied_pct', 'rental_pct', 'avg_woz'],
  'Mobiliteit': ['cars'],
  'Voorzieningen': ['distance_gp', 'distance_supermarket', 'distance_childcare', 'distance_school'],
  'Leefbaarheid': ['address_density'],
};

export async function fetchWijkData() {
  try {
    const filter = Object.keys(WIJK_CODES).map(c => `startswith(WijkenEnBuurten,'${c}')`).join(' or ');
    const url = `https://opendata.cbs.nl/ODataApi/odata/84583NED/TypedDataSet?$format=json&$filter=(${filter})&$orderby=Perioden desc&$top=1000`;
    console.log('Fetching wijk data from CBS 84583NED...');

    const response = await fetch(url);
    if (!response.ok) throw new Error('Wijk data fetch failed');

    const data = await response.json();
    if (!data.value || data.value.length === 0) throw new Error('No wijk data returned');

    const yearsSet = new Set();

    // Parse data per wijk per year
    data.value.forEach(record => {
      const rawCode = record.WijkenEnBuurten ? record.WijkenEnBuurten.trim() : null;
      if (!rawCode || !WIJK_CODES[rawCode]) return;

      const yearStr = record.Perioden ? record.Perioden.substring(0, 4) : null;
      if (!yearStr) return;
      const year = parseInt(yearStr, 10);
      if (isNaN(year)) return;

      yearsSet.add(year);

      if (!WIJK_DATA[rawCode]) {
        WIJK_DATA[rawCode] = { name: WIJK_CODES[rawCode], years: {} };
      }

      if (!WIJK_DATA[rawCode].years[year]) {
        WIJK_DATA[rawCode].years[year] = {};
      }

      Object.keys(WIJK_MAPPING).forEach(cbsKey => {
        const meta = WIJK_MAPPING[cbsKey];
        const val = record[cbsKey];
        if (val !== undefined && val !== null) {
          WIJK_DATA[rawCode].years[year][meta.id] = val;
        }
      });
    });

    WIJK_YEARS = [...yearsSet].sort((a, b) => a - b);
    isWijkDataLoaded = Object.keys(WIJK_DATA).length > 0;

    console.log(`Wijk data loaded: ${Object.keys(WIJK_DATA).length} wijken, ${WIJK_YEARS.length} years`);
    return true;
  } catch (error) {
    console.error('Failed to load wijk data:', error);
    return false;
  }
}

// Get the latest value for a wijk and indicator
export function getWijkLatest(wijkCode, indicatorId) {
  const wijk = WIJK_DATA[wijkCode];
  if (!wijk) return null;
  for (let i = WIJK_YEARS.length - 1; i >= 0; i--) {
    const year = WIJK_YEARS[i];
    if (wijk.years[year] && wijk.years[year][indicatorId] !== undefined) {
      return { value: wijk.years[year][indicatorId], year };
    }
  }
  return null;
}

// Get municipal average across all wijken for an indicator
export function getWijkAverage(indicatorId, year) {
  const codes = Object.keys(WIJK_DATA);
  let sum = 0, count = 0;
  codes.forEach(code => {
    const wijk = WIJK_DATA[code];
    if (wijk.years[year] && wijk.years[year][indicatorId] !== undefined) {
      sum += wijk.years[year][indicatorId];
      count++;
    }
  });
  return count > 0 ? sum / count : null;
}

// Get all wijken comparison data for a given indicator
export function getWijkComparison(indicatorId) {
  const result = [];
  Object.entries(WIJK_DATA).forEach(([code, wijk]) => {
    const latest = getWijkLatest(code, indicatorId);
    if (latest) {
      result.push({ code, name: wijk.name, value: latest.value, year: latest.year });
    }
  });
  return result.sort((a, b) => b.value - a.value);
}

// Get indicator metadata
export function getWijkIndicatorMeta(indicatorId) {
  for (const meta of Object.values(WIJK_MAPPING)) {
    if (meta.id === indicatorId) return meta;
  }
  return null;
}

// Get all indicator IDs
export function getWijkIndicatorIds() {
  return Object.values(WIJK_MAPPING).map(m => m.id);
}
