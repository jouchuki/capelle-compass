from __future__ import annotations
import json
from pathlib import Path
import anthropic
from agents.base import BaseAgent, Skill
from config import MUNICIPALITY, OUTPUT_DIR


class ClaudeAnalystAgent(BaseAgent):
    name = "claude_analyst"

    def __init__(self, memory, output_dir=OUTPUT_DIR):
        self.output_dir = Path(output_dir)
        self._client = anthropic.Anthropic()
        super().__init__(memory)

    def _register_skills(self):
        return [
            Skill("generate_insights",     "Ask Claude to analyse the budget in detail.", self._generate_insights),
            Skill("inject_into_dashboard", "Inject AI insights panel into dashboard.html.", self._inject_into_dashboard),
        ]

    async def run(self):
        summary = self.memory.get("category_summary")
        if not summary:
            self.log("No category_summary in memory — skipping")
            return {"status": "skipped"}

        rows = self.memory.get("parsed_rows") or []
        insights = await self.get_skill("generate_insights")(summary, rows)
        self.memory.set("insights", insights)

        insights_path = self.output_dir / "insights.json"
        insights_path.write_text(json.dumps(insights, indent=2, ensure_ascii=False))
        self.log(f"Wrote {insights_path}")

        await self.get_skill("inject_into_dashboard")(insights)
        return {"status": "ok", "insights_path": str(insights_path)}

    async def _generate_insights(self, summary: list, rows: list) -> dict:
        self.log("Calling Claude to analyse budget data ...")

        total_exp = sum(s["expenditure"] for s in summary)
        total_inc = sum(s["income"] for s in summary)

        # --- Revenue breakdown: top income-generating taakvelden ---
        revenue_rows = sorted(
            [r for r in rows if float(r.get("baten_num", 0) or 0) > 0],
            key=lambda r: float(r.get("baten_num", 0) or 0),
            reverse=True,
        )[:15]
        revenue_text = "\n".join(
            f"  - {r['omschrijving']} (taakveld {r['taakveld']}): EUR {float(r['baten_num'])/1e6:.2f}M"
            for r in revenue_rows
        )

        # --- Spending breakdown: full taakveld detail per domain ---
        domain_detail_lines = []
        for s in summary:
            domain_detail_lines.append(
                f"\n{s['category']} — total exp EUR {s['expenditure']/1e6:.1f}M | "
                f"income EUR {s['income']/1e6:.1f}M | balance EUR {s['balance']/1e6:.1f}M"
            )
            for bk in s.get("breakdown", []):
                domain_detail_lines.append(
                    f"    • {bk['name']}: exp EUR {bk['expenditure']}M, inc EUR {bk['income']}M"
                )
        domain_text = "\n".join(domain_detail_lines)

        prompt = f"""You are a senior public finance analyst. Analyse the full 2023 municipal budget of {MUNICIPALITY} (Netherlands).

=== TOTALS ===
Total expenditure : EUR {total_exp/1e6:.1f}M
Total income      : EUR {total_inc/1e6:.1f}M
Balance           : EUR {(total_inc-total_exp)/1e6:.1f}M

=== REVENUE SOURCES (top income-generating tasks) ===
{revenue_text}

=== EXPENDITURE + INCOME BY POLICY DOMAIN (with taakveld breakdown) ===
{domain_text}

Return a JSON object with exactly these keys — no markdown, no extra text:
{{
  "executive_summary": "3-4 sentence plain-language overview of the overall budget",
  "balance_analysis": "paragraph on fiscal health: is the budget balanced, where are surpluses/deficits and why",
  "revenue_breakdown": [
    {{"source": "name of revenue source or taakveld", "amount_eur_m": 12.3, "insight": "why this matters or what drives it"}}
  ],
  "domain_analysis": [
    {{
      "domain": "policy domain name",
      "expenditure_eur_m": 45.6,
      "income_eur_m": 12.3,
      "balance_eur_m": -33.3,
      "summary": "1-2 sentences on what this domain covers and why it costs what it does",
      "top_tasks": [
        {{"task": "taakveld name", "expenditure_eur_m": 10.2, "note": "brief insight"}}
      ],
      "flag": "high_cost|self_funding|deficit|surplus|normal"
    }}
  ],
  "notable_observations": ["specific data-backed observation 1", "observation 2", "observation 3"],
  "efficiency_flags": [
    {{"domain": "domain name", "issue": "e.g. very low income-to-cost ratio", "detail": "what the numbers show"}}
  ],
  "citizen_questions": ["specific question with domain/number 1", "question 2", "question 3", "question 4"]
}}

Rules:
- revenue_breakdown: include at least 5 entries covering the main income sources
- domain_analysis: include ALL domains from the data, sorted by expenditure descending
- top_tasks: include up to 3 taakvelden per domain (the biggest spenders)
- All EUR amounts must be numbers (not strings)
- flag meanings: high_cost=largest spenders, self_funding=income covers most costs, deficit=large negative balance, surplus=positive balance, normal=balanced mid-range"""

        collected: list[str] = []
        with self._client.messages.stream(
            model="claude-opus-4-6",
            max_tokens=4096,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                collected.append(text)

        raw = "".join(collected).strip()

        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1] if len(parts) > 1 else raw
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        try:
            insights = json.loads(raw)
        except json.JSONDecodeError:
            self.log("Warning: could not parse Claude response as JSON")
            insights = {
                "executive_summary": raw,
                "balance_analysis": "",
                "revenue_breakdown": [],
                "domain_analysis": [],
                "notable_observations": [],
                "efficiency_flags": [],
                "citizen_questions": [],
            }

        self.log("Insights generated")
        return insights

    async def _inject_into_dashboard(self, insights: dict) -> None:
        dashboard_path = self.output_dir / "dashboard.html"
        if not dashboard_path.exists():
            self.log("dashboard.html not found — skipping injection")
            return

        html = dashboard_path.read_text(encoding="utf-8")
        panel = _build_insights_html(insights)
        html = html.replace("</body>", panel + "\n</body>")
        dashboard_path.write_text(html, encoding="utf-8")
        self.log("Injected AI insights panel into dashboard.html")


