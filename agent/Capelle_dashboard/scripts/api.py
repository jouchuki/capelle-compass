import os
import json
import math
import sqlite3
import logging
import traceback
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

DB_PATH = os.path.join(os.path.dirname(__file__), "policies_checkpoint.db")
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'src', 'data')

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def parse_metadata(row):
    """Parse a DB row into a clean policy dict."""
    try:
        meta = json.loads(row['metadata'])
        meta['url'] = row['url']
        meta['processed_at'] = row['processed_at']
        return meta
    except Exception:
        return None

def get_all_policies():
    """Load the local crawler database, or the bundled policy snapshot."""
    if not os.path.exists(DB_PATH):
        with open(os.path.join(DATA_DIR, "policy_database.json"), encoding="utf-8") as stream:
            return json.load(stream).get("policies", [])
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT url, title, metadata, processed_at FROM policies')
    rows = c.fetchall()
    conn.close()
    policies = []
    for row in rows:
        p = parse_metadata(row)
        if p:
            policies.append(p)
    return policies

# ─── Serve static policy data ────────────────────────────────────────
@app.route('/data/policy_database.json')
def serve_policy_json():
    json_path = os.path.join(DATA_DIR, 'policy_database.json')
    if os.path.exists(json_path):
        return send_from_directory(os.path.dirname(json_path), 'policy_database.json', mimetype='application/json')
    return jsonify({'error': 'policy_database.json not found'}), 404

# ─── Policies (paginated, filterable) ────────────────────────────────
@app.route('/api/policies')
def get_policies():
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 20, type=int)
    domain = request.args.get('domain', '')
    qol = request.args.get('qol', '')
    q = request.args.get('q', '')
    sort = request.args.get('sort', 'title')

    policies = get_all_policies()
    result = []

    for p in policies:
        # Domain filter
        if domain and p.get('domain', '').lower() != domain.lower():
            continue

        # QoL dimension filter
        if qol:
            dims = p.get('quality_of_life_dimensions', [])
            if qol.lower() not in [d.lower() for d in dims]:
                continue

        # Text search
        if q:
            q_lower = q.lower()
            searchable = ' '.join([
                str(p.get('title', '')),
                str(p.get('summary', '')),
                str(p.get('target_group', '')),
                str(p.get('domain', '')),
                str(p.get('legal_basis', '')),
                str(p.get('implications', '')),
                str(p.get('benefits', '')),
                ' '.join(p.get('expected_outcomes', [])),
                ' '.join(p.get('linked_indicators', [])),
                str(p.get('priority_explanation', '')),
            ]).lower()
            if q_lower not in searchable:
                continue

        result.append(p)

    # Sort
    if sort == 'priority':
        result.sort(key=lambda x: x.get('priority_score', 0) if isinstance(x.get('priority_score', 0), (int, float)) else 0, reverse=True)
    elif sort == 'impact_score':
        result.sort(key=lambda x: x.get('impact_score', 0) if isinstance(x.get('impact_score', 0), (int, float)) else 0, reverse=True)
    elif sort == 'date':
        result.sort(key=lambda x: x.get('date', 'unknown'), reverse=True)
    else:
        result.sort(key=lambda x: x.get('title', ''))

    total = len(result)
    total_pages = max(1, math.ceil(total / per_page))
    start = (page - 1) * per_page
    end = start + per_page

    return jsonify({
        'policies': result[start:end],
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': total_pages,
    })

