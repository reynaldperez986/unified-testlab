"""
ingestion/embedder.py — Ollama embedding generator.

Mirrors the BA Agent embedder.py role
(Azure OpenAI text-embedding-ada-002 → Ollama nomic-embed-text).

Responsible ONLY for calling the Ollama embedding API.
DB persistence is handled downstream by indexer.py.

Pipeline position:  extractor → chunker → embedder → indexer
"""
from __future__ import annotations

import requests

DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_OLLAMA_API = "http://localhost:11434/api"


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------

def _ollama_embed_url(api_base: str) -> str:
    base = (api_base or DEFAULT_OLLAMA_API).rstrip("/")
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/api"):
        return base + "/embeddings"
    return base + "/api/embeddings"


def _ollama_embed_batch_url(api_base: str) -> str:
    base = (api_base or DEFAULT_OLLAMA_API).rstrip("/")
    if base.endswith("/embed"):
        return base
    if base.endswith("/api"):
        return base + "/embed"
    return base + "/api/embed"


def _ollama_generate_url(api_base: str) -> str:
    base = (api_base or DEFAULT_OLLAMA_API).rstrip("/")
    if base.endswith("/generate"):
        return base
    if base.endswith("/api"):
        return base + "/generate"
    return base + "/api/generate"


def _vector_literal(values: list[float]) -> str:
    """Format a float list as a pgvector literal ``[x,y,…]``."""
    return "[" + ",".join(format(float(v), ".10f") for v in values) + "]"


# ---------------------------------------------------------------------------
# Step 1: Embed Query / batch embed
# ---------------------------------------------------------------------------

def embed_texts(
    texts: list[str],
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 120,
) -> list[list[float]]:
    """Embed a list of text strings using Ollama.

    Tries the batch ``/embed`` endpoint first; falls back to individual
    ``/embeddings`` calls if the model does not support batching.

    Raises ``RuntimeError`` when the model is not installed.
    """
    if not texts:
        return []

    batch_url = _ollama_embed_batch_url(ollama_api)
    response = requests.post(
        batch_url,
        json={"model": embedding_model, "input": texts},
        timeout=timeout,
    )
    if response.status_code == 404 and f'model "{embedding_model}" not found' in (response.text or ""):
        raise RuntimeError(
            f'Ollama embedding model "{embedding_model}" is not installed. '
            f"Run: ollama pull {embedding_model}"
        )
    if response.ok:
        payload = response.json()
        embeddings = payload.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return [[float(v) for v in item] for item in embeddings]

    # Fallback: one-by-one
    single_url = _ollama_embed_url(ollama_api)
    vectors: list[list[float]] = []
    for text in texts:
        single = requests.post(
            single_url,
            json={"model": embedding_model, "prompt": text},
            timeout=timeout,
        )
        if single.status_code == 404 and f'model "{embedding_model}" not found' in (single.text or ""):
            raise RuntimeError(
                f'Ollama embedding model "{embedding_model}" is not installed. '
                f"Run: ollama pull {embedding_model}"
            )
        single.raise_for_status()
        vector = single.json().get("embedding")
        if not isinstance(vector, list):
            raise ValueError("Ollama embeddings response did not include an embedding vector.")
        vectors.append([float(v) for v in vector])
    return vectors
