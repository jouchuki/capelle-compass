import json
from pathlib import Path

DOMAIN_COLORS = {
    "Governance & Administration":  "#6C63FF",
    "Finance & Treasury":           "#2EC4B6",
    "Public Safety":                "#FF6B6B",
    "Mobility & Infrastructure":    "#FFD166",
    "Economy & Employment":         "#06D6A0",
    "Education":                    "#118AB2",
    "Sports, Culture & Recreation": "#EF476F",
    "Social Domain":                "#FB5607",
    "Health & Environment":         "#8338EC",
    "Housing & Spatial Planning":   "#3A86FF",
}

CAT_COLORS = {
    "Roads & Traffic":           "#FFD166",
    "Pavement & Paving":         "#FFC933",
    "Street Lighting":           "#FFBE0B",
    "Road & Traffic Signs":      "#F4A423",
    "Traffic Lights":            "#E89020",
    "Cables & Pipelines":        "#D07A1A",
    "Green Space & Trees":       "#06D6A0",
    "Playgrounds & Sports":      "#0EBF8F",
    "Graffiti":                  "#1BA882",
    "Sewage & Drainage":         "#8338EC",
    "Waterways & Canals":        "#9B55F0",
    "Water Nuisance / Flooding": "#AF72F4",
    "Odour Nuisance":            "#C48FF8",
    "Waste & Litter":            "#6C63FF",
    "Animal Nuisance":           "#8A84FF",
    "Civil Objects & Other":     "#7b7f9e",
    "Building Inspection":       "#3A86FF",
}


def render_dashboard(reports: list[dict], municipality: str, out_dir: str = "output") -> Path:
    rj  = json.dumps(reports)
    dc  = json.dumps(DOMAIN_COLORS)
    cc  = json.dumps(CAT_COLORS)

    by_cat = {}; by_domain = {}; by_status = {}
    new_count = sum(1 for r in reports if r.get("is_new"))
    for r in reports:
        by_cat[r.get("category_en","Other")]   = by_cat.get(r.get("category_en","Other"), 0) + 1
        by_domain[r.get("policy_domain","Other")] = by_domain.get(r.get("policy_domain","Other"), 0) + 1
        by_status[r.get("status","UNKNOWN")]   = by_status.get(r.get("status","UNKNOWN"), 0) + 1

    by_cat_s     = sorted(by_cat.items(), key=lambda x: -x[1])
    cat_labels   = json.dumps([x[0] for x in by_cat_s])
    cat_values   = json.dumps([x[1] for x in by_cat_s])
    dom_labels   = json.dumps(list(by_domain.keys()))
    dom_values   = json.dumps(list(by_domain.values()))
    dom_clrs     = json.dumps([DOMAIN_COLORS.get(d, "#888") for d in by_domain])
    total        = len(reports)
    reported     = by_status.get("REPORTED", 0)
    in_progress  = by_status.get("IN_PROGRESS", 0)
    new_display  = "block" if new_count else "none"

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{municipality} — Reports Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
:root{{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--accent:#6C63FF;
      --text:#e8eaf0;--muted:#7b7f9e;--border:#2e3250;--green:#06D6A0;--red:#FF6B6B;--amber:#FFD166}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:var(--bg);color:var(--text);font-family:"Segoe UI",system-ui,sans-serif;padding:24px}}
header{{display:flex;align-items:baseline;gap:16px;margin-bottom:20px;border-bottom:1px solid var(--border);padding-bottom:16px}}
header h1{{font-size:22px;font-weight:600}}
header span{{font-size:13px;color:var(--muted)}}
.kpi-row{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:18px}}
.kpi{{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:14px 18px}}
.kpi.hl{{border-color:var(--red);box-shadow:0 0 12px rgba(255,107,107,.25)}}
.kpi .label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:5px}}
.kpi .value{{font-size:24px;font-weight:700}}
.accent{{color:var(--accent)}}.green{{color:var(--green)}}.red{{color:var(--red)}}.amber{{color:var(--amber)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:14px;margin-bottom:14px}}
.grid3{{display:grid;grid-template-columns:2fr 1fr;gap:14px}}
@media(max-width:900px){{.grid2,.grid3{{grid-template-columns:1fr}}}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:18px;margin-bottom:14px}}
.card h2{{font-size:13px;font-weight:600;margin-bottom:12px}}
#map{{height:480px;border-radius:8px}}
.leaflet-popup-content-wrapper{{background:var(--surface2);color:var(--text);border:1px solid var(--border);border-radius:8px}}
.leaflet-popup-tip{{background:var(--surface2)}}
.filter-row{{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}}
.btn{{padding:5px 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface2);
      color:var(--muted);cursor:pointer;font-size:11px;transition:all .15s}}
