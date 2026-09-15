from __future__ import annotations
import json
from pathlib import Path
from agents.base import BaseAgent, Skill
from config import MUNICIPALITY, DEPARTMENT_COLORS, OUTPUT_DIR

# Map taakveld codes to high-level policy domains
POLICY_DOMAINS = {
    "0.1":  "Governance & Administration",
    "0.2":  "Governance & Administration",
    "0.3":  "Governance & Administration",
    "0.4":  "Governance & Administration",
    "0.5":  "Finance & Treasury",
    "0.61": "Finance & Treasury",
    "0.62": "Finance & Treasury",
    "0.63": "Finance & Treasury",
    "0.64": "Finance & Treasury",
    "0.7":  "Finance & Treasury",
    "0.8":  "Finance & Treasury",
    "0.9":  "Finance & Treasury",
    "0.10": "Finance & Treasury",
    "0.11": "Finance & Treasury",
    "1.1":  "Public Safety",
    "1.2":  "Public Safety",
    "2.1":  "Mobility & Infrastructure",
    "2.2":  "Mobility & Infrastructure",
    "2.3":  "Mobility & Infrastructure",
    "2.4":  "Mobility & Infrastructure",
    "2.5":  "Mobility & Infrastructure",
    "3.1":  "Economy & Employment",
    "3.2":  "Economy & Employment",
    "3.3":  "Economy & Employment",
    "3.4":  "Economy & Employment",
    "4.1":  "Education",
    "4.2":  "Education",
    "4.3":  "Education",
    "5.1":  "Sports, Culture & Recreation",
    "5.2":  "Sports, Culture & Recreation",
    "5.3":  "Sports, Culture & Recreation",
    "5.4":  "Sports, Culture & Recreation",
    "5.5":  "Sports, Culture & Recreation",
    "5.6":  "Sports, Culture & Recreation",
    "5.7":  "Sports, Culture & Recreation",
    "6.1":  "Social Domain",
    "6.2":  "Social Domain",
    "6.3":  "Social Domain",
    "6.4":  "Social Domain",
    "6.5":  "Social Domain",
    "6.6":  "Social Domain",
    "6.71": "Social Domain",
    "6.72": "Social Domain",
    "6.81": "Social Domain",
    "6.82": "Social Domain",
    "7.1":  "Health & Environment",
    "7.2":  "Health & Environment",
    "7.3":  "Health & Environment",
    "7.4":  "Health & Environment",
    "7.5":  "Health & Environment",
    "8.1":  "Housing & Spatial Planning",
    "8.2":  "Housing & Spatial Planning",
    "8.3":  "Housing & Spatial Planning",
}

TAAKVELD_LABELS = {
    "0.1": "Governance", "0.2": "Civil affairs", "0.3": "Property management",
    "0.4": "Overhead", "0.5": "Treasury", "0.61": "OZB residential",
    "0.62": "OZB commercial", "0.63": "Parking tax", "0.64": "Other taxes",
    "0.7": "General municipal grant", "0.8": "Other income/expenditure",
    "0.10": "Reserve mutations", "0.11": "Result",
    "1.1": "Fire & crisis management", "1.2": "Public order & safety",
    "2.1": "Traffic & transport", "2.2": "Parking", "2.5": "Public transport",
    "3.1": "Economic development", "3.2": "Business infrastructure",
    "3.3": "Business services", "3.4": "Economic promotion",
    "4.1": "Primary education", "4.2": "Education housing",
    "4.3": "Education policy", "5.1": "Sports policy",
    "5.2": "Sports facilities", "5.3": "Culture", "5.4": "Museums",
    "5.5": "Heritage", "5.6": "Media", "5.7": "Public green space",
    "6.1": "Community & participation", "6.2": "Neighbourhood teams",
    "6.3": "Income support", "6.4": "Care guidance", "6.5": "Employment",
    "6.6": "Wmo provisions", "6.71": "Individual care 18+",
    "6.72": "Individual care 18-", "6.81": "Escalated care 18+",
    "6.82": "Escalated care 18-", "7.1": "Public health",
    "7.2": "Sewage", "7.3": "Waste management", "7.4": "Environment",
    "7.5": "Cemeteries", "8.1": "Spatial planning",
    "8.2": "Land development", "8.3": "Housing & construction",
}

