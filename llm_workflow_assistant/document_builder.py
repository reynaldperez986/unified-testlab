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


def _safe_json(val: Any) -> Any:
    """Return a JSON-safe representation (dicts/lists pass through, strings are parsed)."""
    if val is None:
        return None
    if isinstance(val, (dict, list)):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return val
    return val


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
                COALESCE(s.field_name, ''),
                COALESCE(s.field_value, ''),
                s.raw_event,
                s.locators_raw,
                s.tenant_id::text,
                s.created_at,
                COALESCE(s.steps_description, ''),
                COALESCE(s.page_title, '')
            FROM steps s
            LEFT JOIN session_meta m ON m.record_id = s.record_id
            ORDER BY s.created_at DESC, s.record_id DESC, s.step_no DESC
            """
        )
        rows = cur.fetchall()

    documents: list[dict[str, Any]] = []
    for row in rows:
        (record_id, record_name, step_no, action, page_url,
         element_tag, field_name, field_value, raw_event,
         locators_raw, tenant_id, created_at, steps_description, page_title) = row

        raw_event_safe = _safe_json(raw_event)
        locators_raw_safe = _safe_json(locators_raw)

        metadata = {
            "record_id": record_id,
            "record_name": record_name,
            "step_no": step_no,
            "action": action,
            "page_url": page_url,
            "element_tag": element_tag,
            "field_name": field_name,
            "field_value": field_value,
            "raw_event": raw_event_safe,
            "locators_raw": locators_raw_safe,
            "tenant_id": tenant_id,
            "steps_description": steps_description,
            "page_title": page_title,
            "citation": f"steps:{record_name}#step-{step_no}",
        }
        title = f"{page_title or record_name} · Step {step_no}"
        content = "\n".join([
            f"Record name: {record_name}",
            f"Record id: {record_id}",
            f"Step number: {step_no}",
            f"Action: {action}",
            f"Description: {steps_description}",
            f"Page URL: {page_url}",
            f"Page title: {page_title}",
            f"Element tag: {element_tag}",
            f"Field name: {field_name}",
            f"Field value: {field_value}",
            f"Raw event: {json.dumps(raw_event_safe, default=str) if raw_event_safe else ''}",
            f"Locators raw: {json.dumps(locators_raw_safe, default=str) if locators_raw_safe else ''}",
        ])
        documents.append({
            "source_type": "steps",
            "source_key": f"steps:{record_id}:{step_no}",
            "title": title,
            "content": content,
            "metadata": metadata,
            "tenant_id": tenant_id,
            "source_updated_at": created_at,
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