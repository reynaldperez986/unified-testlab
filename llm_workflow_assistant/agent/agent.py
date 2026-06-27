"""
agent/agent.py — prompt construction and answer generation.

Implements Steps 3–5 of the BA Agent pattern:
  3. Build Prompt  — system prompt + retrieved chunks + conversation history
  4. Generate      — Ollama /api/generate
  5. Return        — grounded answer + source references

Public API:
  • SYSTEM_PROMPT  — system instruction string (re-exported for legacy callers)
  • build_prompt(question, context, *, conversation_history, max_history_turns)
  • query(question, *, tenant_id, ollama_api, model, timeout, embedding_model, top_k,
          conversation_history)
"""
from __future__ import annotations

import time
from typing import Any

import requests

from llm_workflow_assistant.ingestion.embedder import (
    DEFAULT_EMBEDDING_MODEL,
    DEFAULT_OLLAMA_API,
    _ollama_generate_url,
)
from llm_workflow_assistant.retrieval.retriever import retrieve_context

DEFAULT_OLLAMA_MODEL = "llama3.2:3b"
DEFAULT_TOP_K = 12

SYSTEM_PROMPT = """You are the WebConX Workflow Assistant.
Answer questions using the supplied PostgreSQL context from recorded steps, AI workflows, and AI databank objects.
Be concrete, cite the relevant record names, workflow names, page names, URLs, actions, step numbers, and locators when available.
If the context is insufficient, say what is missing instead of inventing details.
When showing steps, test cases, or test scripts, always present them ordered by step_no in a markdown table with columns: #, Steps, Page URL, Action, Element.
Explain workflows step by step, detect dependencies and flows, and summarize procedures clearly.
Prefer concise, actionable answers."""


# ---------------------------------------------------------------------------
# Step 3 — Build Prompt
# ---------------------------------------------------------------------------

def build_prompt(
    question: str,
    context: dict[str, Any],
    *,
    conversation_history: list[dict[str, str]] | None = None,
    max_history_turns: int = 6,
) -> str:
    """Construct the full prompt string sent to Ollama.

    The prompt has four sections (in order):
      1. Retrieved context chunks with bracketed citation labels.
      2. Conversation history (last ``max_history_turns`` turns) — gives the
         LLM short-term memory so follow-up questions work correctly.
      3. The current user question.
      4. Answering instructions (cite sources, markdown table for steps, etc.).

    Args:
        question: The user's current question.
        context: The dict returned by ``retriever.retrieve_context()``.
        conversation_history: List of ``{"role": "user"|"assistant", "content": ...}``
            dicts, oldest first.  Pass ``None`` or ``[]`` to skip.
        max_history_turns: How many turns (user+assistant pairs) to include.
            Each turn = 2 list items; so 6 turns = up to 12 items.
    """
    sections: list[str] = []

    # --- 1. Retrieved context ---
    document_lines: list[str] = []
    for doc in context.get("documents", []):
        citation = str(
            (doc.get("metadata") or {}).get("citation") or doc.get("source_key") or "source"
        )
        document_lines.append(
            f"[{citation}] title={doc.get('title', '')} "
            f"hybrid_score={doc.get('hybrid_score', 0):.4f}\n{doc.get('content', '')}"
        )
    sections.append(
        "Retrieved context:\n"
        + ("\n\n".join(document_lines) if document_lines else "- No matching documents found.")
    )

    # --- 2. Conversation history ---
    if conversation_history:
        recent = conversation_history[-(max_history_turns * 2):]
        history_lines: list[str] = []
        for turn in recent:
            role = turn.get("role", "user")
            content = (turn.get("content") or "").strip()
            if content:
                label = "User" if role == "user" else "Assistant"
                history_lines.append(f"{label}: {content}")
        if history_lines:
            sections.append("Conversation history:\n" + "\n".join(history_lines))

    # --- 3. Current question ---
    sections.append(f"User question: {question.strip()}")

    # --- 4. Answering instructions ---
    sections.append(
        "Instructions: answer using only the retrieved context. "
        "Cite using the bracketed source labels when possible. "
        "When showing steps, test cases, or test scripts, present them ordered by step_no "
        "in a markdown table with columns: #, Steps, Page URL, Action, Element. "
        "Explain step-by-step workflow order and dependencies when the data supports it."
    )

    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Step 4–5 — Generate and return grounded answer + source references
# ---------------------------------------------------------------------------

def query(
    question: str,
    *,
    tenant_id: str | None = None,
    ollama_api: str = DEFAULT_OLLAMA_API,
    model: str = DEFAULT_OLLAMA_MODEL,
    timeout: int = 120,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    top_k: int = DEFAULT_TOP_K,
    conversation_history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Full 4-step RAG pipeline: embed → retrieve → build prompt → generate.

    Returns:
        {
            "answer":          str,   # grounded answer text
            "sources":         list,  # [{citation, title, type, score}, ...]
            "context":         dict,  # full retrieval context
            "prompt":          str,   # the assembled prompt (for debugging)
            "model":           str,
            "embedding_model": str,
            "ollama_api":      str,
            "timings":         {"retrieval_ms", "generation_ms", "total_ms"},
        }
    """
    # Step 2 — Retrieve context (Step 1 embed happens inside retrieve_context)
    retrieval_started = time.perf_counter()
    context = retrieve_context(
        question,
        tenant_id=tenant_id,
        ollama_api=ollama_api,
        embedding_model=embedding_model,
        timeout=timeout,
        top_k=top_k,
    )
    retrieval_ms = round((time.perf_counter() - retrieval_started) * 1000, 1)

    # Step 3 — Build prompt (with conversation history)
    prompt = build_prompt(
        question,
        context,
        conversation_history=conversation_history,
    )

    # Step 4 — Generate
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
    generation_ms = round((time.perf_counter() - generation_started) * 1000, 1)

    # Step 5 — Assemble source references list
    sources: list[dict[str, Any]] = []
    seen: set[str] = set()
    for doc in context.get("documents", []):
        citation = str(
            (doc.get("metadata") or {}).get("citation") or doc.get("source_key") or ""
        )
        if citation and citation not in seen:
            seen.add(citation)
            sources.append(
                {
                    "citation": citation,
                    "title": doc.get("title") or "",
                    "type": doc.get("source_type") or "",
                    "score": round(float(doc.get("hybrid_score") or 0), 4),
                }
            )

    return {
        "answer": str(payload.get("response") or "").strip(),
        "sources": sources,
        "context": context,
        "prompt": prompt,
        "model": model or DEFAULT_OLLAMA_MODEL,
        "embedding_model": embedding_model,
        "ollama_api": ollama_api or DEFAULT_OLLAMA_API,
        "timings": {
            "retrieval_ms": retrieval_ms,
            "generation_ms": generation_ms,
            "total_ms": round(retrieval_ms + generation_ms, 1),
        },
    }