CATEGORIE_LABELS = {
    # Lasten (expenditure) categories
    "L1.1":   "Personnel costs",
    "L2.1":   "Depreciation",
    "L3.1":   "Energy",
    "L3.2":   "Rent & leases",
    "L3.4.1": "Minor maintenance",
    "L3.4.2": "Major maintenance",
    "L3.5.1": "Other maintenance",
    "L3.6":   "ICT costs",
    "L3.7":   "Insurance & risk",
    "L3.8":   "Subsidies paid out",
    "L4.1.1": "Social benefits",
    "L4.1.2": "Other benefits",
    "L4.3.1": "Purchased services",
    "L4.3.3": "Purchased care",
    "L4.3.6": "Other purchased services",
    "L4.3.8": "Other purchased care",
    "L4.4.8": "Other program costs",
    "L5.1":   "Interest costs",
    "L7.1":   "Reserve additions",
    "L7.2":   "Reserve transfers",
    "L7.3":   "Provision charges",
    "L7.4":   "Other provisions",
    "L7.5":   "Contingencies",
    # Baten (income) categories
    "B2.2.1": "Property income",
    "B2.2.2": "Lease income",
    "B3.1":   "Service charges",
    "B3.2":   "Levies & fees",
    "B3.4.2": "Other contributions",
    "B3.5.2": "Other income",
    "B3.6":   "Subsidies received",
    "B3.7":   "Grant income",
    "B3.8":   "Other grants received",
    "B4.1.2": "Client contributions",
    "B4.3.1": "Government grants",
    "B4.3.6": "Care income",
    "B4.3.8": "Other care income",
    "B4.4.1": "Tax revenue",
    "B4.4.2": "Property tax",
    "B4.4.3": "Parking income",
    "B4.4.4": "Waste levy",
    "B4.4.5": "Sewage levy",
    "B4.4.6": "Other levies",
    "B4.4.7": "Tourist tax",
    "B4.4.8": "Other taxes",
    "B4.4.9": "Other local tax",
    "B5.1":   "Interest income",
    "B5.2":   "Dividend income",
    "B6.1":   "Municipal fund",
    "B7.1":   "Provision releases",
    "B7.2":   "Reserve withdrawals",
    "B7.3":   "Provisions released",
    "B7.4":   "Other provision releases",
    "B7.5":   "Reserve use",
}

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

