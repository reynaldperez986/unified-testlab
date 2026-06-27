"""
ingestion/extractor.py — Source data extractor.

Mirrors the BA Agent extractor.py role.
Pulls structured records from the PostgreSQL database (steps, workflows,
AI databank objects) and converts them into the canonical RAG document dict
format ready for chunking and indexing.

Technology: Django ORM / psycopg2 via django.db.connection (existing stack).
"""
from __future__ import annotations

# Re-export the existing document builders so the rest of the ingestion
# pipeline has a single, stable import point.
from llm_workflow_assistant.document_builder import (  # noqa: F401
    build_rag_documents,
    _build_step_documents as _build_step_documents,
    _build_workflow_documents as _build_workflow_documents,
    _build_databank_documents as _build_databank_documents,
)

__all__ = [
    "build_rag_documents",
    "_build_step_documents",
    "_build_workflow_documents",
    "_build_databank_documents",
]
