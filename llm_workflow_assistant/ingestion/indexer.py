"""
ingestion/indexer.py — pgvector index management.

Mirrors the BA Agent indexer.py role
(Azure AI Search → PostgreSQL pgvector + full-text search).

Responsible for:
  • Schema creation / migration (CREATE TABLE IF NOT EXISTS, ADD COLUMN IF NOT EXISTS)
  • Document upsert / replacement with content-hash-based change detection
  • Embedding writes (calls embedder to generate vectors, persists to DB)
  • Sync-state bookkeeping (ai_rag_sync_state table)
  • Document counts for progress reporting

Pipeline position:  extractor → chunker → embedder → indexer
"""
from __future__ import annotations

import json
import time
from typing import Any

from django.db import connection

from llm_workflow_assistant.ingestion.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    _vector_literal,
    embed_texts,
)

RAG_DOCUMENT_TABLE = "ai_rag_document"
RAG_STAGING_TABLE = "ai_rag_document_staging"
RAG_SYNC_INTERVAL_SECONDS = 300
DEFAULT_SYNC_KEY = "default"

_RAG_SCHEMA_ENSURED = False


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _ensure_rag_document_table(cur: Any, table_name: str) -> None:
    cur.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table_name} (
            id BIGSERIAL PRIMARY KEY,
            source_type TEXT NOT NULL,
            source_key TEXT NOT NULL UNIQUE,
            source_title TEXT NOT NULL DEFAULT '',
            document_text TEXT NOT NULL DEFAULT '',
            metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
            source_updated_at TIMESTAMPTZ NULL,
            tenant_id UUID NULL,
            content_hash TEXT NOT NULL DEFAULT '',
            embedding_model TEXT NULL,
            embedding vector NULL,
            needs_embedding BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )
    # Idempotent column additions for older schema versions
    for ddl in (
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'unknown'",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_key TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_title TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS document_text TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ NULL",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id UUID NULL",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding_model TEXT NULL",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding vector NULL",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS needs_embedding BOOLEAN NOT NULL DEFAULT TRUE",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        f"CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_source_key_uniq ON {table_name} (source_key)",
        f"CREATE INDEX IF NOT EXISTS {table_name}_source_type_idx ON {table_name} (source_type)",
        f"CREATE INDEX IF NOT EXISTS {table_name}_tenant_idx ON {table_name} (tenant_id)",
        f"CREATE INDEX IF NOT EXISTS {table_name}_updated_at_idx ON {table_name} (updated_at DESC)",
        f"CREATE INDEX IF NOT EXISTS {table_name}_embedding_state_idx ON {table_name} (needs_embedding, embedding_model)",
        f"""
        CREATE INDEX IF NOT EXISTS {table_name}_fts_idx
        ON {table_name}
        USING GIN (to_tsvector('simple', COALESCE(source_title, '') || ' ' || COALESCE(document_text, '')))
        """,
    ):
        cur.execute(ddl)


