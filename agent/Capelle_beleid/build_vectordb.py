"""
Build the policy_documents ChromaDB collection from all scraped PDFs.

Indexes ALL document types (begrotingen, voorjaarsnota, najaarsnota,
jaarstukken, slotwijzigingen, bestuursrapportages, kadernota) into a
single collection with metadata-based filtering.

Embedding: OpenAI text-embedding-3-small (shared with CBS tool).
Chunking: 1500 chars, 200 overlap, recursive split on natural boundaries.
Target: capelle_rag/chroma_db/ (unified ChromaDB, same as CBS + budget).
"""

import re
import sys
import argparse
from pathlib import Path

import fitz  # PyMuPDF
import chromadb

from config import (
    PDF_ROOT, UNIFIED_DB, COLLECTION, CHUNK_SIZE, CHUNK_OVERLAP,
    EMBED_BATCH_SIZE, BASE_URL, URL_PATH_MAP, discover_pdf_folders,
)
from url_resolver import resolve_source_url

# Add repo root to path so we can import cbs_tool
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from cbs_tool.rag.embed import embed_batch


# ── PDF text extraction ──────────────────────────────────────────────────────

def extract_pages(pdf_path: Path) -> list[tuple[int, str]]:
    """Return list of (page_number, text) tuples from a PDF."""
    pages = []
    doc = fitz.open(str(pdf_path))
    for i, page in enumerate(doc):
        text = page.get_text("text")
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if text:
            pages.append((i + 1, text))
    doc.close()
    return pages


# ── Chunking ─────────────────────────────────────────────────────────────────

def recursive_split(text: str, size: int, overlap: int) -> list[str]:
    """Split text recursively on natural boundaries."""
    separators = ["\n\n", "\n", ". ", " "]

    def split_on(text, sep, size, overlap):
        parts = text.split(sep)
        chunks, current = [], ""
        for part in parts:
            candidate = (current + sep + part).lstrip(sep) if current else part
            if len(candidate) <= size:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                tail = current[-overlap:] if len(current) > overlap else current
                current = (tail + sep + part).lstrip(sep) if tail else part
        if current:
            chunks.append(current)
        return chunks

    chunks = [text]
    for sep in separators:
        new_chunks = []
        for chunk in chunks:
            if len(chunk) <= size:
                new_chunks.append(chunk)
            else:
                new_chunks.extend(split_on(chunk, sep, size, overlap))
        chunks = new_chunks

    final = []
    for chunk in chunks:
        if len(chunk) <= size:
            final.append(chunk)
        else:
            for start in range(0, len(chunk), size - overlap):
                final.append(chunk[start:start + size])

    return [c.strip() for c in final if c.strip()]


# ── Metadata inference ───────────────────────────────────────────────────────

def infer_slug_metadata(slug: str) -> dict:
    """Derive document_type and program_number from the PDF filename slug."""
    m = re.match(r"programma-(\d+)-", slug)
    if m:
        return {"document_type": "programma", "program_number": int(m.group(1))}
    if slug.startswith("paragraaf-"):
        return {"document_type": "paragraaf", "program_number": -1}
    if slug in ("aanbiedingsbrief", "leeswijzer", "raadsbesluit",
                "amendementen-en-moties", "amendementen-en-moties-"):
        return {"document_type": "bestuur", "program_number": -1}
    if "hoofdlijnen" in slug:
        return {"document_type": "hoofdlijnen", "program_number": -1}
    if slug in ("financile-begroting", "financiele-begroting"):
        return {"document_type": "financieel", "program_number": -1}
    return {"document_type": "bijlage", "program_number": -1}


# ── Build chunks for one PDF ────────────────────────────────────────────────