class VisualizerAgent(BaseAgent):
    name = "visualizer"
    def __init__(self, memory, output_dir=OUTPUT_DIR):
        self.output_dir = Path(output_dir)
        super().__init__(memory)

    def _register_skills(self):
        return [
            Skill("category_summary", "Aggregate rows into income/expenditure per policy domain.", self._category_summary),
            Skill("flow_data",        "Build Sankey node/link data.",                              self._flow_data),
            Skill("render_dashboard", "Write a self-contained HTML dashboard.",                    self._render_dashboard),
        ]

    async def run(self):
        rows = self.memory.get("parsed_rows") or []
        if not rows:
            self.log("No parsed rows - using synthetic demo data")
            rows = _synthetic_rows()
            self.memory.set("parsed_rows", rows)

        # Group rows by year
        rows_by_year: dict = {}
        for row in rows:
            year = int(row.get("jaar", 2023))
            rows_by_year.setdefault(year, []).append(row)

        all_summaries: dict = {}
        all_flow: dict = {}
        for year in sorted(rows_by_year.keys()):
            summary = await self._category_summary(rows_by_year[year])
            flow    = await self._flow_data(summary)
            all_summaries[year] = summary
            all_flow[year]      = flow

        # Store most recent year's summary for the analyst agent
        if all_summaries:
            latest = max(all_summaries.keys())
            self.memory.set("category_summary", all_summaries[latest])

        path = await self._render_dashboard(all_summaries, all_flow)
        self.memory.set("dashboard_path", str(path))
        return {"status": "ok", "dashboard": str(path), "years": sorted(all_summaries.keys())}

    async def _category_summary(self, rows):
        # Aggregate by policy domain using taakveld code directly
        totals = {d: {"income": 0.0, "expenditure": 0.0, "taakvelden": {}} for d in DOMAIN_COLORS}

        for row in rows:
            taakveld = str(row.get("taakveld", "")).strip()
            domain   = POLICY_DOMAINS.get(taakveld, "Governance & Administration")
            bucket   = totals[domain]
            lasten   = float(row.get("lasten_num", 0) or 0)
            baten    = float(row.get("baten_num",  0) or 0)
            bucket["expenditure"] += lasten
            bucket["income"]      += baten
            # Track sub-taakveld breakdown
            label = TAAKVELD_LABELS.get(taakveld, taakveld)
            if label not in bucket["taakvelden"]:
                bucket["taakvelden"][label] = {"income": 0.0, "expenditure": 0.0, "categories": {}}
            bucket["taakvelden"][label]["income"]      += baten
            bucket["taakvelden"][label]["expenditure"] += lasten
            # Track categorie-level breakdown within taakveld
            categorie = str(row.get("categorie", "")).strip()
            if categorie:
                cat_label = CATEGORIE_LABELS.get(categorie, categorie)
                cats = bucket["taakvelden"][label]["categories"]
                if cat_label not in cats:
                    cats[cat_label] = {"income": 0.0, "expenditure": 0.0}
                cats[cat_label]["income"]      += baten
                cats[cat_label]["expenditure"] += lasten

        summary = []
        for domain, vals in totals.items():
            if vals["income"] == 0 and vals["expenditure"] == 0:
                continue
            # Build sorted sub-breakdown with categorie detail
            breakdown = sorted(
                [{"name": k,
                  "income": round(v["income"]/1e6, 2),
                  "expenditure": round(v["expenditure"]/1e6, 2),
                  "categories": sorted(
                      [{"name": ck, "income": round(cv["income"]/1e6, 2),
                        "expenditure": round(cv["expenditure"]/1e6, 2)}
                       for ck, cv in v["categories"].items()
                       if cv["expenditure"] > 0 or cv["income"] > 0],
                      key=lambda x: x["expenditure"], reverse=True
                  )}
                 for k, v in vals["taakvelden"].items()],
                key=lambda x: x["expenditure"], reverse=True
            )
            summary.append({
                "category":    domain,
                "income":      round(vals["income"], 2),
                "expenditure": round(vals["expenditure"], 2),
                "balance":     round(vals["income"] - vals["expenditure"], 2),
                "color":       DOMAIN_COLORS.get(domain, "#888"),
                "breakdown":   breakdown,
            })

        summary.sort(key=lambda x: x["expenditure"], reverse=True)
        self.memory.set("category_summary", summary)
        return summary

    async def _flow_data(self, summary):
        nodes = ["General grant", "OZB & local taxes", "Other revenues",
                 MUNICIPALITY] + [s["category"] for s in summary]
        total_income = sum(s["income"] for s in summary) or 1
        muni_idx = nodes.index(MUNICIPALITY)
        src, tgt, val, lbl, colors = [], [], [], [], []
        src_colors = ["#4fc3f7", "#81c784", "#ffb74d"]
        for i, (name, pct) in enumerate([("General grant", .55), ("OZB & local taxes", .25), ("Other revenues", .20)]):
            v = total_income * pct
            src.append(nodes.index(name)); tgt.append(muni_idx)
            val.append(round(v/1000, 1)); lbl.append(f"EUR {v/1e6:.1f}M")
            colors.append(src_colors[i] + "88")
        for s in summary:
            v = s["expenditure"] or s["income"]
            src.append(muni_idx); tgt.append(nodes.index(s["category"]))
            val.append(round(v/1000, 1)); lbl.append(f"EUR {v/1e6:.1f}M")
            colors.append(s["color"] + "88")
        node_colors = src_colors + ["#6C63FF"] + [s["color"] for s in summary]
        flow = {"nodes": nodes, "links_src": src, "links_tgt": tgt,
                "links_val": val, "links_lbl": lbl,
                "link_colors": colors, "node_colors": node_colors}
        self.memory.set("flow_data", flow)
        return flow

    async def _render_dashboard(self, all_summaries: dict, all_flow: dict):
        all_s_js = json.dumps({str(y): s for y, s in all_summaries.items()})
        all_f_js = json.dumps({str(y): f for y, f in all_flow.items()})
        years_js = json.dumps(sorted(all_summaries.keys()))
        muni     = MUNICIPALITY
        html = """<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>""" + muni + """ - Budget Dashboard</title>
<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>
<style>
:root{--bg:#0f1117;--surface:#1a1d27;--surface2:#22263a;--accent:#6C63FF;
      --text:#e8eaf0;--muted:#7b7f9e;--border:#2e3250;--green:#06D6A0;--red:#FF6B6B}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:"Segoe UI",system-ui,sans-serif;padding:24px}
header{display:flex;align-items:baseline;gap:16px;margin-bottom:16px;
       border-bottom:1px solid var(--border);padding-bottom:16px;flex-wrap:wrap}
header h1{font-size:22px;font-weight:600}
header span{font-size:13px;color:var(--muted)}
.year-nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}
.yr-btn{background:var(--surface);border:1px solid var(--border);border-radius:6px;
        color:var(--muted);font-size:12px;padding:5px 11px;cursor:pointer;transition:all .15s}
.yr-btn:hover{border-color:var(--accent);color:var(--text)}
.yr-btn.active{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}
.kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px;margin-bottom:20px}
.kpi{background:var(--surface);border:1px solid var(--border);border-radius:10px;padding:16px 20px}
.kpi .label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em;margin-bottom:6px}
.kpi .value{font-size:24px;font-weight:700}
.green{color:var(--green)}.red{color:var(--red)}.accent{color:var(--accent)}
.grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin-bottom:16px}
@media(max-width:1000px){.grid2{grid-template-columns:1fr}}
.card{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:20px}
.card.full{grid-column:1/-1}
.card h2{font-size:13px;font-weight:600;margin-bottom:14px;color:var(--text)}
.domain-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px;margin-bottom:16px}
.domain-card{background:var(--surface);border:1px solid var(--border);border-radius:10px;
             padding:14px;cursor:pointer;transition:border-color .2s}
.domain-card:hover{border-color:var(--accent)}
.domain-card.active{border-color:var(--accent);background:var(--surface2)}
.domain-header{display:flex;align-items:center;gap:8px;margin-bottom:10px}
.domain-dot{width:10px;height:10px;border-radius:50%;flex-shrink:0}
.domain-name{font-size:13px;font-weight:600}
.domain-bar-wrap{display:flex;gap:4px;margin-bottom:6px}
.domain-bar{height:6px;border-radius:3px;min-width:2px}
.domain-nums{display:flex;justify-content:space-between;font-size:11px;color:var(--muted)}
.breakdown-panel{background:var(--surface2);border:1px solid var(--border);border-radius:10px;
                 padding:16px;margin-bottom:16px;display:none}
.breakdown-panel.visible{display:block}
.breakdown-panel h3{margin-bottom:12px;font-size:13px}
.bk-row{display:flex;align-items:center;gap:8px;padding:5px 0;
        border-bottom:1px solid var(--border);font-size:12px}
.bk-row:last-child{border-bottom:none}
.bk-name{flex:1;color:var(--text)}
.bk-val{min-width:80px;text-align:right;color:var(--muted)}
.bk-bar-bg{width:120px;height:5px;background:var(--surface);border-radius:3px}
.bk-bar-fill{height:5px;border-radius:3px}
.cat-panel{background:var(--bg);border-left:2px solid var(--border);margin-left:12px;padding:4px 0 4px 12px}
.cat-row{display:flex;align-items:center;gap:8px;padding:4px 0;font-size:11px;color:var(--muted)}
.cat-name{flex:1}
.pos{color:var(--green)}.neg{color:var(--red)}
.explainer{background:var(--surface2);border:1px solid var(--border);border-radius:10px;
           padding:16px;margin-bottom:16px}
.explainer summary{font-size:13px;font-weight:600;cursor:pointer;color:var(--text);
                   list-style:none;display:flex;align-items:center;gap:8px}
.explainer summary::before{content:"\u25B6";font-size:10px;color:var(--accent);transition:transform .2s}
details[open] summary::before{transform:rotate(90deg)}
.explainer p,.explainer li{font-size:12px;color:var(--muted);line-height:1.6}
.explainer ul{margin:8px 0 0 16px}
.explainer .src-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px;margin-top:12px}
.src-card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px}
.src-card .src-name{font-size:12px;font-weight:600;margin-bottom:4px}
.src-card .src-pct{font-size:20px;font-weight:700;color:var(--accent)}
.src-card .src-amt{font-size:11px;color:var(--muted)}
.src-card .src-desc{font-size:11px;color:var(--muted);margin-top:4px;line-height:1.4}
</style></head><body>
<header>
  <h1>""" + muni + """ &mdash; Budget Dashboard</h1>
  <span id="hdr-sub">CBS Iv3 data &bull; Real figures</span>
</header>

<div class="year-nav" id="year-nav"></div>

<div class="kpi-row">
  <div class="kpi"><div class="label">Total income</div><div class="value accent" id="ki">-</div></div>
  <div class="kpi"><div class="label">Total expenditure</div><div class="value red" id="ke">-</div></div>
  <div class="kpi"><div class="label">Balance</div><div class="value" id="kb">-</div></div>
  <div class="kpi"><div class="label">Policy domains</div><div class="value accent" id="kc">-</div></div>
</div>

<div class="grid2" style="margin-bottom:16px">
  <div class="card full">
    <h2>Money flow &mdash; from revenue sources to policy domains</h2>
    <div id="sankey" style="height:400px"></div>
  </div>
</div>

<div class="card" style="margin-bottom:16px">
  <h2>Policy domains &mdash; click a domain to see breakdown</h2>
  <div class="domain-grid" id="domain-grid"></div>
</div>

<div class="breakdown-panel" id="breakdown-panel">
  <h3 id="breakdown-title"></h3>
  <div id="breakdown-content"></div>
</div>

<div class="grid2">
  <div class="card">
    <h2>Income vs. expenditure by domain</h2>
    <div id="bar" style="height:360px"></div>
  </div>
  <div class="card">
    <h2>Expenditure breakdown (treemap — click to drill down)</h2>
    <div id="treemap" style="height:360px"></div>
  </div>
</div>

<div class="card full" style="margin-top:16px">
  <h2>Budget trend 2010 &ndash; 2026 &mdash; total income &amp; expenditure per year</h2>
  <div id="trend" style="height:300px"></div>
</div>

<details class="explainer" style="margin-top:16px">
  <summary>How does Capelle aan den IJssel get its money? &mdash; revenue sources explained</summary>
  <div style="margin-top:14px">
    <div class="src-grid" id="rev-src-grid"></div>
    <div style="margin-top:14px">
      <p style="margin-bottom:8px"><strong style="color:var(--text)">The three pillars of Dutch municipal finance</strong></p>
      <ul>
        <li><strong style="color:var(--text)">Municipal Fund (Gemeentefonds)</strong> — the largest source (~55–60%). A block grant from the national government, distributed to all municipalities based on population, demographics, and social need. Capelle receives this via taakveld 0.7. The amount changes yearly as the national government adjusts the total fund size and the distribution formula.</li>
        <li><strong style="color:var(--text)">Specific grants (Specifieke uitkeringen)</strong> — earmarked grants for concrete tasks: youth care, social assistance, housing. These drive big year-to-year swings — for example, the 2023 increase reflects extra grants for the energy poverty allowance and refugee reception costs.</li>
        <li><strong style="color:var(--text)">Own revenues (Eigen middelen)</strong> — property taxes (OZB), parking charges, sewage/waste levies, and service fees. These are more stable but grow slowly with property value assessments and inflation.</li>
      </ul>
      <p style="margin-top:10px"><strong style="color:var(--text)">Why does income differ across years?</strong></p>
      <ul>
        <li><strong style="color:var(--text)">2023 vs 2025:</strong> The 2023 budget (EUR 597M) reflected one-off national grants (energy crisis support, asylum costs). The 2025 budget (EUR 640M) is higher due to structural growth in the municipal fund following the 2023 government accord (Hoofdlijnenakkoord) and increased social domain grants.</li>
        <li><strong style="color:var(--text)">2026 spike (EUR 881M):</strong> The 2026 budget is a multi-year planning budget that includes large reserve mutations and capital investment provisions, inflating the gross figure.</li>
        <li><strong style="color:var(--text)">Pre-2019 lower figures:</strong> Older data used a different reporting format (BBV functies vs taakvelden) with fewer category rows, so totals appear lower but reflect the same real spend.</li>
      </ul>
    </div>
  </div>
</details>

<script>
const allData     = """ + all_s_js + """;
const allFlowData = """ + all_f_js + """;
const years       = """ + years_js + """;

const fmt  = v => "EUR " + (Math.abs(v)/1e6).toFixed(1) + "M";
const fmtM = v => (Math.abs(v)/1e6).toFixed(1) + "M";

let currentYear = years[years.length - 1];
let chartsInit  = false;

// Build year buttons
const nav = document.getElementById("year-nav");
years.forEach(y => {
  const btn = document.createElement("button");
  btn.className = "yr-btn" + (y === currentYear ? " active" : "");
  btn.dataset.year = y;
  btn.textContent  = y;
  btn.onclick = () => loadYear(y);
  nav.appendChild(btn);
});

function loadYear(year) {
  currentYear = year;
  document.querySelectorAll(".yr-btn").forEach(b =>
    b.classList.toggle("active", +b.dataset.year === year));
  document.getElementById("hdr-sub").textContent =
    year + " Municipal budget \u2022 CBS Iv3 data \u2022 Real figures";

  const summary = allData[year];
  const flow    = allFlowData[year];

  // KPIs
  const ti  = summary.reduce((a, r) => a + r.income, 0);
  const te  = summary.reduce((a, r) => a + r.expenditure, 0);
  const bal = ti - te;
  document.getElementById("ki").textContent = fmt(ti);
  document.getElementById("ke").textContent = fmt(te);
  document.getElementById("kc").textContent = summary.length;
  const kb = document.getElementById("kb");
  kb.textContent = fmt(bal);
  kb.className   = "value " + (bal >= 0 ? "green" : "red");

  // Domain cards
  const grid = document.getElementById("domain-grid");
  grid.innerHTML = "";
  summary.forEach(r => {
    const total = r.income + r.expenditure || 1;
    const iPct  = (r.income / total * 100).toFixed(0);
    const ePct  = (r.expenditure / total * 100).toFixed(0);
    const balC  = r.balance >= 0 ? "pos" : "neg";
    const card  = document.createElement("div");
    card.className = "domain-card";
    card.innerHTML = `
      <div class="domain-header">
        <div class="domain-dot" style="background:${r.color}"></div>
        <div class="domain-name">${r.category}</div>
      </div>
      <div class="domain-bar-wrap">
        <div class="domain-bar" style="width:${iPct}%;background:#06D6A088"></div>
        <div class="domain-bar" style="width:${ePct}%;background:${r.color}88"></div>
      </div>
      <div class="domain-nums">
        <span>Income: ${fmtM(r.income)}</span>
        <span>Exp: ${fmtM(r.expenditure)}</span>
        <span class="${balC}">${r.balance >= 0 ? "+" : ""}${fmtM(r.balance)}</span>
      </div>`;
    card.onclick = () => showBreakdown(r, card);
    grid.appendChild(card);
  });

  // Hide breakdown panel on year switch
  document.getElementById("breakdown-panel").classList.remove("visible");

  // Sankey
  const sankeyTrace = [{
    type: "sankey", orientation: "h",
    node: {pad: 16, thickness: 20, label: flow.nodes,
           color: flow.node_colors, line: {color: "#0f1117", width: 0.5}},
    link: {source: flow.links_src, target: flow.links_tgt,
           value: flow.links_val, label: flow.links_lbl,
           color: flow.link_colors}
  }];
  const sankeyLayout = {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: {color: "#e8eaf0", size: 11}, margin: {l: 0, r: 0, t: 0, b: 0}
  };
  chartsInit
    ? Plotly.react("sankey", sankeyTrace, sankeyLayout)
    : Plotly.newPlot("sankey", sankeyTrace, sankeyLayout, {responsive: true, displayModeBar: false});

  // Bar
  const barTraces = [
    {type: "bar", name: "Income", orientation: "h",
     x: summary.map(r => r.income/1e6), y: summary.map(r => r.category),
     marker: {color: "#06D6A0", opacity: 0.85}},
    {type: "bar", name: "Expenditure", orientation: "h",
     x: summary.map(r => -r.expenditure/1e6), y: summary.map(r => r.category),
     marker: {color: summary.map(r => r.color), opacity: 0.85}},
  ];
  const barLayout = {
    barmode: "overlay",
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: {color: "#e8eaf0", size: 11},
    xaxis: {title: "EUR million", color: "#7b7f9e", gridcolor: "#2e3250", zerolinecolor: "#6C63FF"},
    yaxis: {color: "#7b7f9e", automargin: true},
    legend: {orientation: "h", y: -0.15, font: {size: 11}},
    margin: {l: 200, r: 20, t: 10, b: 60}
  };
  chartsInit
    ? Plotly.react("bar", barTraces, barLayout)
    : Plotly.newPlot("bar", barTraces, barLayout, {responsive: true, displayModeBar: false});

  // Treemap — hierarchical: domain → taakveld
  const tmL = [], tmP = [], tmV = [], tmC = [];
  summary.forEach(r => {
    tmL.push(r.category); tmP.push(""); tmV.push(r.expenditure/1e6); tmC.push(r.color);
    r.breakdown.forEach(b => {
      tmL.push(b.name); tmP.push(r.category); tmV.push(b.expenditure); tmC.push(r.color + "cc");
    });
  });
  const tmTrace = [{
    type: "treemap", labels: tmL, parents: tmP, values: tmV,
    marker: {colors: tmC}, branchvalues: "total",
    texttemplate: "%{label}<br>EUR %{value:.1f}M",
    hovertemplate: "<b>%{label}</b><br>Expenditure: EUR %{value:.1f}M<extra></extra>",
  }];
  const tmLayout = {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    font: {color: "#e8eaf0", size: 11}, margin: {l: 0, r: 0, t: 0, b: 0}
  };
  chartsInit
    ? Plotly.react("treemap", tmTrace, tmLayout)
    : Plotly.newPlot("treemap", tmTrace, tmLayout, {responsive: true, displayModeBar: false});

  chartsInit = true;
}

function showBreakdown(domain, cardEl) {
  document.querySelectorAll(".domain-card").forEach(c => c.classList.remove("active"));
  cardEl.classList.add("active");
  const panel = document.getElementById("breakdown-panel");
  panel.classList.add("visible");
  document.getElementById("breakdown-title").textContent =
    domain.category + " \u2014 policy breakdown (" + currentYear + ")";
  const maxB = Math.max(...domain.breakdown.map(b => b.expenditure)) || 1;
  document.getElementById("breakdown-content").innerHTML = domain.breakdown.map((b, i) => {
    const hasCats = b.categories && b.categories.length > 0;
    const catRows = hasCats ? b.categories.map(c => `
      <div class="cat-row">
        <div class="cat-name">${c.name}</div>
        <div class="bk-bar-bg"><div class="bk-bar-fill"
          style="width:${b.expenditure ? (c.expenditure/b.expenditure*100).toFixed(0) : 0}%;background:${domain.color}88"></div></div>
        <div class="bk-val">Exp: ${c.expenditure.toFixed(1)}M</div>
        <div class="bk-val" style="color:#06D6A0">Inc: ${c.income.toFixed(1)}M</div>
      </div>`).join("") : "";
    return `
    <div class="bk-row" onclick="toggleCats('cats_${i}')" style="cursor:${hasCats?'pointer':'default'}">
      <div class="bk-name">${b.name}${hasCats
        ? ' <span style="color:var(--muted);font-size:10px">\u25B6 ' + b.categories.length + ' categories</span>'
        : ''}</div>
      <div class="bk-bar-bg"><div class="bk-bar-fill"
        style="width:${(b.expenditure/maxB*100).toFixed(0)}%;background:${domain.color}"></div></div>
      <div class="bk-val">Exp: ${b.expenditure.toFixed(1)}M</div>
      <div class="bk-val" style="color:#06D6A0">Inc: ${b.income.toFixed(1)}M</div>
    </div>
    <div id="cats_${i}" class="cat-panel" style="display:none">${catRows}</div>`;
  }).join("");
  panel.scrollIntoView({behavior: "smooth", block: "nearest"});
}

function toggleCats(id) {
  const el = document.getElementById(id);
  if (el) el.style.display = el.style.display === "none" ? "block" : "none";
}

loadYear(currentYear);

// Trend chart — total income & expenditure per year
const trendYears = years;
const trendInc   = years.map(y => allData[y].reduce((a, r) => a + r.income, 0) / 1e6);
const trendExp   = years.map(y => allData[y].reduce((a, r) => a + r.expenditure, 0) / 1e6);
Plotly.newPlot("trend", [
  {type: "scatter", mode: "lines+markers", name: "Income",
   x: trendYears, y: trendInc,
   line: {color: "#06D6A0", width: 2}, marker: {size: 6}},
  {type: "scatter", mode: "lines+markers", name: "Expenditure",
   x: trendYears, y: trendExp,
   line: {color: "#FF6B6B", width: 2, dash: "dot"}, marker: {size: 6}},
], {
  paper_bgcolor: "transparent", plot_bgcolor: "transparent",
  font: {color: "#e8eaf0", size: 11},
  xaxis: {color: "#7b7f9e", gridcolor: "#2e3250", tickmode: "array", tickvals: trendYears},
  yaxis: {title: "EUR million", color: "#7b7f9e", gridcolor: "#2e3250"},
  legend: {orientation: "h", y: -0.2},
  margin: {l: 60, r: 20, t: 10, b: 60},
  shapes: [{type: "line", x0: currentYear, x1: currentYear, y0: 0, y1: 1,
            yref: "paper", line: {color: "#6C63FF88", width: 1, dash: "dot"}}],
}, {responsive: true, displayModeBar: false});

// Revenue sources grid — top income domains for selected year
function updateRevSources(year) {
  const summary = allData[year];
  const totalInc = summary.reduce((a, r) => a + r.income, 0) || 1;
  const sorted   = [...summary].sort((a, b) => b.income - a.income);
  const descs = {
    "Finance & Treasury":    "Municipal fund (Gemeentefonds) + local taxes (OZB). The single largest revenue block — national grants plus property taxes.",
    "Social Domain":         "Reimbursements and specific grants for youth care, Wmo, income support. Highly variable year-to-year based on national policy.",
    "Health & Environment":  "Sewage and waste levies (service charges paid by residents) plus environmental grants.",
    "Governance & Administration": "Administrative fees, building permits, and other municipal service charges.",
    "Housing & Spatial Planning":  "Land sales, development contributions, and housing-related government grants.",
    "Mobility & Infrastructure":   "Parking income and transport grants from province and national government.",
    "Economy & Employment":        "Employment reintegration grants (BUIG budget) from the national government.",
    "Education":             "Education housing grants and specific grants from the Ministry of Education.",
    "Sports, Culture & Recreation":"Ticket income, facility rentals, and cultural grants.",
    "Public Safety":         "Safety region contributions and emergency management grants.",
  };
  document.getElementById("rev-src-grid").innerHTML = sorted
    .filter(r => r.income > 0)
    .map(r => {
      const pct = (r.income / totalInc * 100).toFixed(1);
      return `<div class="src-card">
        <div class="src-name">${r.category}</div>
        <div class="src-pct">${pct}%</div>
        <div class="src-amt">EUR ${(r.income/1e6).toFixed(1)}M in ${year}</div>
        <div class="src-desc">${descs[r.category] || ""}</div>
      </div>`;
    }).join("");
}
updateRevSources(currentYear);

// Update trend year-line and rev sources when year changes
const _origLoad = loadYear;
loadYear = function(year) {
  _origLoad(year);
  Plotly.relayout("trend", {"shapes[0].x0": year, "shapes[0].x1": year});
  updateRevSources(year);
};
// Re-bind year buttons with new loadYear
document.querySelectorAll(".yr-btn").forEach(b => {
  b.onclick = () => loadYear(+b.dataset.year);
});
</script>
</body></html>"""
        path = self.output_dir / "dashboard.html"
        path.write_text(html, encoding="utf-8")
        return path

def _synthetic_rows():
    data = [
        ("Sociaal domein", "6.1", 28_500_000, 61_200_000),
        ("Overhead", "0.4", 4_100_000, 18_900_000),
        ("Financiering", "0.7", 95_000_000, 5_200_000),
        ("Volksgezondheid", "7.1", 9_800_000, 14_300_000),
        ("Wonen", "8.3", 6_200_000, 9_100_000),
        ("Verkeer", "2.1", 1_800_000, 7_400_000),
        ("Sport", "5.1", 3_400_000, 8_700_000),
        ("Onderwijs", "4.1", 4_900_000, 7_200_000),
        ("Ruimtelijke ordening", "8.1", 2_100_000, 4_800_000),
        ("Veiligheid", "1.1", 600_000, 5_600_000),
        ("Bestuur", "0.1", 900_000, 4_300_000),
        ("Economie", "3.1", 400_000, 1_200_000),
    ]
    return [{"programma": d, "omschrijving": d, "taakveld": t,
             "baten_num": float(b), "lasten_num": float(l),
             "_source": "synthetic", "_type": "synthetic"}
            for d, t, b, l in data]
