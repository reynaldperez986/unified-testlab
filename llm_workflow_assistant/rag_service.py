"""
rag_service.py — thin backward-compatibility facade.

All logic now lives in the modular sub-packages:
  • ingestion/  (extractor, chunker, embedder, indexer, pipeline)
  • retrieval/  (retriever)
  • agent/      (agent)

This module re-exports every public symbol that recorder/views.py imports so
existing call sites require zero changes.
"""
from llm_workflow_assistant.agent.agent import (
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_TOP_K,
    SYSTEM_PROMPT,
    build_prompt as build_rag_prompt,
    query as query_workflow_assistant,
)
from llm_workflow_assistant.ingestion.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OLLAMA_API,
)
from llm_workflow_assistant.ingestion.indexer import (
    RAG_DOCUMENT_TABLE,
    RAG_STAGING_TABLE,
    RAG_SYNC_INTERVAL_SECONDS,
)
from llm_workflow_assistant.ingestion.pipeline import (
    _ensure_rag_ready,
    get_rag_status,
    queue_rag_rebuild,
    rebuild_rag_index,
)
from llm_workflow_assistant.retrieval.retriever import (
    HYBRID_KEYWORD_WEIGHT,
    HYBRID_VECTOR_WEIGHT,
    retrieve_context as retrieve_rag_context,
)

# Legacy aliases kept for any code that imported these private helpers directly.
from llm_workflow_assistant.ingestion.embedder import _vector_literal, _ollama_generate_url  # noqa: F401
from llm_workflow_assistant.ingestion.indexer import ensure_schema as _ensure_rag_schema  # noqa: F401

__all__ = [
    "DEFAULT_OLLAMA_API",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_EMBEDDING_MODEL",
    "DEFAULT_TOP_K",
    "HYBRID_VECTOR_WEIGHT",
    "HYBRID_KEYWORD_WEIGHT",
    "RAG_DOCUMENT_TABLE",
    "RAG_STAGING_TABLE",
    "RAG_SYNC_INTERVAL_SECONDS",
    "SYSTEM_PROMPT",
    # Pipeline
    "get_rag_status",
    "queue_rag_rebuild",
    "rebuild_rag_index",
    "_ensure_rag_ready",
    # Retrieval
    "retrieve_rag_context",
    # Agent
    "build_rag_prompt",
    "query_workflow_assistant",
]

