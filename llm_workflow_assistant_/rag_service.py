import json
import threading
import time
from typing import Any

import requests
from django.db import close_old_connections, connection, transaction

from llm_workflow_assistant.document_builder import build_rag_documents


DEFAULT_OLLAMA_API = "http://localhost:11434/api"
DEFAULT_OLLAMA_MODEL = "llama3"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DEFAULT_TOP_K = 12
HYBRID_VECTOR_WEIGHT = 0.62
HYBRID_KEYWORD_WEIGHT = 0.38
RAG_SYNC_INTERVAL_SECONDS = 300
DEFAULT_SYNC_KEY = "default"
RAG_DOCUMENT_TABLE = "ai_rag_document"
RAG_STAGING_TABLE = "ai_rag_document_staging"

_SYNC_LOCK = threading.Lock()
_SYNC_THREADS: dict[str, threading.Thread] = {}
_SYNC_JOB_LOCK = threading.Lock()

SYSTEM_PROMPT = """You are the WebConX Workflow Assistant.
Answer questions using the supplied PostgreSQL context from recorded steps, AI workflows, and AI databank objects.
Be concrete, cite the relevant record names, workflow names, page names, URLs, actions, step numbers, and locators when available.
If the context is insufficient, say what is missing instead of inventing details.
Explain workflows step by step, detect dependencies and flows, and summarize procedures clearly.
Prefer concise, actionable answers."""


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


def _vector_literal(values: list[float]) -> str:
    return "[" + ",".join(format(float(value), ".10f") for value in values) + "]"


def _serialize_timestamp(value: Any) -> str | None:
    return value.isoformat() if value else None


def _ollama_generate_url(api_base: str) -> str:
    base = (api_base or DEFAULT_OLLAMA_API).rstrip("/")
    if base.endswith("/generate"):
        return base
    if base.endswith("/api"):
        return base + "/generate"
    return base + "/api/generate"


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
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_type TEXT NOT NULL DEFAULT 'unknown'")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_key TEXT NOT NULL DEFAULT ''")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_title TEXT NOT NULL DEFAULT ''")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS document_text TEXT NOT NULL DEFAULT ''")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS source_updated_at TIMESTAMPTZ NULL")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS tenant_id UUID NULL")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS content_hash TEXT NOT NULL DEFAULT ''")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding_model TEXT NULL")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS embedding vector NULL")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS needs_embedding BOOLEAN NOT NULL DEFAULT TRUE")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cur.execute(f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
    cur.execute(f"CREATE UNIQUE INDEX IF NOT EXISTS {table_name}_source_key_uniq ON {table_name} (source_key)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_source_type_idx ON {table_name} (source_type)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_tenant_idx ON {table_name} (tenant_id)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_updated_at_idx ON {table_name} (updated_at DESC)")
    cur.execute(f"CREATE INDEX IF NOT EXISTS {table_name}_embedding_state_idx ON {table_name} (needs_embedding, embedding_model)")
    cur.execute(
        f"""
        CREATE INDEX IF NOT EXISTS {table_name}_fts_idx
        ON {table_name}
        USING GIN (to_tsvector('simple', COALESCE(source_title, '') || ' ' || COALESCE(document_text, '')))
        """
    )


def _ensure_rag_schema() -> None:
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
        cur.execute("ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS indexed_documents BIGINT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS pending_documents BIGINT NOT NULL DEFAULT 0")
        cur.execute("ALTER TABLE ai_rag_sync_state ADD COLUMN IF NOT EXISTS target_documents BIGINT NOT NULL DEFAULT 0")


def _get_sync_state(sync_key: str = DEFAULT_SYNC_KEY) -> dict[str, Any]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT sync_key, status, last_started_at, last_finished_at, last_error, indexed_documents, pending_documents, target_documents
            FROM ai_rag_sync_state
            WHERE sync_key = %s
            """,
            [sync_key],
        )
        row = cur.fetchone()
    if not row:
        return {
            "sync_key": sync_key,
            "status": "idle",
            "last_started_at": None,
            "last_finished_at": None,
            "last_error": None,
            "indexed_documents": 0,
            "pending_documents": 0,
            "target_documents": 0,
        }
    return {
        "sync_key": row[0],
        "status": row[1] or "idle",
        "last_started_at": row[2],
        "last_finished_at": row[3],
        "last_error": row[4],
        "indexed_documents": int(row[5] or 0),
        "pending_documents": int(row[6] or 0),
        "target_documents": int(row[7] or 0),
    }


def _serialize_sync_state(state: dict[str, Any]) -> dict[str, Any]:
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


def _set_sync_state(
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
                sync_key,
                status,
                last_started_at,
                last_finished_at,
                last_error,
                indexed_documents,
                pending_documents,
                target_documents,
                updated_at
            )
            VALUES (
                %s,
                %s,
                CASE WHEN %s THEN NOW() ELSE NULL END,
                CASE WHEN %s THEN NOW() ELSE NULL END,
                %s,
                COALESCE(%s, 0),
                COALESCE(%s, 0),
                COALESCE(%s, 0),
                NOW()
            )
            ON CONFLICT (sync_key)
            DO UPDATE SET
                status = EXCLUDED.status,
                last_started_at = CASE WHEN %s THEN NOW() ELSE ai_rag_sync_state.last_started_at END,
                last_finished_at = CASE WHEN %s THEN NOW() ELSE ai_rag_sync_state.last_finished_at END,
                last_error = %s,
                indexed_documents = COALESCE(%s, ai_rag_sync_state.indexed_documents),
                pending_documents = COALESCE(%s, ai_rag_sync_state.pending_documents),
                target_documents = COALESCE(%s, ai_rag_sync_state.target_documents),
                updated_at = NOW()
            """,
            [
                sync_key,
                status,
                started,
                finished,
                last_error,
                indexed_documents,
                pending_documents,
                target_documents,
                started,
                finished,
                last_error,
                indexed_documents,
                pending_documents,
                target_documents,
            ],
        )