def ensure_schema() -> None:
    """Create all RAG tables and indexes (idempotent, called once at startup)."""
    global _RAG_SCHEMA_ENSURED
    if _RAG_SCHEMA_ENSURED:
        return
    with connection.cursor() as cur:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        _ensure_rag_document_table(cur, RAG_DOCUMENT_TABLE)
        _ensure_rag_document_table(cur, RAG_STAGING_TABLE)
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ai_rag_sync_state (
                sync_key TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'idle',
                last_started_at TIMESTAMPTZ NULL,
                last_finished_at TIMESTAMPTZ NULL,
                last_error TEXT NULL,
                indexed_documents BIGINT NOT NULL DEFAULT 0,
                pending_documents BIGINT NOT NULL DEFAULT 0,
                target_documents BIGINT NOT NULL DEFAULT 0,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        for ddl in (
            "ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS indexed_documents BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS pending_documents BIGINT NOT NULL DEFAULT 0",
            "ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS target_documents BIGINT NOT NULL DEFAULT 0",
        ):
            cur.execute(ddl)
    _RAG_SCHEMA_ENSURED = True


# ---------------------------------------------------------------------------
# Sync-state bookkeeping
# ---------------------------------------------------------------------------

def _serialize_timestamp(value: Any) -> str | None:
    return value.isoformat() if value else None


def get_sync_state(sync_key: str = DEFAULT_SYNC_KEY) -> dict[str, Any]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT sync_key, status, last_started_at, last_finished_at,
                   last_error, indexed_documents, pending_documents, target_documents
            FROM ai_rag_sync_state WHERE sync_key = %s
            """,
            [sync_key],
        )
        row = cur.fetchone()
    if not row:
        return {
            "sync_key": sync_key, "status": "idle",
            "last_started_at": None, "last_finished_at": None, "last_error": None,
            "indexed_documents": 0, "pending_documents": 0, "target_documents": 0,
        }
    return {
        "sync_key": row[0], "status": row[1] or "idle",
        "last_started_at": row[2], "last_finished_at": row[3], "last_error": row[4],
        "indexed_documents": int(row[5] or 0),
        "pending_documents": int(row[6] or 0),
        "target_documents": int(row[7] or 0),
    }


def serialize_sync_state(state: dict[str, Any]) -> dict[str, Any]:
    return {
        "sync_key": state.get("sync_key") or DEFAULT_SYNC_KEY,
        "status": state.get("status") or "idle",
        "last_started_at": _serialize_timestamp(state.get("last_started_at")),
        "last_finished_at": _serialize_timestamp(state.get("last_finished_at")),
        "last_error": state.get("last_error"),
        "indexed_documents": int(state.get("indexed_documents") or 0),
        "pending_documents": int(state.get("pending_documents") or 0),
        "target_documents": int(state.get("target_documents") or 0),
    }


def set_sync_state(
    status: str,
    *,
    last_error: str | None = None,
    started: bool = False,
    finished: bool = False,
    indexed_documents: int | None = None,
    pending_documents: int | None = None,
    target_documents: int | None = None,
    sync_key: str = DEFAULT_SYNC_KEY,
) -> None:
    with connection.cursor() as cur:
        cur.execute(
            """
            INSERT INTO ai_rag_sync_state (
                sync_key, status,
                last_started_at, last_finished_at, last_error,
                indexed_documents, pending_documents, target_documents, updated_at
            )
            VALUES (
                %s, %s,
                CASE WHEN %s THEN NOW() ELSE NULL END,
                CASE WHEN %s THEN NOW() ELSE NULL END,
                %s,
                COALESCE(%s, 0), COALESCE(%s, 0), COALESCE(%s, 0),
                NOW()
            )
            ON CONFLICT (sync_key) DO UPDATE SET
                status = EXCLUDED.status,
                last_started_at = CASE WHEN %s THEN NOW() ELSE ai_rag_sync_state.last_started_at END,
                last_finished_at = CASE WHEN %s THEN NOW() ELSE ai_rag_sync_state.last_finished_at END,
                last_error = %s,
                indexed_documents = COALESCE(%s, ai_rag_sync_state.indexed_documents),
                pending_documents = COALESCE(%s, ai_rag_sync_state.pending_documents),
                target_documents  = COALESCE(%s, ai_rag_sync_state.target_documents),
                updated_at = NOW()
            """,
            [
                sync_key, status,
                started, finished, last_error,
                indexed_documents, pending_documents, target_documents,
                started, finished, last_error,
                indexed_documents, pending_documents, target_documents,
            ],
        )


# ---------------------------------------------------------------------------
# Document counts
# ---------------------------------------------------------------------------

def count_indexed(embedding_model: str, *, table_name: str = RAG_DOCUMENT_TABLE) -> int:
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE embedding IS NOT NULL AND embedding_model = %s",
            [embedding_model],
        )
        return int((cur.fetchone() or [0])[0] or 0)


def count_pending(embedding_model: str, *, table_name: str = RAG_DOCUMENT_TABLE) -> int:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*) FROM {table_name}
            WHERE needs_embedding = TRUE OR embedding IS NULL
               OR embedding_model IS DISTINCT FROM %s
            """,
            [embedding_model],
        )
        return int((cur.fetchone() or [0])[0] or 0)


def sync_is_stale(state: dict[str, Any], *, max_age_seconds: int = RAG_SYNC_INTERVAL_SECONDS) -> bool:
    finished_at = state.get("last_finished_at")
    if not finished_at:
        return True
    try:
        return time.time() - finished_at.timestamp() >= max_age_seconds
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Document upsert / replace
# ---------------------------------------------------------------------------

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


