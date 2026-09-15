// Live data from CBS Open Data API (Regionale Kerncijfers - 70072ned) for Capelle aan den IJssel (GM0502)

export let YEARS = [];
export let FEATURES = {};
export let isDataLoaded = false;

// Define the metrics we want to extract from the CBS API
const API_MAPPING = {
  // Demographics
  'TotaleBevolking_1': { id: 'population', label: 'Totale Bevolking', unit: '', domain: 'Demografie' },
  'Mannen_2': { id: 'men', label: 'Mannen', unit: '', domain: 'Demografie' },
  'Vrouwen_3': { id: 'women', label: 'Vrouwen', unit: '', domain: 'Demografie' },
  'JongerDan5Jaar_4': { id: 'youth_0_4', label: 'Jonger dan 5 jaar', unit: '', domain: 'Demografie' },
  'k_5Tot10Jaar_5': { id: 'youth_5_9', label: '5 tot 10 jaar', unit: '', domain: 'Demografie' },
  'k_10Tot15Jaar_6': { id: 'youth_10_14', label: '10 tot 15 jaar', unit: '', domain: 'Demografie' },
  'k_15Tot25Jaar_7': { id: 'youth_15_24', label: '15 tot 25 jaar', unit: '', domain: 'Demografie' },
  'k_25Tot45Jaar_8': { id: 'adults_25_44', label: '25 tot 45 jaar', unit: '', domain: 'Demografie' },
  'k_45Tot65Jaar_9': { id: 'adults_45_64', label: '45 tot 65 jaar', unit: '', domain: 'Demografie' },
  'k_65Tot80Jaar_11': { id: 'seniors_65_80', label: '65 tot 80 jaar', unit: '', domain: 'Demografie' },
  'k_80JaarOfOuder_12': { id: 'seniors_80_plus', label: '80 jaar of ouder', unit: '', domain: 'Demografie' },

  // Population dynamics
  'GeboorteTotaal_34': { id: 'births', label: 'Geboorten', unit: '', domain: 'Bevolkingsdynamiek' },
  'GeboorteRelatief_36': { id: 'birth_rate', label: 'Geboortecijfer', unit: '‰', domain: 'Bevolkingsdynamiek' },
  'SterfteTotaal_37': { id: 'deaths', label: 'Sterfgevallen', unit: '', domain: 'Bevolkingsdynamiek' },
  'SterfteRelatief_38': { id: 'death_rate', label: 'Sterftecijfer', unit: '‰', domain: 'Bevolkingsdynamiek' },
  'BevolkingsdichtheidInwonersPerKm2_33': { id: 'pop_density', label: 'Bevolkingsdichtheid', unit: 'per km²', domain: 'Bevolkingsdynamiek' },

  // Housing
  'TotaalParticuliereHuishoudens_82': { id: 'households', label: 'Particuliere Huishoudens', unit: '', domain: 'Wonen & Huishoudens' },
  'Eenpersoonshuishoudens_83': { id: 'single_households', label: 'Eenpersoonshuishoudens', unit: '', domain: 'Wonen & Huishoudens' },
  'HuishoudensMet_kinderen_84': { id: 'family_households', label: 'Huishoudens met kinderen', unit: '', domain: 'Wonen & Huishoudens' },
  'GemiddeldeHuishoudensgrootte_89': { id: 'household_size', label: 'Gemiddelde Huishoudensgrootte', unit: 'pers', domain: 'Wonen & Huishoudens' },
  'VoorraadOp1Januari_90': { id: 'housing_stock', label: 'Woningvoorraad', unit: '', domain: 'Wonen & Huishoudens' },
  'GemiddeldeWOZWaardeVanWoningen_98': { id: 'avg_woz', label: 'Gemiddelde WOZ-waarde', unit: 'k€', domain: 'Wonen & Huishoudens' },

  // Income & Economy
  'GemiddeldInkomenPerInwoner_99': { id: 'avg_income', label: 'Gem. Inkomen per Inwoner', unit: 'k€', domain: 'Inkomen & Economie' },
  'BedrijfsvestigingenTotaal_168': { id: 'businesses', label: 'Bedrijfsvestigingen', unit: '', domain: 'Inkomen & Economie' },
  'UitkeringsontvangersTotaal_156': { id: 'benefits', label: 'Uitkeringsontvangers', unit: '', domain: 'Inkomen & Economie' },
  'Werkloosheid_159': { id: 'unemployment', label: 'Werklozen (UWV)', unit: '', domain: 'Inkomen & Economie' },

  // Mobility
  'PersonenautoS_201': { id: 'cars', label: 'Personenauto\'s', unit: '', domain: 'Mobiliteit' },
  'PersonenautoSRelatief_202': { id: 'cars_per_1000', label: 'Auto\'s per 1000 inw.', unit: '', domain: 'Mobiliteit' },
  'Motortweewielers_203': { id: 'motorcycles', label: 'Motortweewielers', unit: '', domain: 'Mobiliteit' },
};

