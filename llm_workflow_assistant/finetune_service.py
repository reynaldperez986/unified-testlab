"""Fine-tuning service for the Workflow Assistant.

Provides dataset building (JSONL export from ai_rag_document), dataset upload
storage for external training workflows, custom Ollama model creation, model
listing, and test-script generation.
"""

import json
import os
import shutil
import sys
import time
from typing import Any

import requests
from django.db import connection

DEFAULT_OLLAMA_API = "http://localhost:11434/api"
DEFAULT_BASE_MODEL = "llama3.2:3b"
DEFAULT_FT_MODEL = "llama3-finetuned"
DEFAULT_EMBEDDING_MODEL = "nomic-embed-text"
DATASET_UPLOAD_DIRNAME = "uploads"
FINETUNE_WORKSPACE_DIRNAME = "finetune"

MODELFILE_SYSTEM_PROMPT = """\
You are the WebConX Workflow Assistant.

Your job is:
- Interpret PSQL Steps, ai_workflow, ai_databank workflow relationship
- Explain workflows step-by-step
- Understand automation scripts
- Summarize procedures
- Detect workflow dependencies and flows
- Generate Steps or scripts
- Cite from retrieved context when possible

Behavior rules:
- Use retrieved context as the primary source of truth.
- When possible, cite concrete workflow names, page names, page URLs, record IDs, step numbers, actions, and locators.
- If the available context is incomplete, say what is missing instead of inventing details.
- Prefer concise, structured, actionable answers.
- When describing a workflow, preserve the observed step order and call out dependencies between steps, pages, and workflows.
- When relevant, explain how databank objects and saved workflows connect back to recorded steps."""


def get_training_environment_status() -> dict[str, Any]:
    """Return whether local Unsloth training is supported in this environment."""
    status: dict[str, Any] = {
        "supported": False,
        "platform": sys.platform,
        "python_version": sys.version.split()[0],
        "torch_version": None,
        "cuda_available": False,
        "cuda_version": None,
        "device_name": None,
        "message": "Unknown training environment status.",
    }
    try:
        import torch
    except Exception as exc:
        status["message"] = f"PyTorch is required for LoRA training: {exc}"
        return status

    status["torch_version"] = getattr(torch, "__version__", None)
    status["cuda_available"] = bool(torch.cuda.is_available())
    status["cuda_version"] = getattr(getattr(torch, "version", None), "cuda", None)

    if status["cuda_available"]:
        try:
            status["device_name"] = torch.cuda.get_device_name(0)
        except Exception:
            status["device_name"] = None

    if sys.platform == "win32":
        status["message"] = "Unsloth LoRA training is not supported in this workflow on native Windows. Use Linux or WSL2 with an NVIDIA CUDA GPU."
        return status

    if not status["cuda_available"]:
        status["message"] = "Unsloth requires an NVIDIA CUDA GPU. No CUDA device was detected."
        return status

    status["supported"] = True
    status["message"] = "CUDA training environment detected."
    return status


# ---------------------------------------------------------------------------
# Ollama API helpers
# ---------------------------------------------------------------------------

def _api_base(ollama_api: str) -> str:
    base = (ollama_api or DEFAULT_OLLAMA_API).rstrip("/")
    if not base.endswith("/api"):
        base = base + "/api"
    return base


def list_ollama_models(*, ollama_api: str = DEFAULT_OLLAMA_API, timeout: int = 30) -> list[dict[str, Any]]:
    """Return the list of locally available Ollama models."""
    url = _api_base(ollama_api) + "/tags"
    response = requests.get(url, timeout=timeout)
    response.raise_for_status()
    return response.json().get("models", [])


def get_finetune_status(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    ft_model_name: str = DEFAULT_FT_MODEL,
    timeout: int = 30,
    _models: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Check whether the fine-tuned model is available locally.

    Pass *_models* (from a prior ``list_ollama_models`` call) to avoid a
    redundant HTTP round-trip to Ollama.
    """
    try:
        models = _models if _models is not None else list_ollama_models(ollama_api=ollama_api, timeout=timeout)
    except Exception as exc:
        return {"exists": False, "model": None, "error": str(exc)}
    for m in models:
        name = m.get("name") or m.get("model") or ""
        if name == ft_model_name or name.startswith(ft_model_name + ":"):
            return {"exists": True, "model": m, "error": None}
    return {"exists": False, "model": None, "error": None}


# ---------------------------------------------------------------------------
# Dataset building
# ---------------------------------------------------------------------------

def build_finetune_dataset() -> dict[str, Any]:
    """Build a JSONL training dataset from the live ai_rag_document table.

    Each row becomes one instruction/output pair suitable for Ollama or
    other LLM fine-tuning pipelines.
    """
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT source_type, source_key, source_title, document_text
            FROM ai_rag_document
            WHERE embedding IS NOT NULL
            ORDER BY source_type, source_key
            """
        )
        rows = cur.fetchall()

    entries: list[dict[str, str]] = []
    for source_type, source_key, title, text in rows:
        if source_type == "steps":
            instruction = f"Describe the recorded automation step: {title}"
        elif source_type == "ai_workflow":
            instruction = f"Explain the saved workflow: {title}"
        elif source_type == "ai_databank":
            instruction = f"Describe the databank object: {title}"
        else:
            instruction = f"Explain: {title}"
        entries.append({
            "instruction": instruction,
            "input": "",
            "output": text or "",
        })

    jsonl_lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    return {
        "count": len(entries),
        "jsonl": "\n".join(jsonl_lines),
    }