def build_chunks_for_pdf(pdf_path: Path, doc_type: str, year: int,
                         folder_name: str, sub_index: int | None) -> list[dict]:
    """Extract text from PDF and return chunk dicts with metadata."""
    slug = pdf_path.stem
    slug_meta = infer_slug_metadata(slug)

    # Build source_url by looking the slug up in the begrotingsapp URL
    # catalog instead of templating a guess. begrotingsapp serves every
    # section type (programma / paragraaf / bestuur / bestanden) under
    # a single ``/programma/`` path with a ``paragraaf-`` slug prefix
    # where applicable — heuristic templating produced 60%+ 404s.
    # See ``url_resolver.py`` for the lookup logic and refresh workflow.
    source_url = resolve_source_url(doc_type, year, sub_index, slug)

    meta_base = {
        "doc_type": doc_type,
        "year": year,
        "document": slug,
        "source_folder": folder_name,
        "source_url": source_url,
        "file_path": str(pdf_path),
        **slug_meta,
    }
    if sub_index is not None:
        meta_base["sub_index"] = sub_index

    pages = extract_pages(pdf_path)
    if not pages:
        return []

    # Concatenate full text, tracking page boundaries
    full_text = ""
    page_map = []
    for page_num, text in pages:
        page_map.append((len(full_text), page_num))
        full_text += text + "\n\n"

    raw_chunks = recursive_split(full_text, CHUNK_SIZE, CHUNK_OVERLAP)

    def char_to_page(pos: int) -> int:
        page = page_map[0][1]
        for start, pnum in page_map:
            if pos >= start:
                page = pnum
            else:
                break
        return page

    chunks = []
    char_pos = 0
    for i, text in enumerate(raw_chunks):
        idx = full_text.find(text[:50], char_pos)
        pos = idx if idx != -1 else char_pos
        char_pos = pos + len(text)

        chunks.append({
            "text": text,
            "metadata": {
                **meta_base,
                "page_number": char_to_page(pos),
                "chunk_index": i,
                "total_chunks": len(raw_chunks),
            },
        })
    return chunks


# ── Main ─────────────────────────────────────────────────────────────────────

def build_index(reset: bool = False, verbose: bool = False):
    folders = discover_pdf_folders()
    if not folders:
        print(f"[ERROR] No PDF folders found at {PDF_ROOT}", file=sys.stderr)
        sys.exit(1)

    print(f"PDF root: {PDF_ROOT}")
    print(f"Found {len(folders)} document folders")
    print(f"Target: {UNIFIED_DB} / collection '{COLLECTION}'")
    print()

    # Open unified ChromaDB (same DB as CBS + budget)
    UNIFIED_DB.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(UNIFIED_DB))

    if reset:
        try:
            client.delete_collection(COLLECTION)
            print("Dropped existing collection.")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION,
        metadata={"hnsw:space": "cosine", "description": "Capelle policy documents 2019-2026"},
    )

    # Gather all PDFs
    all_pdfs = []
    for folder in folders:
        pdfs = sorted(folder["path"].glob("*.pdf"))
        for p in pdfs:
            all_pdfs.append((p, folder))
        if verbose:
            print(f"  {folder['folder_name']}: {len(pdfs)} PDFs ({folder['doc_type']} {folder['year']})")

    print(f"\nTotal: {len(all_pdfs)} PDFs across {len(folders)} folders\n")

    # Process PDFs and embed in batches
    total_chunks = 0
    batch_ids, batch_texts, batch_metas = [], [], []

    def flush_batch():
        nonlocal total_chunks
        if not batch_texts:
            return
        embeddings = embed_batch(batch_texts, verbose=verbose)
        collection.upsert(
            ids=batch_ids[:],
            embeddings=embeddings,
            documents=batch_texts[:],
            metadatas=batch_metas[:],
        )
        total_chunks += len(batch_texts)
        batch_ids.clear()
        batch_texts.clear()
        batch_metas.clear()

    for pdf_idx, (pdf_path, folder) in enumerate(all_pdfs, 1):
        chunks = build_chunks_for_pdf(
            pdf_path,
            doc_type=folder["doc_type"],
            year=folder["year"],
            folder_name=folder["folder_name"],
            sub_index=folder.get("sub_index"),
        )
        rel = pdf_path.relative_to(PDF_ROOT)
        print(f"[{pdf_idx}/{len(all_pdfs)}] {rel}  ->  {len(chunks)} chunks")

        for chunk in chunks:
            sub = f"_{folder['sub_index']}" if folder.get('sub_index') else ""
            uid = f"{folder['doc_type']}__{folder['year']}{sub}__{chunk['metadata']['document']}__c{chunk['metadata']['chunk_index']}"
            batch_ids.append(uid)
            batch_texts.append(chunk["text"])
            batch_metas.append(chunk["metadata"])

            if len(batch_texts) >= EMBED_BATCH_SIZE:
                flush_batch()

    flush_batch()

    print(f"\nDone! {total_chunks} chunks indexed into '{COLLECTION}'.")
    print(f"DB: {UNIFIED_DB}")

    # Verification
    print(f"\nVerification:")
    for col_name in ["cbs_tables", "budget_data", COLLECTION]:
        try:
            c = client.get_collection(col_name)
            print(f"  {col_name:<25} -> {c.count()} documents")
        except Exception:
            print(f"  {col_name:<25} -> not present")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build policy_documents collection")
    parser.add_argument("--reset", action="store_true", help="Drop and rebuild collection")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    build_index(reset=args.reset, verbose=args.verbose)