def upsert_documents(documents: list[dict[str, Any]], *, table_name: str = RAG_DOCUMENT_TABLE) -> None:
    """Insert-or-update RAG documents with content-hash-based change detection.

    Only marks ``needs_embedding = TRUE`` when the content hash has changed or
    the embedding model differs from the current default.
    """
    if not documents:
        return
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT source_key, content_hash, embedding_model FROM {table_name} WHERE source_key = ANY(%s)",
            [[doc["source_key"] for doc in documents]],
        )
        existing = {row[0]: {"content_hash": row[1], "embedding_model": row[2]} for row in cur.fetchall()}

        for doc in documents:
            current = existing.get(doc["source_key"])
            embed_stale = (
                current is None
                or current.get("content_hash") != doc["content_hash"]
                or current.get("embedding_model") != DEFAULT_EMBEDDING_MODEL
            )
            cur.execute(
                f"""
                INSERT INTO {table_name} (
                    source_type, source_key, source_title, document_text, metadata,
                    source_updated_at, tenant_id, content_hash,
                    embedding_model, embedding, needs_embedding, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (source_key) DO UPDATE SET
                    source_type = EXCLUDED.source_type,
                    source_title = EXCLUDED.source_title,
                    document_text = EXCLUDED.document_text,
                    metadata = EXCLUDED.metadata,
                    source_updated_at = EXCLUDED.source_updated_at,
                    tenant_id = EXCLUDED.tenant_id,
                    content_hash = EXCLUDED.content_hash,
                    needs_embedding = EXCLUDED.needs_embedding,
                    updated_at = NOW()
                """,
                [
                    doc["source_type"], doc["source_key"], doc["title"],
                    doc["content"], json.dumps(doc["metadata"]),
                    doc.get("source_updated_at"), doc.get("tenant_id"),
                    doc["content_hash"], None, None, embed_stale,
                ],
            )

        # Remove documents of each source_type that were not in this batch
        for source_type in {doc["source_type"] for doc in documents}:
            source_keys = [doc["source_key"] for doc in documents if doc["source_type"] == source_type]
            cur.execute(
                f"DELETE FROM {table_name} WHERE source_type = %s AND NOT (source_key = ANY(%s))",
                [source_type, source_keys],
            )


def replace_documents(documents: list[dict[str, Any]], *, table_name: str) -> None:
    """Truncate *table_name* and insert all *documents* fresh (used for staging)."""
    with connection.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY")
    if not documents:
        return
    with connection.cursor() as cur:
        for doc in documents:
            cur.execute(
                f"""
                INSERT INTO {table_name} (
                    source_type, source_key, source_title, document_text, metadata,
                    source_updated_at, tenant_id, content_hash,
                    embedding_model, embedding, needs_embedding, updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    doc["source_type"], doc["source_key"], doc["title"],
                    doc["content"], json.dumps(doc["metadata"]),
                    doc.get("source_updated_at"), doc.get("tenant_id"),
                    doc["content_hash"], None, None, True,
                ],
            )


# ---------------------------------------------------------------------------
# Embedding writes (embed step → write vectors to DB)
# ---------------------------------------------------------------------------

def embed_stale_documents(
    *,
    ollama_api: str,
    embedding_model: str,
    timeout: int,
    table_name: str = RAG_DOCUMENT_TABLE,
    batch_size: int = 24,
    progress_callback: Any | None = None,
) -> None:
    """Fetch un-embedded documents, call Ollama, and write vectors back.

    Processes in batches of *batch_size* until no stale rows remain.
    """
    while True:
        with connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, document_text FROM {table_name}
                WHERE needs_embedding = TRUE OR embedding IS NULL
                   OR embedding_model IS DISTINCT FROM %s
                ORDER BY source_updated_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                [embedding_model, batch_size],
            )
            rows = cur.fetchall()
        if not rows:
            return

        embeddings = embed_texts(
            [row[1] for row in rows],
            ollama_api=ollama_api,
            embedding_model=embedding_model,
            timeout=timeout,
        )
        with connection.cursor() as cur:
            for row, embedding in zip(rows, embeddings):
                cur.execute(
                    f"""
                    UPDATE {table_name}
                    SET embedding = %s::vector, embedding_model = %s,
                        needs_embedding = FALSE, updated_at = NOW()
                    WHERE id = %s
                    """,
                    [_vector_literal(embedding), embedding_model, row[0]],
                )
        if progress_callback:
            progress_callback()
