"""
retrieval/retriever.py — hybrid pgvector + full-text search retrieval.

BA Agent Step 2: Vector Search.

The retrieve_context() function is the single public entry point.  It:
  1. Embeds the query string (Step 1 → embed_texts).
  2. Runs a hybrid query against ai_rag_document combining:
       • cosine similarity via pgvector `<=>` (vector score)
       • ts_rank_cd full-text match via to_tsvector / plainto_tsquery (keyword score)
     Blended weight: 62 % vector + 38 % keyword.
  3. Returns a structured context dict consumed by agent/agent.py → build_prompt().

The function also calls _ensure_rag_ready() to trigger a background sync when
the index is stale, ensuring callers always get the freshest available data
without blocking on the embedding step.
"""
from __future__ import annotations

import json
from typing import Any

from django.db import connection

from llm_workflow_assistant.ingestion.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OLLAMA_API,
    _vector_literal,
    embed_texts,
)
from llm_workflow_assistant.ingestion.pipeline import _ensure_rag_ready

HYBRID_VECTOR_WEIGHT = 0.62
HYBRID_KEYWORD_WEIGHT = 0.38


def _normalize_metadata(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except Exception:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def retrieve_context(
    query: str,
    tenant_id: str | None = None,
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 120,
    top_k: int = 12,
) -> dict[str, Any]:
    """Hybrid keyword + vector retrieval against ai_rag_document.

    Returns:
        {
            "steps": [...],       # step metadata dicts, sorted by step_no
            "workflows": [...],   # workflow metadata dicts
            "databank": [...],    # databank metadata dicts
            "documents": [...],   # full ranked document list
            "sync": {...},        # pipeline sync state
            "counts": {...},
        }
    """
    sync = _ensure_rag_ready(
        ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout
    )

    # Step 1 — Embed query
    query_vector = _vector_literal(
        embed_texts(
            [query],
            ollama_api=ollama_api,
            embedding_model=embedding_model,
            timeout=timeout,
        )[0]
    )

    # Step 2 — Hybrid SQL query
    with connection.cursor() as cur:
        cur.execute(
            """
            WITH ranked_docs AS (
                SELECT
                    source_type,
                    source_key,
                    source_title,
                    document_text,
                    metadata,
                    source_updated_at,
                    tenant_id,
                    ts_rank_cd(
                        to_tsvector('simple',
                            COALESCE(source_title, '') || ' ' || COALESCE(document_text, '')
                        ),
                        plainto_tsquery('simple', %s)
                    ) AS keyword_score,
                    CASE
                        WHEN embedding IS NOT NULL
                            THEN 1 - (embedding <=> %s::vector)
                        ELSE 0
                    END AS vector_score
                FROM ai_rag_document
                WHERE embedding_model = %s
                  AND (%s IS NULL OR tenant_id = %s OR tenant_id IS NULL)
            )
            SELECT
                source_type,
                source_key,
                source_title,
                document_text,
                metadata,
                source_updated_at,
                keyword_score,
                vector_score,
                ((vector_score * %s) + (keyword_score * %s)) AS hybrid_score
            FROM ranked_docs
            WHERE keyword_score > 0 OR vector_score > 0
            ORDER BY hybrid_score DESC, source_updated_at DESC NULLS LAST, source_key ASC
            LIMIT %s
            """,
            [
                query,
                query_vector,
                embedding_model,
                tenant_id, tenant_id,
                HYBRID_VECTOR_WEIGHT,
                HYBRID_KEYWORD_WEIGHT,
                top_k,
            ],
        )
        rows = cur.fetchall()

    # Step 3 — Assemble context dict
    documents: list[dict[str, Any]] = []
    context: dict[str, Any] = {
        "steps": [],
        "workflows": [],
        "databank": [],
        "documents": documents,
        "sync": sync,
        "counts": {"steps": 0, "workflows": 0, "databank": 0, "documents": 0},
    }

    for row in rows:
        metadata = _normalize_metadata(row[4])
        doc = {
            "source_type": row[0],
            "source_key": row[1],
            "title": row[2],
            "content": row[3],
            "metadata": metadata,
            "updated_at": row[5].isoformat() if row[5] else None,
            "keyword_score": float(row[6] or 0),
            "vector_score": float(row[7] or 0),
            "hybrid_score": float(row[8] or 0),
        }
        documents.append(doc)
        if row[0] == "steps":
            context["steps"].append(metadata)
        elif row[0] == "ai_workflow":
            context["workflows"].append(metadata)
        elif row[0] == "ai_databank":
            context["databank"].append(metadata)

    # Steps must be in execution order for the LLM
    context["steps"].sort(key=lambda s: int(s.get("step_no") or 0))

    context["counts"] = {
        "steps": len(context["steps"]),
        "workflows": len(context["workflows"]),
        "databank": len(context["databank"]),
        "documents": len(documents),
    }
    return context