# ── HTML rendering ────────────────────────────────────────────────────────────

FLAG_STYLE = {
    "high_cost":     ("🔴", "#FF6B6B"),
    "deficit":       ("🟠", "#FFD166"),
    "self_funding":  ("🟢", "#06D6A0"),
    "surplus":       ("🟢", "#06D6A0"),
    "normal":        ("⚪", "#7b7f9e"),
}


def _build_insights_html(insights: dict) -> str:
    exec_summary    = insights.get("executive_summary", "")
    balance         = insights.get("balance_analysis", "")
    revenue_bk      = insights.get("revenue_breakdown", [])
    domain_analysis = insights.get("domain_analysis", [])
    notable         = insights.get("notable_observations", [])
    flags           = insights.get("efficiency_flags", [])
    questions       = insights.get("citizen_questions", [])

    def li(items): return "".join(f"<li style='margin-bottom:6px'>{i}</li>" for i in items)

    # Revenue breakdown table
    rev_rows = "".join(
        f"<tr><td style='padding:6px 8px;border-bottom:1px solid #2e3250;color:#c8cae0'>{r.get('source','')}</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #2e3250;color:#06D6A0;text-align:right'>EUR {r.get('amount_eur_m',0):.1f}M</td>"
        f"<td style='padding:6px 8px;border-bottom:1px solid #2e3250;color:#7b7f9e;font-size:11px'>{r.get('insight','')}</td></tr>"
        for r in revenue_bk
    )
    revenue_table = f"""
    <table style="width:100%;border-collapse:collapse;font-size:12px">
      <thead><tr>
        <th style="text-align:left;padding:5px 8px;color:#7b7f9e;font-size:10px;text-transform:uppercase;border-bottom:1px solid #2e3250">Source</th>
        <th style="text-align:right;padding:5px 8px;color:#7b7f9e;font-size:10px;text-transform:uppercase;border-bottom:1px solid #2e3250">Amount</th>
        <th style="text-align:left;padding:5px 8px;color:#7b7f9e;font-size:10px;text-transform:uppercase;border-bottom:1px solid #2e3250">Insight</th>
      </tr></thead>
      <tbody>{rev_rows}</tbody>
    </table>"""

    # Domain analysis cards
    domain_cards = ""
    for d in domain_analysis:
        flag   = d.get("flag", "normal")
        icon, color = FLAG_STYLE.get(flag, ("⚪", "#7b7f9e"))
        tasks  = d.get("top_tasks", [])
        task_rows = "".join(
            f"<div style='display:flex;justify-content:space-between;padding:4px 0;"
            f"border-bottom:1px solid #2e325044;font-size:11px'>"
            f"<span style='color:#c8cae0'>{t.get('task','')}</span>"
            f"<span style='color:#7b7f9e;margin-left:8px;flex-shrink:0'>EUR {t.get('expenditure_eur_m',0):.1f}M</span>"
            f"</div>"
            for t in tasks
        )
        bal_val = d.get("balance_eur_m", 0)
        bal_color = "#06D6A0" if bal_val >= 0 else "#FF6B6B"
        domain_cards += f"""
        <div style="background:#1a1d27;border:1px solid #2e3250;border-left:3px solid {color};
                    border-radius:10px;padding:14px;break-inside:avoid">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">
            <span style="font-size:13px;font-weight:600;color:#e8eaf0">{icon} {d.get('domain','')}</span>
            <span style="font-size:11px;color:{bal_color}">
              {"+" if bal_val >= 0 else ""}{bal_val:.1f}M
            </span>
          </div>
          <div style="display:flex;gap:16px;margin-bottom:8px;font-size:11px">
            <span style="color:#FF6B6B">Exp: EUR {d.get('expenditure_eur_m',0):.1f}M</span>
            <span style="color:#06D6A0">Inc: EUR {d.get('income_eur_m',0):.1f}M</span>
          </div>
          <p style="font-size:12px;color:#7b7f9e;line-height:1.5;margin-bottom:10px">{d.get('summary','')}</p>
          {f'<div style="margin-top:6px">{task_rows}</div>' if task_rows else ''}
        </div>"""

    # Efficiency flags
    flag_items = "".join(
        f"<div style='background:#22263a;border-radius:8px;padding:10px 14px;font-size:12px'>"
        f"<span style='color:#FFD166;font-weight:600'>{f.get('domain','')}</span>"
        f"<span style='color:#7b7f9e'> — {f.get('issue','')}</span>"
        f"<div style='color:#c8cae0;margin-top:4px'>{f.get('detail','')}</div></div>"
        for f in flags
    ) if flags else ""

    # Citizen question chips
    chips = "".join(
        f'<span style="background:#6C63FF22;border:1px solid #6C63FF55;border-radius:6px;'
        f'padding:6px 12px;font-size:12px;color:#c8cae0">{q}</span>'
        for q in questions
    )

    return f"""
<style>
.ai-section h3{{font-size:11px;font-weight:600;color:#7b7f9e;text-transform:uppercase;
               letter-spacing:.05em;margin-bottom:10px}}
</style>
<div class="card ai-section" style="margin-top:16px;border-color:#6C63FF55">
  <h2 style="color:#6C63FF;margin-bottom:20px">&#x1F9E0; Claude AI Analysis</h2>

  <!-- Summary + Balance -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">
    <div>
      <h3>Executive Summary</h3>
      <p style="font-size:13px;line-height:1.65;color:#c8cae0">{exec_summary}</p>
    </div>
    <div>
      <h3>Fiscal Health</h3>
      <p style="font-size:13px;line-height:1.65;color:#c8cae0">{balance}</p>
    </div>
  </div>

  <!-- Revenue breakdown -->
  <div style="margin-bottom:20px">
    <h3>Revenue Sources — Detailed Breakdown</h3>
    {revenue_table}
  </div>

  <!-- Domain analysis -->
  <div style="margin-bottom:20px">
    <h3>Policy Domain Analysis — with Taakveld Detail</h3>
    <div style="columns:2;column-gap:14px;orphans:1;widows:1">
      {domain_cards}
    </div>
  </div>

  <!-- Notable observations + flags -->
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-bottom:20px">
    <div>
      <h3>Notable Observations</h3>
      <ul style="font-size:13px;line-height:1.8;color:#c8cae0;padding-left:18px">{li(notable)}</ul>
    </div>
    <div>
      <h3>Efficiency Flags</h3>
      <div style="display:flex;flex-direction:column;gap:8px">{flag_items if flag_items else '<p style="font-size:12px;color:#7b7f9e">No major flags identified.</p>'}</div>
    </div>
  </div>

  <!-- Citizen questions -->
  <div style="padding-top:14px;border-top:1px solid #2e3250">
    <h3>Questions Citizens Might Ask</h3>
    <div style="display:flex;flex-wrap:wrap;gap:8px">{chips}</div>
  </div>

  <p style="font-size:10px;color:#7b7f9e;margin-top:14px">Generated by claude-opus-4-6 &bull; adaptive thinking</p>
</div>"""