def save_uploaded_finetune_dataset(
    *,
    filename: str,
    content: bytes,
    base_dir: str,
    base_model: str = DEFAULT_BASE_MODEL,
    ft_model_name: str = DEFAULT_FT_MODEL,
) -> dict[str, Any]:
    """Validate and store a fine-tune dataset for external training.

    Ollama does not accept JSON/JSONL training uploads directly, so this stores
    the dataset locally and returns the next-step commands for an external LoRA
    workflow such as Unsloth.
    """
    raw_name = os.path.basename(filename or "finetune.jsonl")
    stem, ext = os.path.splitext(raw_name)
    ext = ext.lower()
    if ext not in {".json", ".jsonl"}:
        raise ValueError("Upload a .json or .jsonl fine-tune dataset.")

    try:
        text = content.decode("utf-8-sig")
    except Exception as exc:
        raise ValueError("Dataset must be UTF-8 encoded.") from exc

    normalized_lines: list[str] = []
    if ext == ".json":
        try:
            payload = json.loads(text)
        except Exception as exc:
            raise ValueError("Invalid JSON dataset.") from exc
        if not isinstance(payload, list):
            raise ValueError("JSON dataset must be an array of objects.")
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError("Each JSON dataset item must be an object.")
            normalized_lines.append(json.dumps(row, ensure_ascii=False))
    else:
        for line_no, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                row = json.loads(stripped)
            except Exception as exc:
                raise ValueError(f"Invalid JSON on line {line_no}.") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Line {line_no} must be a JSON object.")
            normalized_lines.append(json.dumps(row, ensure_ascii=False))

    if not normalized_lines:
        raise ValueError("Dataset is empty.")

    upload_dir = os.path.join(base_dir, "llm_workflow_assistant", DATASET_UPLOAD_DIRNAME)
    os.makedirs(upload_dir, exist_ok=True)
    safe_stem = (stem or "finetune").replace(" ", "_")
    saved_name = f"{safe_stem}.jsonl"
    saved_path = os.path.join(upload_dir, saved_name)

    counter = 1
    while os.path.exists(saved_path):
        saved_name = f"{safe_stem}_{counter}.jsonl"
        saved_path = os.path.join(upload_dir, saved_name)
        counter += 1

    normalized_text = "\n".join(normalized_lines) + "\n"
    with open(saved_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(normalized_text)

    workspace = prepare_finetune_workspace(
        dataset_path=saved_path,
        base_dir=base_dir,
        base_model=base_model,
        ft_model_name=ft_model_name,
    )

    instructions = [
        "Ollama does not accept fine-tune dataset uploads directly.",
        "Install Unsloth in a CUDA-capable Python environment: pip install unsloth",
        f"Train LoRA using dataset: {workspace['dataset_copy_path']}",
        f"Training script prepared: {workspace['trainer_script_path']}",
        f"Export GGUF to: {workspace['gguf_path']}",
        f"Create Modelfile prepared at: {workspace['modelfile_path']}",
        f"Import script prepared at: {workspace['import_script_path']}",
    ]
    return {
        "count": len(normalized_lines),
        "saved_path": saved_path,
        "saved_name": saved_name,
        **workspace,
        "instructions": instructions,
    }


def prepare_finetune_workspace(
    *,
    dataset_path: str,
    base_dir: str,
    base_model: str = DEFAULT_BASE_MODEL,
    ft_model_name: str = DEFAULT_FT_MODEL,
) -> dict[str, Any]:
    """Prepare reusable local files for the external LoRA -> GGUF -> Ollama flow."""
    workspace_dir = os.path.join(base_dir, "llm_workflow_assistant", FINETUNE_WORKSPACE_DIRNAME)
    os.makedirs(workspace_dir, exist_ok=True)

    dataset_copy_path = os.path.join(workspace_dir, "fine_tune.jsonl")
    shutil.copyfile(dataset_path, dataset_copy_path)

    gguf_name = f"{ft_model_name}.gguf"
    gguf_path = os.path.join(workspace_dir, gguf_name)
    modelfile_path = os.path.join(workspace_dir, "Modelfile")
    import_script_path = os.path.join(workspace_dir, "train_llama3.bat")
    trainer_script_path = os.path.join(workspace_dir, "train_lora_unsloth.py")

    with open(modelfile_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(
            f"FROM ./{gguf_name}\n"
            f"SYSTEM \"\"\"{MODELFILE_SYSTEM_PROMPT}\"\"\"\n"
        )

    with open(import_script_path, "w", encoding="utf-8", newline="\r\n") as fh:
        fh.write(
            "@echo off\n"
            "setlocal\n"
            f"set \"GGUF={gguf_name}\"\n"
            "if not exist \"%~dp0%GGUF%\" (\n"
            "    echo [ERROR] GGUF file not found: %~dp0%GGUF%\n"
            "    echo [INFO] Run the Unsloth training script first.\n"
            "    exit /b 1\n"
            ")\n"
            f"ollama create {ft_model_name} -f \"%~dp0Modelfile\"\n"
            "endlocal\n"
        )

    trainer_script = f'''import argparse
import os
import sys


def validate_runtime() -> None:
    try:
        import torch
    except Exception as exc:
        raise RuntimeError(f"PyTorch is required for Unsloth training: {{exc}}") from exc

    if sys.platform == "win32":
        raise RuntimeError("Unsloth training is not supported in this workflow on native Windows. Use Linux or WSL2 with an NVIDIA CUDA GPU.")
    if not torch.cuda.is_available():
        raise RuntimeError("Unsloth requires an NVIDIA CUDA GPU. No CUDA device was detected.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a LoRA adapter with Unsloth and export GGUF.")
    parser.add_argument("--dataset", default=r"{dataset_copy_path}")
    parser.add_argument("--base-model", default="{base_model}")
    parser.add_argument("--ft-model-name", default="{ft_model_name}")
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(__file__), "training_output"))
    args = parser.parse_args()

    validate_runtime()

    import unsloth
    from unsloth import FastLanguageModel
    from datasets import load_dataset
    from transformers import TrainingArguments
    from trl import SFTTrainer

    max_seq_length = 2048
    model, tokenizer = FastLanguageModel.from_pretrained(
        model_name=args.base_model,
        max_seq_length=max_seq_length,
        dtype=None,
        load_in_4bit=True,
    )
    model = FastLanguageModel.get_peft_model(
        model,
        r=16,
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        lora_alpha=16,
        lora_dropout=0,
        bias="none",
        use_gradient_checkpointing="unsloth",
        random_state=3407,
    )

    dataset = load_dataset("json", data_files=args.dataset, split="train")

    def format_row(row):
        instruction = str(row.get("instruction") or "").strip()
        input_text = str(row.get("input") or "").strip()
        output_text = str(row.get("output") or "").strip()
        prompt = f"### Instruction\\n{{instruction}}\\n\\n"
        if input_text:
            prompt += f"### Input\\n{{input_text}}\\n\\n"
        prompt += f"### Response\\n{{output_text}}"
        return {{"text": prompt}}

    dataset = dataset.map(format_row)
    trainer = SFTTrainer(
        model=model,
        tokenizer=tokenizer,
        train_dataset=dataset,
        dataset_text_field="text",
        max_seq_length=max_seq_length,
        args=TrainingArguments(
            output_dir=args.output_dir,
            per_device_train_batch_size=2,
            gradient_accumulation_steps=4,
            warmup_steps=5,
            max_steps=60,
            learning_rate=2e-4,
            fp16=True,
            logging_steps=1,
            optim="adamw_8bit",
            weight_decay=0.01,
            lr_scheduler_type="linear",
            seed=3407,
        ),
    )
    trainer.train()

    os.makedirs(args.output_dir, exist_ok=True)
    model.save_pretrained_gguf(
        os.path.dirname(__file__),
        tokenizer,
        quantization_method="q4_k_m",
        gguf_file_name=f"{{args.ft_model_name}}.gguf",
    )
    print("GGUF exported to:", os.path.join(os.path.dirname(__file__), args.ft_model_name + ".gguf"))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[ERROR] {{exc}}")
        raise SystemExit(1)
'''
    with open(trainer_script_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(trainer_script)

    return {
        "workspace_dir": workspace_dir,
        "dataset_copy_path": dataset_copy_path,
        "gguf_path": gguf_path,
        "modelfile_path": modelfile_path,
        "import_script_path": import_script_path,
        "trainer_script_path": trainer_script_path,
    }


# ---------------------------------------------------------------------------
# Model creation
# ---------------------------------------------------------------------------

def create_finetuned_model(
    *,
    ollama_api: str = DEFAULT_OLLAMA_API,
    base_model: str = DEFAULT_BASE_MODEL,
    ft_model_name: str = DEFAULT_FT_MODEL,
    timeout: int = 300,
) -> dict[str, Any]:
    """Create (or replace) a custom Ollama model with the WebConX system prompt.

    The Ollama REST API expects ``from`` + ``system`` fields rather than a raw
    Modelfile payload. The resulting model is an alias of *base_model* with a
    baked-in SYSTEM prompt so every query is automatically contextualised for
    workflow-assistant duties.
    """
    url = _api_base(ollama_api) + "/create"
    started = time.perf_counter()
    response = requests.post(
        url,
        json={
            "name": ft_model_name,
            "from": base_model,
            "system": MODELFILE_SYSTEM_PROMPT,
            "stream": False,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "ok": True,
        "model": ft_model_name,
        "base_model": base_model,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# Test-script generation
# ---------------------------------------------------------------------------

def generate_test_script(
    *,
    prompt: str,
    ollama_api: str = DEFAULT_OLLAMA_API,
    model: str = DEFAULT_FT_MODEL,
    timeout: int = 120,
) -> dict[str, Any]:
    """Ask Ollama to generate a Robot Framework test script."""
    system = (
        "You generate Robot Framework (.robot) test scripts. "
        "Use the Browser or SeleniumLibrary keywords. "
        "Include setup, teardown, and meaningful assertions. "
        "Output ONLY the .robot file content — no commentary."
    )
    url = _api_base(ollama_api) + "/generate"
    started = time.perf_counter()
    response = requests.post(
        url,
        json={
            "model": model or DEFAULT_FT_MODEL,
            "prompt": prompt,
            "system": system,
            "stream": False,
        },
        timeout=timeout,
    )
    # Fall back to the base model if the requested model is not found
    if response.status_code == 404:
        fallback = DEFAULT_BASE_MODEL
        response = requests.post(
            url,
            json={
                "model": fallback,
                "prompt": prompt,
                "system": system,
                "stream": False,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        model = fallback
    else:
        response.raise_for_status()
    payload = response.json()
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    return {
        "script": str(payload.get("response") or "").strip(),
        "model": model or DEFAULT_FT_MODEL,
        "elapsed_ms": elapsed_ms,
    }


# ---------------------------------------------------------------------------
# RAG search (direct document retrieval without LLM generation)
# ---------------------------------------------------------------------------

def search_rag_documents(
    *,
    query: str,
    ollama_api: str = DEFAULT_OLLAMA_API,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
    top_k: int = 10,
    timeout: int = 60,
) -> dict[str, Any]:
    """Perform a hybrid vector + keyword search and return matching documents."""
    from llm_workflow_assistant.rag_service import (
        _ollama_embed_texts,
        _vector_literal,
        _ensure_rag_schema,
    )

    _ensure_rag_schema()
    embeddings = _ollama_embed_texts(
        [query], ollama_api=ollama_api, embedding_model=embedding_model, timeout=timeout,
    )
    query_vector = _vector_literal(embeddings[0])

    with connection.cursor() as cur:
        cur.execute(
            """
            WITH ranked AS (
                SELECT
                    id, source_type, source_key, source_title, document_text, metadata,
                    ts_rank_cd(
                        to_tsvector('simple', COALESCE(source_title,'') || ' ' || COALESCE(document_text,'')),
                        plainto_tsquery('simple', %s)
                    ) AS kw,
                    CASE WHEN embedding IS NOT NULL THEN 1 - (embedding <=> %s::vector) ELSE 0 END AS vec
                FROM ai_rag_document
                WHERE embedding_model = %s
            )
            SELECT source_type, source_key, source_title, document_text, metadata,
                   kw, vec, (vec * 0.62 + kw * 0.38) AS score
            FROM ranked
            WHERE kw > 0 OR vec > 0
            ORDER BY score DESC
            LIMIT %s
            """,
            [query, query_vector, embedding_model, top_k],
        )
        rows = cur.fetchall()

    results: list[dict[str, Any]] = []
    for r in rows:
        meta = r[4]
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        results.append({
            "source_type": r[0],
            "source_key": r[1],
            "title": r[2],
            "content": r[3],
            "metadata": meta if isinstance(meta, dict) else {},
            "keyword_score": float(r[5] or 0),
            "vector_score": float(r[6] or 0),
            "hybrid_score": float(r[7] or 0),
        })
    return {"query": query, "count": len(results), "results": results}
