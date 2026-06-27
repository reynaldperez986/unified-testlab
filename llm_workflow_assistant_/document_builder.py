import hashlib
import json
from typing import Any

from django.db import connection


def _content_hash(title: str, content: str, metadata: dict[str, Any]) -> str:
    payload = {
        "title": title,
        "content": content,
        "metadata": metadata,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def _normalize_locator_property(raw_value: Any) -> dict[str, Any]:
    locator_property = raw_value or {}
    if isinstance(locator_property, str):
        try:
            locator_property = json.loads(locator_property)
        except Exception:
            locator_property = {}
    return locator_property if isinstance(locator_property, dict) else {}


def _build_step_documents() -> list[dict[str, Any]]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT
                s.record_id::text,
                COALESCE(NULLIF(TRIM(m.record_name), ''), s.record_id::text) AS record_name,
                s.step_no,
                COALESCE(s.action, ''),
                COALESCE(s.page_url, ''),
                COALESCE(s.element_tag, ''),
                COALESCE(l.strategy, ''),
                COALESCE(l.locator, ''),
                COALESCE(d.value, ''),
                COALESCE(s.validation, ''),
                COALESCE(m.folder_name, s.folder_name, ''),
                s.tenant_id::text,
                s.created_at
            FROM steps s
            LEFT JOIN session_meta m ON m.record_id = s.record_id
            LEFT JOIN locators l
                ON l.record_id = s.record_id
               AND l.step_no = s.step_no
               AND l.is_primary = TRUE
            LEFT JOIN data d
                ON d.record_id = s.record_id
               AND d.step_no = s.step_no
            ORDER BY s.created_at DESC, s.record_id DESC, s.step_no DESC
            """
        )
        rows = cur.fetchall()

    documents: list[dict[str, Any]] = []
    for row in rows:
        metadata = {
            "record_id": row[0],
            "record_name": row[1],
            "step_no": row[2],
            "action": row[3],
            "page_url": row[4],
            "element_tag": row[5],
            "locator_strategy": row[6],
            "locator": row[7],
            "data_value": row[8],
            "validation": row[9],
            "folder_name": row[10],
            "tenant_id": row[11],
            "citation": f"steps:{row[1]}#step-{row[2]}",
        }
        title = f"Step {row[2]} · {row[1]}"
        content = "\n".join([
            f"Record name: {row[1]}",
            f"Record id: {row[0]}",
            f"Step number: {row[2]}",
            f"Action: {row[3]}",
            f"Page URL: {row[4]}",
            f"Element tag: {row[5]}",
            f"Primary locator: {row[6]} {row[7]}".strip(),
            f"Data value: {row[8]}",
            f"Validation: {row[9]}",
            f"Folder: {row[10]}",
        ])
        documents.append({
            "source_type": "steps",
            "source_key": f"steps:{row[0]}:{row[2]}",
            "title": title,
            "content": content,
            "metadata": metadata,
            "tenant_id": row[11],
            "source_updated_at": row[12],
            "content_hash": _content_hash(title, content, metadata),
        })
    return documents


def _build_workflow_documents() -> list[dict[str, Any]]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT workflow_name, page_connections, page_sequence, workflow_payload, updated_at
            FROM ai_workflow
            ORDER BY updated_at DESC, id DESC
            """
        )
        rows = cur.fetchall()

    documents: list[dict[str, Any]] = []
    for row in rows:
        workflow_name = str(row[0] or "").strip()
        page_connections = row[1] if isinstance(row[1], list) else []
        page_sequence = row[2] if isinstance(row[2], list) else []
        workflow_payload = row[3] if isinstance(row[3], dict) else {}
        page_names = [str(item.get("page_name") or "").strip() for item in page_sequence if isinstance(item, dict)]
        metadata = {
            "workflow_name": workflow_name,
            "page_names": [name for name in page_names if name],
            "page_connections": page_connections,
            "page_sequence": page_sequence,
            "view_state": workflow_payload.get("view_state") if isinstance(workflow_payload.get("view_state"), dict) else {},
            "citation": f"ai_workflow:{workflow_name}",
        }
        sequence_text = ", ".join(
            f"{item.get('order', '?')}:{item.get('page_name', '')}" for item in page_sequence if isinstance(item, dict)
        )
        connections_text = ", ".join(
            f"{item.get('from_page_name', '')}->{item.get('to_page_name', '')}" for item in page_connections if isinstance(item, dict)
        )
        title = f"Workflow · {workflow_name}"
        content = "\n".join([
            f"Workflow name: {workflow_name}",
            f"Page sequence: {sequence_text or 'None'}",
            f"Page connections: {connections_text or 'None'}",
            f"View state: {json.dumps(metadata['view_state'])}",
        ])
        documents.append({
            "source_type": "ai_workflow",
            "source_key": f"ai_workflow:{workflow_name}",
            "title": title,
            "content": content,
            "metadata": metadata,
            "tenant_id": None,
            "source_updated_at": row[4],
            "content_hash": _content_hash(title, content, metadata),
        })
    return documents


def _build_databank_documents() -> list[dict[str, Any]]:
    with connection.cursor() as cur:
        cur.execute(
            """
            SELECT id, page_name, page_url, element_type, locator_property, updated_at
            FROM ai_databank
            ORDER BY updated_at DESC, id DESC
            """
        )
        rows = cur.fetchall()

    documents: list[dict[str, Any]] = []
    for row in rows:
        locator_property = _normalize_locator_property(row[4])
        locators = locator_property.get("locators") if isinstance(locator_property.get("locators"), dict) else {}
        metadata = {
            "id": row[0],
            "page_name": row[1] or "Untitled page",
            "page_url": row[2] or "",
            "element_type": row[3] or "element",
            "tag_name": locator_property.get("tag_name") or "",
            "text": locator_property.get("text") or "",
            "locator_keys": list(locators.keys())[:12],
            "citation": f"ai_databank:{row[0]}",
        }
        locator_text = "; ".join(f"{key}:{value}" for key, value in list(locators.items())[:12])
        title = f"Databank · {metadata['page_name']} · Row {row[0]}"
        content = "\n".join([
            f"Databank row: {row[0]}",
            f"Page name: {metadata['page_name']}",
            f"Page URL: {metadata['page_url']}",
            f"Element type: {metadata['element_type']}",
            f"Tag name: {metadata['tag_name']}",
            f"Element text: {metadata['text']}",
            f"Locators: {locator_text or 'None'}",
        ])
        documents.append({
            "source_type": "ai_databank",
            "source_key": f"ai_databank:{row[0]}",
            "title": title,
            "content": content,
            "metadata": metadata,
            "tenant_id": None,
            "source_updated_at": row[5],
            "content_hash": _content_hash(title, content, metadata),
        })
    return documents


def build_rag_documents() -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    documents.extend(_build_step_documents())
    documents.extend(_build_workflow_documents())
    documents.extend(_build_databank_documents())
    return documents