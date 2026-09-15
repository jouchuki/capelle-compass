# Compass municipal dashboard

A data platform for improving quality of life in **Capelle aan den IJssel** by connecting public indicators, municipal policy, and local data into one interface.

---

## What it does

**Compass municipal dashboard** helps the municipality, council, and residents answer one central question:

> Are municipal policies actually improving daily life in Capelle — and where should attention go next?

---

## Dashboard pages

| Page | Description |
|---|---|
| **City Overview** | Live KPI cards covering population, housing, economy, and mobility — pulled directly from CBS Open Data |
| **Trends Explorer** | Interactive time-series charts per quality-of-life domain |
| **Neighbourhood** | Per-neighbourhood indicator breakdown across Capelle's wijken |
| **Benchmark** | Compare Capelle's indicators against peer municipalities |
| **Policy Explorer** | Searchable and filterable browser of all indexed Capelle municipal policies |
| **Policy Insights** | Aggregate statistics: domain distributions, approval bodies, legal bases, target groups, and timeline |
| **Priority Matrix** | Indicator-level prioritisation view based on trend urgency and policy coverage |
| **Action Matrix** | Sortable table of CBS indicators with 5-year trends and suggested policy actions |
| **Data Integrity** | Validation view showing CBS API connection status, data completeness, and missing years |

---

## Quality-of-life dimensions

The platform is organised around Capelle's core quality-of-life themes:

- **Safe & liveable neighbourhoods**
- **Healthy & vital residents**
- **Housing & accessibility**
- **Mobility & public space**
- **Green & climate-resilient environment**
- **Participation & social inclusion**
- **Work & financial security**

---

## Tech stack

- **Frontend:** Vite + Vanilla JS
- **Charts:** Chart.js
- **Live data:** CBS Open Data API (dataset `70072ned`, municipality `GM0502`)
- **Policy data:** Pre-processed and served as static JSON
- **Backend:** Python / Flask (policy API)

---

## Getting started

### Backend (policy API)

```bash
cd scripts/
pip install -r requirements.txt
python api.py
```

The API starts on `http://127.0.0.1:5000`.

### Frontend

```bash
npm install
npm run dev      # development server (http://localhost:5173)
npm run build    # production build → dist/
npm run preview  # preview production build locally
```

Both the API and frontend must run simultaneously for full functionality.

---

## Project structure

```
├── index.html
├── package.json
├── vite.config.js
├── scripts/
│   ├── api.py                  # Flask backend (policy API)
│   └── requirements.txt        # Python dependencies
├── public/
│   └── data/
│       └── policy_database.json
└── src/
    ├── main.js                 # App entry point & SPA router
    ├── pages/                  # One file per dashboard page
    ├── data/                   # Data loaders and static definitions
    ├── utils/                  # Formatters and chart helpers
    └── styles/                 # Global CSS
```
