"""
ingestion/pipeline.py — sync/rebuild orchestration.

Mirrors the BA Agent pipeline role (document ingestion → embedding → index).

Public API (re-exported through rag_service.py for backward compatibility):
  • get_rag_status(*, embedding_model)
  • queue_rag_rebuild(*, ollama_api, embedding_model, timeout)
  • rebuild_rag_index(*, ollama_api, embedding_model, timeout)
  • _ensure_rag_ready(*, ollama_api, embedding_model, timeout)
"""
from __future__ import annotations

import threading
import time
from typing import Any

from django.db import close_old_connections, connection, transaction

from llm_workflow_assistant.ingestion.embedder import DEFAULT_EMBEDDING_MODEL, DEFAULT_OLLAMA_API
from llm_workflow_assistant.ingestion.extractor import build_rag_documents
from llm_workflow_assistant.ingestion.indexer import (
    RAG_DOCUMENT_TABLE,
    RAG_STAGING_TABLE,
    RAG_SYNC_INTERVAL_SECONDS,
    count_indexed,
    count_pending,
    embed_stale_documents,
    ensure_schema,
    get_sync_state,
    replace_documents,
    serialize_sync_state,
    set_sync_state,
    sync_is_stale,
    upsert_documents,
)

DEFAULT_TIMEOUT = 300

_SYNC_LOCK = threading.Lock()
_SYNC_THREADS: dict[str, threading.Thread] = {}
_SYNC_JOB_LOCK = threading.Lock()


# ---------------------------------------------------------------------------
# Internal sync/rebuild jobs
# ---------------------------------------------------------------------------