export const DOMAINS = {
  'Demografie': ['population', 'men', 'women', 'youth_0_4', 'youth_5_9', 'youth_10_14', 'youth_15_24', 'adults_25_44', 'adults_45_64', 'seniors_65_80', 'seniors_80_plus'],
  'Bevolkingsdynamiek': ['births', 'birth_rate', 'deaths', 'death_rate', 'pop_density'],
  'Wonen & Huishoudens': ['households', 'single_households', 'family_households', 'household_size', 'housing_stock', 'avg_woz'],
  'Inkomen & Economie': ['avg_income', 'businesses', 'benefits', 'unemployment'],
  'Mobiliteit': ['cars', 'cars_per_1000', 'motorcycles']
};

// Initialize empty structure
export function resetFeatures() {
  YEARS.length = 0;
  FEATURES = {};
  Object.values(API_MAPPING).forEach(m => {
    FEATURES[m.id] = { label: m.label, unit: m.unit, domain: m.domain, values: [] };
  });
}

export async function fetchCapelleData() {
  resetFeatures();
  
  try {
    // Determine recent years logic
    const currentYear = new Date().getFullYear();
    const startYear = currentYear - 10;
    
    // Fetch directly from CBS OData API for GM0502 (Capelle aan den IJssel)
    const url = `https://opendata.cbs.nl/ODataApi/odata/70072ned/TypedDataSet?$format=json&$filter=startswith(RegioS,'GM0502')&$orderby=Perioden asc`;
    console.log('Fetching live Capelle data from:', url);
    
    const response = await fetch(url);
    if (!response.ok) throw new Error('Network response was not ok');
    
    const data = await response.json();
    if (!data.value || data.value.length === 0) throw new Error('No data returned from CBS');

    // Process all periods
    data.value.forEach(record => {
      // Perioden is in format "2021JJ00"
      const yearStr = record.Perioden.substring(0, 4);
      const yearInt = parseInt(yearStr, 10);
      
      if (yearInt >= startYear) {
        if (!YEARS.includes(yearInt)) YEARS.push(yearInt);
        
        Object.keys(API_MAPPING).forEach(cbsKey => {
          const uiKey = API_MAPPING[cbsKey].id;
          const val = record[cbsKey];
          FEATURES[uiKey].values.push(val !== undefined ? val : null);
        });
      }
    });

    isDataLoaded = true;
    
    // Update live banner
    const statusEl = document.getElementById('live-status');
    if (statusEl) {
      statusEl.textContent = `Live: Verbonden`;
      statusEl.classList.add('connected');
    }
    
    return true;
  } catch (error) {
    console.error("Failed to load live Capelle data:", error);
    const statusEl = document.getElementById('live-status');
    if (statusEl) {
      statusEl.textContent = `API Fout`;
      statusEl.style.color = 'var(--accent-red)';
    }
    return false;
  }
}

// Get 5-year trend percentage for a feature
export function getTrend(featureKey) {
  if (!FEATURES[featureKey]) return null;
  const vals = FEATURES[featureKey].values;
  const recent = vals.filter(v => v !== null);
  if (recent.length < 2) return null;
  const latest = recent[recent.length - 1];
  const fiveAgo = recent.length >= 6 ? recent[recent.length - 6] : recent[0];
  if (fiveAgo === 0 || fiveAgo === null) return null;
  return ((latest - fiveAgo) / Math.abs(fiveAgo)) * 100;
}

// Get latest non-null value
export function getLatest(featureKey) {
  if (!FEATURES[featureKey]) return null;
  const vals = FEATURES[featureKey].values;
  for (let i = vals.length - 1; i >= 0; i--) {
    if (vals[i] !== null) return { value: vals[i], year: YEARS[i] };
  }
  return null;
}