def _clear_rag_documents(table_name: str = RAG_DOCUMENT_TABLE) -> None:
    with connection.cursor() as cur:
        cur.execute(f"TRUNCATE TABLE {table_name} RESTART IDENTITY")


def _count_indexed_documents(embedding_model: str, *, table_name: str = RAG_DOCUMENT_TABLE) -> int:
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT COUNT(*) FROM {table_name} WHERE embedding IS NOT NULL AND embedding_model = %s",
            [embedding_model],
        )
        row = cur.fetchone()
    return int((row or [0])[0] or 0)


def _count_pending_documents(embedding_model: str, *, table_name: str = RAG_DOCUMENT_TABLE) -> int:
    with connection.cursor() as cur:
        cur.execute(
            f"""
            SELECT COUNT(*)
            FROM {table_name}
            WHERE needs_embedding = TRUE OR embedding IS NULL OR embedding_model IS DISTINCT FROM %s
            """,
            [embedding_model],
        )
        row = cur.fetchone()
    return int((row or [0])[0] or 0)


def _sync_is_stale(state: dict[str, Any], *, max_age_seconds: int = RAG_SYNC_INTERVAL_SECONDS) -> bool:
    finished_at = state.get("last_finished_at")
    if not finished_at:
        return True
    try:
        age_seconds = time.time() - finished_at.timestamp()
    except Exception:
        return True
    return age_seconds >= max_age_seconds


def _upsert_rag_documents(documents: list[dict[str, Any]], *, table_name: str = RAG_DOCUMENT_TABLE) -> None:
    if not documents:
        return
    with connection.cursor() as cur:
        cur.execute(
            f"SELECT source_key, content_hash, embedding_model FROM {table_name} WHERE source_key = ANY(%s)",
            [[doc["source_key"] for doc in documents]],
        )
        existing = {
            row[0]: {"content_hash": row[1], "embedding_model": row[2]}
            for row in cur.fetchall()
        }

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
                    source_type,
                    source_key,
                    source_title,
                    document_text,
                    metadata,
                    source_updated_at,
                    tenant_id,
                    content_hash,
                    embedding_model,
                    embedding,
                    needs_embedding,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW())
                ON CONFLICT (source_key)
                DO UPDATE SET
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
                    doc["source_type"],
                    doc["source_key"],
                    doc["title"],
                    doc["content"],
                    json.dumps(doc["metadata"]),
                    doc.get("source_updated_at"),
                    doc.get("tenant_id"),
                    doc["content_hash"],
                    None,
                    None,
                    embed_stale,
                ],
            )

        for source_type in {doc["source_type"] for doc in documents}:
            source_keys = [doc["source_key"] for doc in documents if doc["source_type"] == source_type]
            cur.execute(
                f"DELETE FROM {table_name} WHERE source_type = %s AND NOT (source_key = ANY(%s))",
                [source_type, source_keys],
            )