.btn:hover,.btn.active{{border-color:var(--accent);color:var(--text);background:var(--surface)}}
.btn.new{{border-color:var(--red);color:var(--red)}}.btn.new.active{{background:rgba(255,107,107,.15)}}
.legend{{background:var(--surface2);border-radius:8px;padding:12px;font-size:11px}}
.legend-item{{display:flex;align-items:center;gap:6px;padding:3px 0}}
.dot{{width:10px;height:10px;border-radius:50%;flex-shrink:0}}
@keyframes pulse-ring{{0%{{transform:scale(1);opacity:.9}}60%{{transform:scale(2.2);opacity:0}}100%{{transform:scale(2.2);opacity:0}}}}
.pulse{{position:relative;display:inline-block}}
.pulse::after{{content:'';position:absolute;top:50%;left:50%;width:100%;height:100%;border-radius:50%;
  transform:translate(-50%,-50%);background:inherit;animation:pulse-ring 1.8s ease-out infinite}}
.tbl{{width:100%;border-collapse:collapse;font-size:11px}}
.tbl th{{text-align:left;color:var(--muted);padding:4px 8px;border-bottom:1px solid var(--border);font-weight:600}}
.tbl td{{padding:5px 8px;border-bottom:1px solid var(--border)}}
.tbl tr:last-child td{{border-bottom:none}}
</style></head><body>
<header>
  <h1>{municipality} — Resident Reports</h1>
  <span>BuitenBeter live data &bull; open reports &bull; click markers for details</span>
</header>
<div class="kpi-row">
  <div class="kpi"><div class="label">Total open</div><div class="value accent">{total}</div></div>
  <div class="kpi hl"><div class="label">New this run</div><div class="value red">{new_count}</div></div>
  <div class="kpi"><div class="label">Just reported</div><div class="value amber">{reported}</div></div>
  <div class="kpi"><div class="label">In progress</div><div class="value green">{in_progress}</div></div>
  <div class="kpi"><div class="label">Categories</div><div class="value accent">{len(by_cat)}</div></div>
</div>
<div class="card">
  <h2>Live map</h2>
  <div class="filter-row" id="fr">
    <button class="btn active" onclick="filter('ALL',this)">All</button>
    <button class="btn new" onclick="filter('NEW',this)">&#9679; New only</button>
  </div>
  <div class="grid3">
    <div id="map"></div>
    <div class="legend" id="legend"></div>
  </div>
</div>
<div class="grid2">
  <div class="card"><h2>By category</h2><div id="bar" style="height:320px"></div></div>
  <div class="card"><h2>By policy domain</h2><div id="pie" style="height:320px"></div></div>
</div>
<div class="card" id="new-card" style="display:{new_display}">
  <h2>New reports this run</h2>
  <table class="tbl"><thead><tr><th>ID</th><th>Category</th><th>Domain</th><th>Status</th><th>Description</th></tr></thead>
  <tbody id="new-tbody"></tbody></table>
</div>
<script>
const reports={rj}, DC={dc}, CC={cc};
const CAT_LABELS={cat_labels}, CAT_VALUES={cat_values};
const DOM_LABELS={dom_labels}, DOM_VALUES={dom_values}, DOM_CLRS={dom_clrs};

const map=L.map('map').setView([51.935,4.55],13);
L.tileLayer('https://{{s}}.basemaps.cartocdn.com/dark_all/{{z}}/{{x}}/{{y}}{{r}}.png',{{attribution:'&copy; OSM &copy; CARTO',maxZoom:19}}).addTo(map);