# ─── Stats (for Insights page) ──────────────────────────────────────
@app.route('/api/policies/stats')
def get_stats():
    policies = get_all_policies()

    domains = {}
    impact_scores = []
    target_groups = {}
    legal_bases = {}
    approved_by = {}
    dates = {}
    qol_dims = {}
    total = 0
    no_budget_count = 0
    v2_count = 0

    for m in policies:
        total += 1
        d = m.get('domain', 'Onbekend')
        domains[d] = domains.get(d, 0) + 1

        s = m.get('impact_score', 0)
        if isinstance(s, (int, float)):
            impact_scores.append(s)

        t = m.get('target_group', 'Onbekend')
        target_groups[t] = target_groups.get(t, 0) + 1

        lb = m.get('legal_basis', 'Onbekend')
        legal_bases[lb] = legal_bases.get(lb, 0) + 1

        ab = m.get('approved_by', 'Onbekend')
        approved_by[ab] = approved_by.get(ab, 0) + 1

        dt = m.get('date', 'unknown')
        yr = dt[:4] if dt and len(dt) >= 4 and dt[:4].isdigit() else 'onbekend'
        dates[yr] = dates.get(yr, 0) + 1

        budget = str(m.get('budget_spent', '')).lower()
        if 'niet vermeld' in budget or 'unknown' in budget or budget == '':
            no_budget_count += 1

        # V2 fields
        for dim in m.get('quality_of_life_dimensions', []):
            qol_dims[dim] = qol_dims.get(dim, 0) + 1
        if m.get('priority_score') is not None:
            v2_count += 1

    avg_impact = round(sum(impact_scores) / len(impact_scores), 1) if impact_scores else 0

    return jsonify({
        'total': total,
        'domains': dict(sorted(domains.items(), key=lambda x: -x[1])),
        'avg_impact_score': avg_impact,
        'no_budget_percentage': round(100 * no_budget_count / total) if total else 0,
        'legal_basis_with_data_percentage': round(100 * (total - legal_bases.get('Niet vermeld', 0) - legal_bases.get('Onbekend', 0)) / total) if total else 0,
        'target_groups': dict(sorted(target_groups.items(), key=lambda x: -x[1])[:15]),
        'legal_bases': dict(sorted(legal_bases.items(), key=lambda x: -x[1])[:15]),
        'approved_by': dict(sorted(approved_by.items(), key=lambda x: -x[1])[:10]),
        'years': dict(sorted(dates.items())),
        'qol_dimensions': dict(sorted(qol_dims.items(), key=lambda x: -x[1])),
        'v2_count': v2_count,
    })

# ─── QoL dimension aggregation ──────────────────────────────────────
@app.route('/api/policies/qol')
def get_qol():
    """Aggregate policies per quality-of-life dimension."""
    policies = get_all_policies()
    
    dims = {}
    for p in policies:
        for dim_id in p.get('quality_of_life_dimensions', []):
            if dim_id not in dims:
                dims[dim_id] = {'count': 0, 'policies': [], 'avg_priority': 0, 'scores': []}
            dims[dim_id]['count'] += 1
            dims[dim_id]['policies'].append({
                'title': p.get('title', ''),
                'url': p.get('url', ''),
                'priority_score': p.get('priority_score'),
                'status': p.get('status'),
            })
            ps = p.get('priority_score')
            if isinstance(ps, (int, float)):
                dims[dim_id]['scores'].append(ps)

    # Calculate averages
    for dim_id, data in dims.items():
        data['avg_priority'] = round(sum(data['scores']) / len(data['scores']), 2) if data['scores'] else None
        del data['scores']

    return jsonify({'dimensions': dims, 'total_policies': len(policies)})

# ─── Priority ranking ───────────────────────────────────────────────
@app.route('/api/policies/priority')
def get_priority():
    """Return policies sorted by composite priority score."""
    n = request.args.get('n', 10, type=int)
    policies = get_all_policies()

    # Only include those with priority_score
    scored = [p for p in policies if isinstance(p.get('priority_score'), (int, float))]
    scored.sort(key=lambda x: x['priority_score'], reverse=True)

    return jsonify({
        'policies': scored[:n],
        'total_scored': len(scored),
        'total_policies': len(policies),
    })

# ─── Groups (for quick-find on Insights) ────────────────────────────
@app.route('/api/policies/groups/<field>')
def get_groups(field):
    allowed = ['target_group', 'legal_basis', 'approved_by', 'domain']
    if field not in allowed:
        return jsonify({'error': f'Field must be one of: {allowed}'}), 400

    policies = get_all_policies()
    groups = {}
    for m in policies:
        val = m.get(field, 'Onbekend')
        if val not in groups:
            groups[val] = []
        groups[val].append({
            'title': m.get('title', 'Onbekend'),
            'url': m.get('url', ''),
            'summary': m.get('summary', ''),
            'domain': m.get('domain', ''),
        })
    sorted_groups = dict(sorted(groups.items(), key=lambda x: -len(x[1])))
    return jsonify({
        'field': field,
        'groups': {k: {'count': len(v), 'policies': v[:5]} for k, v in sorted_groups.items()},
    })