def _replace_rag_documents(documents: list[dict[str, Any]], *, table_name: str) -> None:
    _clear_rag_documents(table_name)
    if not documents:
        return
    with connection.cursor() as cur:
        for doc in documents:
            cur.execute(
                f"""
                INSERT INTO {table_name} (
                    source_type,
                    source_key,
                    source_title,
                    document_text,
                    metadata,
                    source_updated_at,
                    tenant_id,
                    content_hash,
                    embedding_model,
                    embedding,
                    needs_embedding,
                    updated_at
                )
                VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s, %s, %s, NOW())
                """,
                [
                    doc["source_type"],
                    doc["source_key"],
                    doc["title"],
                    doc["content"],
                    json.dumps(doc["metadata"]),
                    doc.get("source_updated_at"),
                    doc.get("tenant_id"),
                    doc["content_hash"],
                    None,
                    None,
                    True,
                ],
            )


def _ollama_embed_texts(
    texts: list[str],
    *,
    ollama_api: str,
    embedding_model: str,
    timeout: int,
) -> list[list[float]]:
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
            f'Ollama embedding model "{embedding_model}" is not installed. Run: ollama pull {embedding_model}'
        )
    if response.ok:
        payload = response.json()
        embeddings = payload.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return [[float(value) for value in item] for item in embeddings]

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
                f'Ollama embedding model "{embedding_model}" is not installed. Run: ollama pull {embedding_model}'
            )
        single.raise_for_status()
        payload = single.json()
        vector = payload.get("embedding")
        if not isinstance(vector, list):
            raise ValueError("Ollama embeddings response did not include an embedding vector.")
        vectors.append([float(value) for value in vector])
    return vectors


def _embed_stale_documents(
    *,
    ollama_api: str,
    embedding_model: str,
    timeout: int,
    table_name: str = RAG_DOCUMENT_TABLE,
    batch_size: int = 24,
    progress_callback: Any | None = None,
) -> None:
    while True:
        with connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT id, document_text
                FROM {table_name}
                WHERE needs_embedding = TRUE OR embedding IS NULL OR embedding_model IS DISTINCT FROM %s
                ORDER BY source_updated_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                [embedding_model, batch_size],
            )
            rows = cur.fetchall()
        if not rows:
            return
        embeddings = _ollama_embed_texts(
            [row[1] for row in rows],
            ollama_api=ollama_api,
            embedding_model=embedding_model,
            timeout=timeout,
        )
        with connection.cursor() as cur:
            for row, embedding in zip(rows, embeddings):
                cur.execute(
                    f"UPDATE {table_name} SET embedding = %s::vector, embedding_model = %s, needs_embedding = FALSE, updated_at = NOW() WHERE id = %s",
                    [_vector_literal(embedding), embedding_model, row[0]],
                )
        if progress_callback:
            progress_callback()


