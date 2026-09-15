"""Minimal Flask API serving agent analysis JSON files."""
import os
import json
import logging
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')

ANALYSES_DIR = os.path.join(
    os.path.dirname(__file__), '..', 'capelle_rag', 'analyses'
)


def _load_analysis_files():
    if not os.path.isdir(ANALYSES_DIR):
        return []
    files = []
    for f in sorted(os.listdir(ANALYSES_DIR), reverse=True):
        if not f.endswith('.json'):
            continue
        try:
            path = os.path.join(ANALYSES_DIR, f)
            with open(path, encoding='utf-8') as fh:
                data = json.load(fh)
                data['_filename'] = f
                files.append(data)
        except Exception as e:
            logging.warning(f"Failed to load {f}: {e}")
    return files


@app.route('/api/analyses')
def list_analyses():
    files = _load_analysis_files()
    analyses = [f for f in files if 'sections' in f]
    return jsonify({'analyses': analyses, 'total': len(analyses)})


@app.route('/api/analyses/<analysis_id>')
def get_analysis(analysis_id):
    files = _load_analysis_files()
    for f in files:
        if f.get('id') == analysis_id:
            return jsonify(f)
    return jsonify({'error': 'Analysis not found'}), 404


@app.route('/api/tool-outputs')
def list_tool_outputs():
    files = _load_analysis_files()
    outputs = [f for f in files if 'tool' in f and 'sections' not in f]
    return jsonify({'outputs': outputs, 'total': len(outputs)})


@app.route('/')
def index():
    return '<p>Capelle Agent API. <a href="/api/analyses">/api/analyses</a></p>'


if __name__ == '__main__':
    logging.info(f"Analyses dir: {os.path.abspath(ANALYSES_DIR)}")
    logging.info("Starting API on port 5000...")
    app.run(port=5000, debug=False)