# ─── Top policies by impact ─────────────────────────────────────────
@app.route('/api/policies/top')
def get_top():
    n = request.args.get('n', 10, type=int)
    policies = get_all_policies()
    policies.sort(
        key=lambda x: x.get('impact_score', 0) if isinstance(x.get('impact_score', 0), (int, float)) else 0,
        reverse=True
    )
    return jsonify({'policies': policies[:n]})

# ─── Health check ───────────────────────────────────────────────────
@app.route('/api/health')
def health():
    """Health check for the frontend integrity page."""
    try:
        policies = get_all_policies()
        v2 = [p for p in policies if p.get('priority_score') is not None]
        return jsonify({
            'status': 'ok',
            'database': DB_PATH,
            'total_policies': len(policies),
            'v2_policies': len(v2),
            'db_exists': os.path.exists(DB_PATH),
        })
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

# ─── Legacy analyze endpoint ────────────────────────────────────────
@app.route('/api/analyze', methods=['POST'])
def analyze():
    data = request.json
    url = data.get('url')
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        import requests as req
        from bs4 import BeautifulSoup
        logging.info(f"Analyzing URL: {url}")
        res = req.get(url, headers={"User-Agent": "Mozilla/5.0"})
        if res.status_code != 200:
            return jsonify({"error": f"Could not fetch URL. Status: {res.status_code}"}), 400
        soup = BeautifulSoup(res.text, 'html.parser')
        for script in soup(["script", "style"]):
            script.extract()
        text_area = soup.find('div', id='content') or soup.find('body')
        text = " ".join([p.get_text(strip=True) for p in text_area.find_all(['p', 'li']) if len(p.get_text()) > 20]) if text_area else ""
        if len(text) < 50:
            return jsonify({"error": "Document could not be parsed or is too short."}), 400
        return jsonify({"summary": text[:500] + "...", "length": len(text)})
    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


# ─── Root Endpoint ──────────────────────────────────────────────────
@app.route('/')
def index():
    return """
    <html><head><title>Capelle Leefkompas API</title>
    <style>body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;max-width:800px;margin:40px auto;line-height:1.6;color:#333}h1{color:#154273}code{background:#f1f5f9;padding:2px 6px;border-radius:4px;color:#e11d48}.ep{margin-bottom:16px;padding:12px 16px;border:1px solid #e2e8f0;border-radius:8px}</style>
    </head><body>
    <h1>🧭 Capelle Leefkompas API</h1>
    <p>Flask backend serving the Leefkompas frontend.</p>
    <div class="ep"><strong>GET <code><a href="/api/policies">/api/policies</a></code></strong><br>Paginated, filterable policies. Params: <code>page, per_page, domain, qol, q, sort</code></div>
    <div class="ep"><strong>GET <code><a href="/api/policies/stats">/api/policies/stats</a></code></strong><br>Aggregated stats + QoL dimensions for Insights page.</div>
    <div class="ep"><strong>GET <code><a href="/api/policies/qol">/api/policies/qol</a></code></strong><br>Policies per quality-of-life dimension.</div>
    <div class="ep"><strong>GET <code><a href="/api/policies/priority">/api/policies/priority</a></code></strong><br>Policies ranked by priority score.</div>
    <div class="ep"><strong>GET <code><a href="/api/policies/groups/domain">/api/policies/groups/{field}</a></code></strong><br>Policies grouped by field.</div>
    <div class="ep"><strong>GET <code><a href="/api/policies/top">/api/policies/top</a></code></strong><br>Top N policies by impact score.</div>
    <div class="ep"><strong>GET <code><a href="/api/health">/api/health</a></code></strong><br>Health check with DB status.</div>
    <p><em>Open the Vite frontend at <a href="http://localhost:5173">http://localhost:5173</a>.</em></p>
    </body></html>
    """

if __name__ == '__main__':
    logging.info(f"Database: {DB_PATH}")
    logging.info("Starting Capelle Leefkompas API on port 5000...")
    app.run(port=5000, debug=False)