def _perform_sync(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    _ensure_rag_schema()
    _upsert_rag_documents(build_rag_documents(), table_name=RAG_DOCUMENT_TABLE)
    _embed_stale_documents(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout, table_name=RAG_DOCUMENT_TABLE)


def _swap_staging_into_live() -> None:
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {RAG_DOCUMENT_TABLE} RESTART IDENTITY")
            cur.execute(
                f"""
                INSERT INTO {RAG_DOCUMENT_TABLE} (
                    source_type,
                    source_key,
                    source_title,
                    document_text,
                    metadata,
                    source_updated_at,
                    tenant_id,
                    content_hash,
                    embedding_model,
                    embedding,
                    needs_embedding,
                    created_at,
                    updated_at
                )
                SELECT
                    source_type,
                    source_key,
                    source_title,
                    document_text,
                    metadata,
                    source_updated_at,
                    tenant_id,
                    content_hash,
                    embedding_model,
                    embedding,
                    needs_embedding,
                    created_at,
                    updated_at
                FROM {RAG_STAGING_TABLE}
                """
            )


def _run_sync_job(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    close_old_connections()
    try:
        with _SYNC_JOB_LOCK:
            _set_sync_state("running", started=True)
            _perform_sync(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
            _set_sync_state("idle", finished=True)
    except Exception as exc:
        _set_sync_state("error", last_error=str(exc), finished=True)
        raise
    finally:
        close_old_connections()


def _run_rebuild_job(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    close_old_connections()
    try:
        with _SYNC_JOB_LOCK:
            _ensure_rag_schema()
            live_indexed_count = _count_indexed_documents(embedding_model, table_name=RAG_DOCUMENT_TABLE)
            _set_sync_state(
                "running",
                started=True,
                last_error=None,
                indexed_documents=live_indexed_count,
                pending_documents=0,
                target_documents=0,
            )
            documents = build_rag_documents()
            target_count = len(documents)
            _replace_rag_documents(documents, table_name=RAG_STAGING_TABLE)
            _set_sync_state(
                "running",
                last_error=None,
                indexed_documents=live_indexed_count,
                pending_documents=target_count,
                target_documents=target_count,
            )

            def update_progress() -> None:
                _set_sync_state(
                    "running",
                    last_error=None,
                    indexed_documents=live_indexed_count,
                    pending_documents=_count_pending_documents(embedding_model, table_name=RAG_STAGING_TABLE),
                    target_documents=target_count,
                )

            _embed_stale_documents(
                ollama_api=ollama_api,
                embedding_model=embedding_model,
                timeout=timeout,
                table_name=RAG_STAGING_TABLE,
                progress_callback=update_progress,
            )
            _swap_staging_into_live()
            final_live_count = _count_indexed_documents(embedding_model, table_name=RAG_DOCUMENT_TABLE)
            _set_sync_state(
                "idle",
                finished=True,
                last_error=None,
                indexed_documents=final_live_count,
                pending_documents=0,
                target_documents=final_live_count,
            )
    except Exception as exc:
        _set_sync_state("error", last_error=str(exc), finished=True)
        raise
    finally:
        close_old_connections()


def _start_async_job(
    *,
    embedding_model: str,
    thread_name: str,
    queued_status: str,
    target_factory: Any,
) -> bool:
    _ensure_rag_schema()
    live_indexed_count = _count_indexed_documents(embedding_model, table_name=RAG_DOCUMENT_TABLE)
    with _SYNC_LOCK:
        existing = _SYNC_THREADS.get(embedding_model)
        if existing and existing.is_alive():
            return False

        _set_sync_state(
            queued_status,
            last_error=None,
            indexed_documents=live_indexed_count,
            pending_documents=0,
            target_documents=0,
        )

        def target() -> None:
            try:
                target_factory()
            except Exception:
                pass
            finally:
                with _SYNC_LOCK:
                    _SYNC_THREADS.pop(embedding_model, None)

        thread = threading.Thread(target=target, name=thread_name, daemon=True)
        _SYNC_THREADS[embedding_model] = thread
        thread.start()
        return True


def _start_async_sync(*, ollama_api: str, embedding_model: str, timeout: int) -> bool:
    return _start_async_job(
        embedding_model=embedding_model,
        thread_name=f"rag-sync-{embedding_model}",
        queued_status="queued",
        target_factory=lambda: _run_sync_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout),
    )


def get_rag_status(*, embedding_model: str = DEFAULT_EMBEDDING_MODEL) -> dict[str, Any]:
    _ensure_rag_schema()
    state = _get_sync_state()
    status = _serialize_sync_state(state)
    live_indexed = _count_indexed_documents(embedding_model, table_name=RAG_DOCUMENT_TABLE)
    status["indexed_documents"] = live_indexed

    # Detect stale "running"/"queued" state: happens when the server restarts and the
    # background thread is gone but the DB row was never reset to idle.
    if status["status"] in {"queued", "running"}:
        with _SYNC_LOCK:
            thread = _SYNC_THREADS.get(embedding_model)
            thread_alive = thread is not None and thread.is_alive()
        if not thread_alive:
            # Thread is gone — reset the DB row so the UI doesn't spin forever.
            _set_sync_state("idle", finished=True, last_error="Rebuild interrupted (server restarted or thread died).")
            state = _get_sync_state()
            status = _serialize_sync_state(state)
            status["indexed_documents"] = live_indexed

    if status["status"] in {"queued", "running"}:
        status["pending_documents"] = int(state.get("pending_documents") or 0)
        status["target_documents"] = int(state.get("target_documents") or 0)
    else:
        status["pending_documents"] = _count_pending_documents(embedding_model, table_name=RAG_DOCUMENT_TABLE)
        status["target_documents"] = live_indexed
    status["is_active"] = status["status"] in {"queued", "running"}
    return status


def queue_rag_rebuild(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 300,
) -> dict[str, Any]:
    queued = _start_async_job(
        embedding_model=embedding_model,
        thread_name=f"rag-rebuild-{embedding_model}",
        queued_status="queued",
        target_factory=lambda: _run_rebuild_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout),
    )
    status = get_rag_status(embedding_model=embedding_model)
    status["queued"] = queued
    status["mode"] = "background" if status.get("is_active") else "ready"
    return status


def _ensure_rag_ready(*, ollama_api: str, embedding_model: str, timeout: int) -> dict[str, Any]:
    _ensure_rag_schema()
    indexed_count = _count_indexed_documents(embedding_model)
    state = _get_sync_state()
    current_status = state.get("status")

    if indexed_count == 0 and current_status in {"queued", "running"}:
        return {
            "mode": "rebuilding",
            "queued": current_status == "queued",
            "indexed_documents": indexed_count,
            **_serialize_sync_state(state),
        }

    if indexed_count == 0:
        _run_sync_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
        state = _get_sync_state()
        return {
            "mode": "blocking",
            "queued": False,
            "indexed_documents": _count_indexed_documents(embedding_model),
            **_serialize_sync_state(state),
        }

    queued = False
    if current_status not in {"queued", "running"} and _sync_is_stale(state):
        queued = _start_async_sync(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
        state = _get_sync_state()

    return {
        "mode": "background" if queued or state.get("status") in {"queued", "running"} else "ready",
        "queued": queued,
        "indexed_documents": indexed_count,
        **_serialize_sync_state(state),
    }


def rebuild_rag_index(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 120,
) -> dict[str, Any]:
    rebuild_started = time.perf_counter()
    _run_rebuild_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
    sync_state = get_rag_status(embedding_model=embedding_model)
    sync_state["indexed_documents"] = _count_indexed_documents(embedding_model)
    sync_state["rebuild_ms"] = round((time.perf_counter() - rebuild_started) * 1000, 1)
    return sync_state


def retrieve_rag_context(
    query: str,
    tenant_id: str | None = None,
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 120,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    sync = _ensure_rag_ready(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
    query_embedding = _ollama_embed_texts([query], ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)[0]
    query_vector = _vector_literal(query_embedding)

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
                        to_tsvector('simple', COALESCE(source_title, '') || ' ' || COALESCE(document_text, '')),
                        plainto_tsquery('simple', %s)
                    ) AS keyword_score,
                    CASE
                        WHEN embedding IS NOT NULL THEN 1 - (embedding <=> %s::vector)
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
                tenant_id,
                tenant_id,
                HYBRID_VECTOR_WEIGHT,
                HYBRID_KEYWORD_WEIGHT,
                top_k,
            ],
        )
        rows = cur.fetchall()

    retrieved_documents: list[dict[str, Any]] = []
    context = {
        "steps": [],
        "workflows": [],
        "databank": [],
        "documents": retrieved_documents,
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
        retrieved_documents.append(doc)
        if row[0] == "steps":
            context["steps"].append(metadata)
        elif row[0] == "ai_workflow":
            context["workflows"].append(metadata)
        elif row[0] == "ai_databank":
            context["databank"].append(metadata)

    context["counts"] = {
        "steps": len(context["steps"]),
        "workflows": len(context["workflows"]),
        "databank": len(context["databank"]),
        "documents": len(retrieved_documents),
    }
    return context


def build_rag_prompt(question: str, context: dict[str, Any]) -> str:
    sections = [f"User question: {question.strip()}"]

    document_lines = []
    for doc in context.get("documents", []):
        citation = str((doc.get("metadata") or {}).get("citation") or doc.get("source_key") or "source")
        document_lines.append(
            f"[{citation}] title={doc.get('title', '')} hybrid_score={doc.get('hybrid_score', 0):.4f}\n{doc.get('content', '')}"
        )

    sections.append(
        "Retrieved context:\n" + ("\n\n".join(document_lines) if document_lines else "- No matching documents found.")
    )
    sections.append(
        "Instructions: answer using only the retrieved context. Cite using the bracketed source labels when possible. Explain step-by-step workflow order and dependencies when the data supports it."
    )
    return "\n\n".join(sections)


def query_workflow_assistant(
    question: str,
    *,
    tenant_id: str | None = None,
    ollama_api: str = DEFAULT_OLLAMA_API,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout: int = 120,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    top_k: int = DEFAULT_TOP_K,
) -> dict[str, Any]:
    retrieval_started = time.perf_counter()
    context = retrieve_rag_context(
        question,
        tenant_id=tenant_id,
        ollama_api=ollama_api,
        embedding_model=embedding_model,
        timeout=timeout,
        top_k=top_k,
    )
    retrieval_duration_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)
    prompt = build_rag_prompt(question, context)
    generation_started = time.perf_counter()
    response = requests.post(
        _ollama_generate_url(ollama_api),
        json={
            "model": model or DEFAULT_OLLAMA_MODEL,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    generation_duration_ms = round((time.perf_counter() - generation_started) * 1000, 1)
    return {
        "answer": str(payload.get("response") or "").strip(),
        "context": context,
        "prompt": prompt,
        "model": model or DEFAULT_OLLAMA_MODEL,
        "embedding_model": embedding_model,
        "ollama_api": ollama_api or DEFAULT_OLLAMA_API,
        "timings": {
            "retrieval_ms": retrieval_duration_ms,
            "generation_ms": generation_duration_ms,
            "total_ms": round(retrieval_duration_ms + generation_duration_ms, 1),
        },
    }