const markers=[];
reports.forEach(r=>{{
  const c=CC[r.category_en]||'#888', isNew=!!r.is_new, sz=isNew?14:11;
  const dot=isNew
    ?`<div class="pulse" style="width:${{sz}}px;height:${{sz}}px;border-radius:50%;background:${{c}};border:2px solid rgba(255,255,255,.6)"></div>`
    :`<div style="width:${{sz}}px;height:${{sz}}px;border-radius:50%;background:${{c}};border:2px solid rgba(255,255,255,.3);opacity:.85"></div>`;
  const m=L.marker([r.latitude,r.longitude],{{icon:L.divIcon({{html:dot,className:'',iconSize:[sz,sz]}})}});
  m.bindPopup(`<div style="font-size:12px;line-height:1.6">
    ${{isNew?'<div style="color:#FF6B6B;font-size:10px;font-weight:700">&#9679; NEW</div>':''}}
    <div style="font-weight:600;color:${{c}}">${{r.category_en}}</div>
    <div style="color:#aaa;font-size:10px">${{r.category_nl}}</div>
    <div>Status: <span style="color:${{r.status==='IN_PROGRESS'?'#06D6A0':'#FFD166'}}">${{r.status==='IN_PROGRESS'?'In progress':'Reported'}}</span></div>
    ${{r.description?`<div style="margin-top:4px;color:#ccc;font-size:11px">${{r.description}}</div>`:''}}
    <div style="color:#555;font-size:10px">ID ${{r.id}} &bull; ${{r.policy_domain}}</div>
    <div style="color:#555;font-size:10px">First seen: ${{(r.first_seen||'').slice(0,16).replace('T',' ')}}</div>
  </div>`);
  markers.push({{m,cat:r.category_en,isNew}});
  m.addTo(map);
}});

const cats=[...new Set(reports.map(r=>r.category_en))].sort();
cats.forEach(cat=>{{
  const b=document.createElement('button');
  b.className='btn'; b.textContent=cat; b.onclick=()=>filter(cat,b);
  document.getElementById('fr').appendChild(b);
}});

function filter(cat,btn){{
  document.querySelectorAll('.btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  markers.forEach(entry=>{{
    const show=cat==='ALL'||(cat==='NEW'&&entry.isNew)||entry.cat===cat;
    show?entry.m.addTo(map):map.removeLayer(entry.m);
  }});
}}

const leg=document.getElementById('legend');
leg.innerHTML='<div style="font-size:11px;font-weight:600;margin-bottom:8px;color:var(--muted)">CATEGORIES</div>';
const cc2={{}};
reports.forEach(r=>cc2[r.category_en]=(cc2[r.category_en]||0)+1);
Object.entries(cc2).sort((a,b)=>b[1]-a[1]).forEach(([cat,n])=>{{
  const c=CC[cat]||'#888';
  leg.innerHTML+=`<div class="legend-item"><div class="dot" style="background:${{c}}"></div><div style="flex:1">${{cat}}</div><div style="color:var(--muted)">${{n}}</div></div>`;
}});

const tb=document.getElementById('new-tbody');
if(tb) reports.filter(r=>r.is_new).forEach(r=>{{
  const c=CC[r.category_en]||'#888';
  tb.innerHTML+=`<tr><td style="color:var(--muted)">${{r.id}}</td><td style="color:${{c}}">${{r.category_en}}</td>
    <td style="color:var(--muted)">${{r.policy_domain}}</td>
    <td style="color:${{r.status==='IN_PROGRESS'?'#06D6A0':'#FFD166'}}">${{r.status==='IN_PROGRESS'?'In progress':'Reported'}}</td>
    <td style="color:#aaa">${{r.description||''}}</td></tr>`;
}});

Plotly.newPlot('bar',[{{type:'bar',orientation:'h',x:CAT_VALUES,y:CAT_LABELS,
  marker:{{color:CAT_LABELS.map(c=>CC[c]||'#888'),opacity:.85}},text:CAT_VALUES,textposition:'outside'}}],
  {{paper_bgcolor:'transparent',plot_bgcolor:'transparent',font:{{color:'#e8eaf0',size:11}},
    xaxis:{{color:'#7b7f9e',gridcolor:'#2e3250'}},yaxis:{{color:'#7b7f9e',automargin:true}},
    margin:{{l:180,r:40,t:10,b:40}}}},{{responsive:true,displayModeBar:false}});

Plotly.newPlot('pie',[{{type:'pie',labels:DOM_LABELS,values:DOM_VALUES,marker:{{colors:DOM_CLRS}},
  hole:.45,textinfo:'label+percent',textfont:{{size:10}},
  hovertemplate:'<b>%{{label}}</b><br>%{{value}} reports<extra></extra>'}}],
  {{paper_bgcolor:'transparent',font:{{color:'#e8eaf0',size:10}},showlegend:false,margin:{{l:20,r:20,t:20,b:20}}}},
  {{responsive:true,displayModeBar:false}});
</script></body></html>"""

    path = Path(out_dir) / "reports_dashboard.html"
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    path.write_text(html, encoding="utf-8")
    return path
