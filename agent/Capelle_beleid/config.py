"""Configuration for Capelle beleid (policy document) tool."""

from pathlib import Path
import os
import re

# ── Paths ────────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_ROOT = Path(os.environ.get("CAPELLE_POLICY_PDF_ROOT", str(REPO_ROOT / "data" / "policy-pdfs")))
UNIFIED_DB = REPO_ROOT / "capelle_rag" / "chroma_db"
COLLECTION = "policy_documents"

# ── Chunk settings ───────────────────────────────────────────────────────────
CHUNK_SIZE = 1500       # characters per chunk
CHUNK_OVERLAP = 200     # overlap between chunks
EMBED_BATCH_SIZE = 100  # OpenAI batch size
BASE_URL = "https://capelleaandenijssel.begrotingsapp.nl"

# ── Document type mapping ────────────────────────────────────────────────────
# Maps folder name prefixes to canonical doc types
DOC_TYPE_MAP = {
    "Begroting":            "begroting",
    "Voorjaarsnota":        "voorjaarsnota",
    "Najaarsnota":          "najaarsnota",
    "Jaarstukken":          "jaarstukken",
    "Slotwijziging":        "slotwijziging",
    "Bestuursrapportage":   "bestuursrapportage",
    "Kadernota":            "kadernota",
}

# URL path templates per doc type
URL_PATH_MAP = {
    "begroting":          "/begroting-{year}",
    "voorjaarsnota":      "/voorjaarsnota-{year}",
    "najaarsnota":        "/najaarsnota-{year}",
    "jaarstukken":        "/jaarstukken-{year}",
    "slotwijziging":      "/slotwijziging-{year}",
    "bestuursrapportage": "/bestuursrapportage-{year}",
    "kadernota":          "/kadernota-{year}",
}

# Regex to parse folder names like "Begroting_2024" or "Bestuursrapportage_2025_1"
FOLDER_PATTERN = re.compile(r"^(.+?)_(\d{4})(?:_(\d+))?$")


def discover_pdf_folders() -> list[dict]:
    """Auto-discover all PDF folders and return metadata for each."""
    if not PDF_ROOT.exists():
        return []

    folders = []
    for d in sorted(PDF_ROOT.iterdir()):
        if not d.is_dir():
            continue
        m = FOLDER_PATTERN.match(d.name)
        if not m:
            continue

        prefix, year_str, sub_idx = m.group(1), m.group(2), m.group(3)
        year = int(year_str)

        # Match prefix to doc type
        doc_type = None
        for key, dtype in DOC_TYPE_MAP.items():
            if prefix == key:
                doc_type = dtype
                break
        if doc_type is None:
            doc_type = prefix.lower()

        folders.append({
            "path": d,
            "folder_name": d.name,
            "doc_type": doc_type,
            "year": year,
            "sub_index": int(sub_idx) if sub_idx else None,
        })

    return folders
