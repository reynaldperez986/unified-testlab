"""
ingestion/chunker.py — Token-aware document chunker.

Mirrors the BA Agent chunker.py role (Azure chunker → Ollama/pgvector stack).

Splits long document text into overlapping chunks so each chunk fits
comfortably within the embedding model's context window.  Word count is
used as a token proxy — no external tokenizer dependency required.

Pipeline position:  extractor → chunker → embedder → indexer
"""
from __future__ import annotations

import hashlib
import json

DEFAULT_CHUNK_TOKENS: int = 400   # ≈ words per chunk
DEFAULT_CHUNK_OVERLAP: int = 80   # ≈ words of overlap between adjacent chunks


# ---------------------------------------------------------------------------
# Text-level splitting
# ---------------------------------------------------------------------------

def chunk_text(
    text: str,
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping chunks by word count.

    Documents shorter than *max_tokens* words are returned as a single-element
    list so callers can always iterate without a length check.
    """
    words = text.split()
    if not words:
        return []
    if len(words) <= max_tokens:
        return [text]

    stride = max(1, max_tokens - overlap)
    chunks: list[str] = []
    start = 0
    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start += stride
    return chunks


# ---------------------------------------------------------------------------
# Document-level splitting
# ---------------------------------------------------------------------------

def chunk_document(
    doc: dict,
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """Split a RAG document dict into chunk dicts.

    Each chunk inherits the parent's metadata and appends chunk-specific keys:
      • ``chunk_index`` / ``chunk_total``
      • ``parent_source_key`` — original un-chunked source_key

    Documents that fit in a single chunk are returned unchanged (list of 1),
    so the caller never needs to special-case short documents.
    """
    content: str = doc.get("content") or ""
    raw_chunks = chunk_text(content, max_tokens=max_tokens, overlap=overlap)

    if len(raw_chunks) <= 1:
        return [doc]

    parent_key: str = doc["source_key"]
    result: list[dict] = []
    total = len(raw_chunks)

    for i, chunk_content in enumerate(raw_chunks):
        chunk_doc = dict(doc)
        chunk_doc["content"] = chunk_content
        chunk_doc["source_key"] = f"{parent_key}#chunk-{i}"
        chunk_doc["title"] = f"{doc.get('title', '')} (part {i + 1}/{total})"

        chunk_meta: dict = dict(doc.get("metadata") or {})
        chunk_meta["chunk_index"] = i
        chunk_meta["chunk_total"] = total
        chunk_meta["parent_source_key"] = parent_key
        chunk_doc["metadata"] = chunk_meta

        payload = {
            "title": chunk_doc["title"],
            "content": chunk_content,
            "metadata": chunk_meta,
        }
        chunk_doc["content_hash"] = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()
        result.append(chunk_doc)

    return result


def chunk_documents(
    documents: list[dict],
    *,
    max_tokens: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[dict]:
    """Chunk every document in *documents* and return the combined list."""
    result: list[dict] = []
    for doc in documents:
        result.extend(chunk_document(doc, max_tokens=max_tokens, overlap=overlap))
    return result
