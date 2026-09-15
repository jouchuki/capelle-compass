"""
Embedding backend.

Default:  OpenAI text-embedding-3-small  (requires OPENAI_API_KEY)
Local:    any Ollama model via OpenAI-compatible endpoint

Override with env vars:
  CBS_EMBED_BASE_URL   e.g. http://localhost:11434/v1
  CBS_EMBED_MODEL      e.g. mxbai-embed-large  or  nomic-embed-text
  CBS_EMBED_API_KEY    any string when using Ollama (e.g. "ollama")

IMPORTANT: switching embedding models requires a full index rebuild
           (cbs rag index --reset), because vector spaces are incompatible.
"""
import os
import time
from typing import List

from pathlib import Path

from openai import OpenAI

def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            if k.strip() not in os.environ:
                os.environ[k.strip()] = v.strip().strip('"').strip("'")

# Look for .env in sidecar dir first, then repo root (dev/editable install)
_load_env(Path.home() / ".local" / "share" / "cbs-tool" / ".env")
_load_env(Path(__file__).parent.parent.parent / ".env")

DEFAULT_MODEL = "text-embedding-3-small"
BATCH_SIZE    = 100

_client: OpenAI | None = None
_client_base_url: str | None = None  # track which base_url the cached client was built with


def _get_embed_config() -> tuple[str, str, str | None]:
    """Return (api_key, model, base_url). Reads env vars fresh every call."""
    base_url = os.environ.get("CBS_EMBED_BASE_URL")
    model    = os.environ.get("CBS_EMBED_MODEL", DEFAULT_MODEL)
    api_key  = os.environ.get("CBS_EMBED_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise EnvironmentError(
            "No API key set for embeddings. "
            "Set OPENAI_API_KEY for OpenAI, or CBS_EMBED_API_KEY for local Ollama."
        )
    return api_key, model, base_url


def _get_client() -> OpenAI:
    global _client, _client_base_url
    api_key, _, base_url = _get_embed_config()
    # Rebuild client if base_url changed (e.g. switching between OpenAI and Ollama)
    if _client is None or _client_base_url != base_url:
        kwargs: dict = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client          = OpenAI(**kwargs)
        _client_base_url = base_url
    return _client


def embed_one(text: str) -> List[float]:
    _, model, _ = _get_embed_config()
    client = _get_client()
    response = client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def embed_batch(texts: List[str], verbose: bool = False) -> List[List[float]]:
    """Embed a list of texts in batches. Returns list of embedding vectors."""
    _, model, _ = _get_embed_config()
    client = _get_client()
    all_embeddings = []

    for i in range(0, len(texts), BATCH_SIZE):
        batch = texts[i:i + BATCH_SIZE]
        if verbose:
            print(f"  Embedding batch {i // BATCH_SIZE + 1}/{-(-len(texts) // BATCH_SIZE)}  [{model}]", flush=True)
        response = client.embeddings.create(input=batch, model=model)
        # Response preserves input order
        all_embeddings.extend([d.embedding for d in response.data])
        time.sleep(0.1)  # gentle rate limiting

    return all_embeddings