def _perform_sync(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    ensure_schema()
    upsert_documents(build_rag_documents(), table_name=RAG_DOCUMENT_TABLE)
    embed_stale_documents(
        ollama_api=ollama_api,
        embedding_model=embedding_model,
        timeout=timeout,
        table_name=RAG_DOCUMENT_TABLE,
    )


def _swap_staging_into_live() -> None:
    with transaction.atomic():
        with connection.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {RAG_DOCUMENT_TABLE} RESTART IDENTITY")
            cur.execute(
                f"""
                INSERT INTO {RAG_DOCUMENT_TABLE} (
                    source_type, source_key, source_title, document_text, metadata,
                    source_updated_at, tenant_id, content_hash,
                    embedding_model, embedding, needs_embedding, created_at, updated_at
                )
                SELECT
                    source_type, source_key, source_title, document_text, metadata,
                    source_updated_at, tenant_id, content_hash,
                    embedding_model, embedding, needs_embedding, created_at, updated_at
                FROM {RAG_STAGING_TABLE}
                """
            )


def _run_sync_job(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    close_old_connections()
    try:
        with _SYNC_JOB_LOCK:
            set_sync_state("running", started=True)
            _perform_sync(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
            set_sync_state("idle", finished=True)
    except Exception as exc:
        set_sync_state("error", last_error=str(exc), finished=True)
        raise
    finally:
        close_old_connections()


def _run_rebuild_job(*, ollama_api: str, embedding_model: str, timeout: int) -> None:
    close_old_connections()
    try:
        with _SYNC_JOB_LOCK:
            ensure_schema()
            # Start fresh in both tables
            from django.db import connection as _conn  # noqa: PLC0415
            with _conn.cursor() as cur:
                cur.execute(f"TRUNCATE TABLE {RAG_STAGING_TABLE} RESTART IDENTITY")
                cur.execute(f"TRUNCATE TABLE {RAG_DOCUMENT_TABLE} RESTART IDENTITY")

            set_sync_state(
                "running", started=True, last_error=None,
                indexed_documents=0, pending_documents=0, target_documents=0,
            )
            documents = build_rag_documents()
            target_count = len(documents)
            replace_documents(documents, table_name=RAG_STAGING_TABLE)
            set_sync_state(
                "running", last_error=None,
                indexed_documents=0, pending_documents=target_count, target_documents=target_count,
            )

            def _update_progress() -> None:
                set_sync_state(
                    "running", last_error=None,
                    indexed_documents=0,
                    pending_documents=count_pending(embedding_model, table_name=RAG_STAGING_TABLE),
                    target_documents=target_count,
                )

            embed_stale_documents(
                ollama_api=ollama_api,
                embedding_model=embedding_model,
                timeout=timeout,
                table_name=RAG_STAGING_TABLE,
                progress_callback=_update_progress,
            )
            _swap_staging_into_live()
            final_count = count_indexed(embedding_model, table_name=RAG_DOCUMENT_TABLE)
            set_sync_state(
                "idle", finished=True, last_error=None,
                indexed_documents=final_count, pending_documents=0, target_documents=final_count,
            )
    except Exception as exc:
        set_sync_state("error", last_error=str(exc), finished=True)
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
    ensure_schema()
    live_indexed = count_indexed(embedding_model, table_name=RAG_DOCUMENT_TABLE)
    with _SYNC_LOCK:
        existing = _SYNC_THREADS.get(embedding_model)
        if existing and existing.is_alive():
            return False

        set_sync_state(
            queued_status, last_error=None,
            indexed_documents=live_indexed, pending_documents=0, target_documents=0,
        )

        def _target() -> None:
            try:
                target_factory()
            except Exception:
                pass
            finally:
                with _SYNC_LOCK:
                    _SYNC_THREADS.pop(embedding_model, None)

        thread = threading.Thread(target=_target, name=thread_name, daemon=True)
        _SYNC_THREADS[embedding_model] = thread
        thread.start()
        return True


def _start_async_sync(*, ollama_api: str, embedding_model: str, timeout: int) -> bool:
    return _start_async_job(
        embedding_model=embedding_model,
        thread_name=f"rag-sync-{embedding_model}",
        queued_status="queued",
        target_factory=lambda: _run_sync_job(
            ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout
        ),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_rag_status(*, embedding_model: str = DEFAULT_EMBEDDING_MODEL) -> dict[str, Any]:
    ensure_schema()
    state = get_sync_state()
    status = serialize_sync_state(state)
    live_indexed = count_indexed(embedding_model, table_name=RAG_DOCUMENT_TABLE)
    status["indexed_documents"] = live_indexed

    # If DB says running/queued but the thread is gone (server restarted), reset.
    if status["status"] in {"queued", "running"}:
        with _SYNC_LOCK:
            thread = _SYNC_THREADS.get(embedding_model)
            thread_alive = thread is not None and thread.is_alive()
        if not thread_alive:
            set_sync_state(
                "idle", finished=True,
                last_error="Rebuild interrupted (server restarted or thread died).",
            )
            state = get_sync_state()
            status = serialize_sync_state(state)
            status["indexed_documents"] = live_indexed

    if status["status"] in {"queued", "running"}:
        status["pending_documents"] = int(state.get("pending_documents") or 0)
        status["target_documents"] = int(state.get("target_documents") or 0)
    else:
        status["pending_documents"] = count_pending(embedding_model, table_name=RAG_DOCUMENT_TABLE)
        status["target_documents"] = live_indexed

    status["is_active"] = status["status"] in {"queued", "running"}
    return status


def queue_rag_rebuild(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    queued = _start_async_job(
        embedding_model=embedding_model,
        thread_name=f"rag-rebuild-{embedding_model}",
        queued_status="queued",
        target_factory=lambda: _run_rebuild_job(
            ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout
        ),
    )
    status = get_rag_status(embedding_model=embedding_model)
    status["queued"] = queued
    status["mode"] = "background" if status.get("is_active") else "ready"
    return status


def _ensure_rag_ready(*, ollama_api: str, embedding_model: str, timeout: int) -> dict[str, Any]:
    ensure_schema()
    indexed_count = count_indexed(embedding_model)
    state = get_sync_state()
    current_status = state.get("status")

    if indexed_count == 0 and current_status in {"queued", "running"}:
        return {
            "mode": "rebuilding",
            "queued": current_status == "queued",
            "indexed_documents": indexed_count,
            **serialize_sync_state(state),
        }

    if indexed_count == 0:
        _run_sync_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
        state = get_sync_state()
        return {
            "mode": "blocking",
            "queued": False,
            "indexed_documents": count_indexed(embedding_model),
            **serialize_sync_state(state),
        }

    queued = False
    if current_status not in {"queued", "running"} and sync_is_stale(state):
        queued = _start_async_sync(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
        state = get_sync_state()

    return {
        "mode": "background" if queued or state.get("status") in {"queued", "running"} else "ready",
        "queued": queued,
        "indexed_documents": indexed_count,
        **serialize_sync_state(state),
    }


def rebuild_rag_index(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    timeout: int = 120,
) -> dict[str, Any]:
    started = time.perf_counter()
    _run_rebuild_job(ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout)
    status = get_rag_status(embedding_model=embedding_model)
    status["indexed_documents"] = count_indexed(embedding_model)
    status["rebuild_ms"] = round((time.perf_counter() - started) * 1000, 1)
    return status
