from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin

import requests

from django.db import connection, transaction

from llm_workflow_assistant.ingestion.embedder import DEFAULT_OLLAMA_API, _ollama_generate_url


class WorkflowAgentError(Exception):
	"""Base error for workflow-driven test-case generation."""


class WorkflowNotFoundError(WorkflowAgentError):
	"""Raised when the requested ai_workflow row does not exist."""


class WorkflowGenerationError(WorkflowAgentError):
	"""Raised when a workflow cannot be converted into actionable steps."""


_LOCATOR_STRATEGY_ORDER = (
	"xpath", "id", "name", "value", "placeholder", "class", "className",
	"tagName", "css", "href", "text", "linkText", "partialLinkText",
	"type", "role", "title", "alt", "ariaLabel", "dataTestId",
)

_LOCATOR_WRAP_MAP = {
	"value": lambda value: f'[value="{value}"]',
	"placeholder": lambda value: f'[placeholder="{value}"]',
	"type": lambda value: f'[type="{value}"]',
	"role": lambda value: f'[role="{value}"]',
	"title": lambda value: f'[title="{value}"]',
	"alt": lambda value: f'[alt="{value}"]',
	"href": lambda value: f'[href="{value}"]',
	"text": lambda value: f'//*[normalize-space(text())="{value}"]',
}

_TEXT_ENTRY_INPUT_TYPES = {
	"", "text", "email", "password", "search", "tel", "url", "number",
	"date", "datetime-local", "month", "time", "week",
}

_CLICK_INPUT_TYPES = {"button", "submit", "reset", "checkbox", "radio", "file", "image"}
DEFAULT_LLM_MODEL = "llama3.2:3b"


@dataclass(slots=True)
class GeneratedStep:
	action: str
	page_name: str
	page_url: str
	element_tag: str
	field_name: str
	field_value: str
	strategy: str
	locator: str
	raw_event: dict[str, Any]
	locators_raw: dict[str, str]
	validation: str
	steps_description: str
	page_title: str
	source_row_id: int | None
	pos_x: float | None = None
	pos_y: float | None = None


def create_test_case_from_workflow(
	workflow_name: str,
	*,
	record_name: str | None = None,
	folder_name: str = "",
	author: str = "system",
	max_steps_per_page: int = 8,
	use_llm: bool = True,
	ollama_api: str = DEFAULT_OLLAMA_API,
	model: str = DEFAULT_LLM_MODEL,
	llm_timeout: int = 60,
) -> dict[str, Any]:
	workflow_name = _clean_text(workflow_name)
	if not workflow_name:
		raise WorkflowGenerationError("workflow_name is required.")

	normalized_record_name = _clean_text(record_name) or f"{workflow_name} Generated"
	if max_steps_per_page < 1:
		max_steps_per_page = 1

	with transaction.atomic():
		with connection.cursor() as cur:
			workflow = _load_workflow(cur, workflow_name)
			page_sequence = _build_page_sequence(workflow)
			if not page_sequence:
				raise WorkflowGenerationError("The workflow has no ordered pages or page connections to generate from.")
			selection_mode = "deterministic"
			llm_notes = ""
			# If no explicit source_record_id on cards, resolve by matching page_name → session_meta.record_name
			if not any(_clean_text(item.get("source_record_id")) for item in page_sequence):
				_resolve_source_records_by_name(cur, page_sequence)
			if any(_clean_text(item.get("source_record_id")) for item in page_sequence):
				generated_steps, skipped_pages = _build_generated_steps_from_source_records(
					cur,
					page_sequence=page_sequence,
					workflow_name=workflow_name,
				)
				page_candidates = []
				candidate_index = {}
				selection_mode = "source-record"
			else:
				page_rows = _load_databank_rows_for_pages(cur, [item["page_name"] for item in page_sequence])
				connection_hints = _build_connection_hints(workflow)
				generated_steps, skipped_pages, page_candidates, candidate_index = _build_generated_steps(
					page_sequence=page_sequence,
					page_rows=page_rows,
					workflow_name=workflow_name,
					connection_hints=connection_hints,
					max_steps_per_page=max_steps_per_page,
				)
				if use_llm:
					llm_selected_steps, llm_notes = _llm_select_generated_steps(
						workflow_name=workflow_name,
						workflow=workflow,
						page_sequence=page_sequence,
						page_candidates=page_candidates,
						candidate_index=candidate_index,
						deterministic_steps=generated_steps,
						ollama_api=ollama_api,
						model=model,
						timeout=llm_timeout,
					)
					if llm_selected_steps:
						generated_steps = llm_selected_steps
						selection_mode = "llm-assisted"
			if not generated_steps:
				raise WorkflowGenerationError(
					"The workflow pages were found, but no actionable ai_databank elements could be converted into steps."
				)

			folder_ids = _resolve_folder_ids(cur, folder_name)
			file_order = _next_file_order(cur, folder_name)
			record_id = uuid.uuid4()

			# Overwrite: delete existing record with same name in same folder
			cur.execute(
				"""
				SELECT record_id FROM session_meta
				WHERE record_name = %s AND COALESCE(folder_name, '') = %s
				""",
				[normalized_record_name, folder_name or ""],
			)
			existing_ids = [str(row[0]) for row in cur.fetchall()]
			if existing_ids:
				placeholders = ",".join(["%s"] * len(existing_ids))
				cur.execute(f"DELETE FROM run_table WHERE record_id::text IN ({placeholders})", existing_ids)
				cur.execute(f"DELETE FROM recordings WHERE record_id::text IN ({placeholders})", existing_ids)
				cur.execute(f"DELETE FROM steps WHERE record_id::text IN ({placeholders})", existing_ids)
				cur.execute(f"DELETE FROM data WHERE record_id::text IN ({placeholders})", existing_ids)
				cur.execute(f"DELETE FROM locators WHERE record_id::text IN ({placeholders})", existing_ids)
				cur.execute(f"DELETE FROM session_meta WHERE record_id::text IN ({placeholders})", existing_ids)

			cur.execute(
				"""
				INSERT INTO session_meta
					(record_id, record_name, recorder, folder_name,
					 parent_folder_id, sub_folder_id, end_folder_id)
				VALUES (%s, %s, %s, %s, %s, %s, %s)
				""",
				[
					str(record_id),
					normalized_record_name,
					author,
					folder_name or None,
					folder_ids["parent_folder_id"],
					folder_ids["sub_folder_id"],
					folder_ids["end_folder_id"],
				],
			)

			for step_no, step in enumerate(generated_steps, start=1):
				data_id = _insert_data_row(cur, record_id, step_no, step, folder_name)
				locator_id = _insert_locator_row(cur, record_id, step_no, step, folder_name)

				cur.execute(
					"""
					INSERT INTO steps
						(record_id, step_no, action, page_url, element_tag,
						 locator_id, data_id, raw_event, recorder, folder_name,
						 locators_raw, field_name, field_value,
						 strategy, locator, is_primary, locator_rank,
						 folder_order, file_order, author, file_type,
						 parent_folder_id, sub_folder_id, end_folder_id, validation,
						 steps_description, page_title)
					VALUES (%s, %s, %s, %s, %s,
							%s, %s, %s::jsonb, %s, %s,
							%s::jsonb, %s, %s,
							%s, %s, TRUE, 1,
							1, %s, %s, 'step',
							%s, %s, %s, %s,
							%s, %s)
					""",
					[
						str(record_id),
						step_no,
						step.action,
						step.page_url,
						step.element_tag or None,
						locator_id,
						data_id,
						json.dumps(step.raw_event),
						author,
						folder_name or None,
						json.dumps(step.locators_raw or {}),
						step.field_name or None,
						step.field_value or None,
						step.strategy or None,
						step.locator or None,
						file_order,
						author,
						folder_ids["parent_folder_id"],
						folder_ids["sub_folder_id"],
						folder_ids["end_folder_id"],
						step.validation or None,
						step.steps_description or None,
						step.page_title or None,
					],
				)

			if folder_ids["end_folder_id"]:
				cur.execute(
					"""
					UPDATE end_folders
					SET end_file_order = end_file_order + 1,
						last_updated = NOW()
					WHERE end_folder_id = %s
					""",
					[folder_ids["end_folder_id"]],
				)

	return {
		"record_id": str(record_id),
		"record_name": normalized_record_name,
		"workflow_name": workflow_name,
		"folder_name": folder_name or "",
		"step_count": len(generated_steps),
		"selection_mode": selection_mode,
		"llm_notes": llm_notes,
		"pages_used": [item["page_name"] for item in page_sequence],
		"skipped_pages": skipped_pages,
		"steps": [
			{
				"step_no": index,
				"action": step.action,
				"page_name": step.page_name,
				"page_url": step.page_url,
				"field_name": step.field_name,
				"field_value": step.field_value,
				"strategy": step.strategy,
				"locator": step.locator,
				"source_row_id": step.source_row_id,
			}
			for index, step in enumerate(generated_steps, start=1)
		],
	}


def _load_workflow(cur, workflow_name: str) -> dict[str, Any]:
	cur.execute(
		"""
		SELECT workflow_name, page_connections, page_sequence, workflow_payload
		FROM ai_workflow
		WHERE workflow_name = %s
		LIMIT 1
		""",
		[workflow_name],
	)
	row = cur.fetchone()
	if not row:
		raise WorkflowNotFoundError(f"Workflow '{workflow_name}' was not found in ai_workflow.")
	return {
		"workflow_name": row[0],
		"page_connections": _json_list(row[1]),
		"page_sequence": _json_list(row[2]),
		"workflow_payload": _json_dict(row[3]),
	}


def _build_page_sequence(workflow: dict[str, Any]) -> list[dict[str, str]]:
	sequence: list[dict[str, str]] = []
	seen: set[str] = set()
	workflow_payload = workflow.get("workflow_payload") if isinstance(workflow.get("workflow_payload"), dict) else {}
	payload_cards = workflow_payload.get("cards") if isinstance(workflow_payload.get("cards"), list) else []
	cards_by_id: dict[str, dict[str, Any]] = {}
	for card in payload_cards:
		if not isinstance(card, dict):
			continue
		cards_by_id[_clean_text(card.get("id"))] = card

	raw_sequence = workflow.get("page_sequence") or []
	if raw_sequence:
		sorted_sequence = sorted(
			(item for item in raw_sequence if isinstance(item, dict)),
			key=lambda item: int(item.get("order") or 0),
		)
		for item in sorted_sequence:
			page_name = _normalize_page_name(item.get("page_name"))
			card_payload = cards_by_id.get(_clean_text(item.get("card_id"))) or {}
			source_record_id = _clean_text(item.get("source_record_id") or card_payload.get("source_record_id"))
			source_record_name = _clean_text(item.get("source_record_name") or card_payload.get("source_record_name") or page_name)
			seen_key = source_record_id or page_name
			if not page_name or seen_key in seen:
				continue
			sequence.append({
				"page_name": page_name,
				"page_url": _clean_text(item.get("page_url")),
				"source_record_id": source_record_id,
				"source_record_name": source_record_name,
			})
			seen.add(seen_key)

	if sequence:
		return sequence

	for connection in workflow.get("page_connections") or []:
		if not isinstance(connection, dict):
			continue
		for key in ("from_page_name", "to_page_name"):
			page_name = _normalize_page_name(connection.get(key))
			if not page_name or page_name in seen:
				continue
			sequence.append({"page_name": page_name, "page_url": "", "source_record_id": "", "source_record_name": page_name})
			seen.add(page_name)

	return sequence


def _load_databank_rows_for_pages(cur, page_names: list[str]) -> dict[str, list[dict[str, Any]]]:
	if not page_names:
		return {}

	placeholders = ", ".join(["%s"] * len(page_names))
	cur.execute(
		f"""
		SELECT id,
			   COALESCE(NULLIF(TRIM(page_name), ''), 'Untitled page') AS normalized_page_name,
			   COALESCE(page_url, '') AS page_url,
			   COALESCE(element_type, '') AS element_type,
			   locator_property,
			   updated_at
		FROM ai_databank
		WHERE COALESCE(NULLIF(TRIM(page_name), ''), 'Untitled page') IN ({placeholders})
		ORDER BY normalized_page_name ASC, updated_at DESC, id DESC
		""",
		page_names,
	)

	rows_by_page: dict[str, list[dict[str, Any]]] = {page_name: [] for page_name in page_names}
	for row in cur.fetchall():
		locator_property = _json_dict(row[4])
		rows_by_page.setdefault(row[1], []).append(
			{
				"id": row[0],
				"page_name": row[1],
				"page_url": row[2] or "",
				"element_type": row[3] or "",
				"locator_property": locator_property,
				"updated_at": row[5],
			}
		)
	return rows_by_page


def _build_generated_steps(
	*,
	page_sequence: list[dict[str, str]],
	page_rows: dict[str, list[dict[str, Any]]],
	workflow_name: str,
	connection_hints: dict[tuple[str, str], dict[str, Any]],
	max_steps_per_page: int,
) -> tuple[list[GeneratedStep], list[str], list[dict[str, Any]], dict[tuple[str, int], GeneratedStep]]:
	generated: list[GeneratedStep] = []
	skipped_pages: list[str] = []
	page_candidates: list[dict[str, Any]] = []
	candidate_index: dict[tuple[str, int], GeneratedStep] = {}
	first_page_url = ""
	if page_sequence:
		first_page_name = page_sequence[0]["page_name"]
		first_page_url = page_sequence[0].get("page_url") or _first_page_url(page_rows.get(first_page_name) or [])
		if first_page_url:
			generated.append(_build_open_step(first_page_name, first_page_url, workflow_name))

	for index, page in enumerate(page_sequence):
		page_name = page["page_name"]
		page_url = page.get("page_url") or _first_page_url(page_rows.get(page_name) or [])
		next_page = page_sequence[index + 1] if index + 1 < len(page_sequence) else None
		connection_hint = _connection_hint_for_pages(connection_hints, page_name, next_page)
		candidates, candidate_payload, actionable = _page_candidates(
			page_rows.get(page_name) or [],
			page_name,
			page_url,
			next_page,
			workflow_name,
			connection_hint,
		)
		page_candidates.append(candidate_payload)
		for step in actionable:
			if step.source_row_id is None:
				continue
			candidate_index[(step.page_name, int(step.source_row_id))] = step
		if not candidates:
			skipped_pages.append(page_name)
			continue
		generated.extend(candidates[:max_steps_per_page])

	deduped: list[GeneratedStep] = []
	seen_keys: set[tuple[str, str, str, str, str]] = set()
	for step in generated:
		key = (step.page_name, step.action, step.field_name, step.strategy, step.locator)
		if key in seen_keys:
			continue
		seen_keys.add(key)
		deduped.append(step)

	return deduped, skipped_pages, page_candidates, candidate_index


def _build_open_step(page_name: str, page_url: str, workflow_name: str) -> GeneratedStep:
	return GeneratedStep(
		action="open",
		page_name=page_name,
		page_url=page_url,
		element_tag="body",
		field_name="",
		field_value="",
		strategy="",
		locator="",
		raw_event={
			"action": "open",
			"url": page_url,
			"tag": "body",
			"text": "",
			"value": "",
			"locators": {},
			"source": {
				"type": "ai_workflow",
				"workflow_name": workflow_name,
				"page_name": page_name,
			},
		},
		locators_raw={},
		validation="",
		steps_description=f"Open URL {page_url} for workflow '{workflow_name}' on page '{page_name}'",
		page_title=page_name,
		source_row_id=None,
	)


def _resolve_source_records_by_name(cur, page_sequence: list[dict[str, str]]) -> None:
	"""Match page_sequence card names against session_meta.record_name and populate source_record_id."""
	names = [_clean_text(item.get("page_name")) for item in page_sequence if _clean_text(item.get("page_name"))]
	if not names:
		return
	placeholders = ",".join(["%s"] * len(names))
	cur.execute(
		f"""
		SELECT record_id::text, record_name
		FROM session_meta
		WHERE record_name IN ({placeholders})
		  AND folder_name != 'AI Gen'
		ORDER BY created_at DESC
		""",
		names,
	)
	name_to_id: dict[str, str] = {}
	for row in cur.fetchall():
		# Keep first match per name (most recent due to ORDER BY)
		if row[1] not in name_to_id:
			name_to_id[row[1]] = row[0]
	for item in page_sequence:
		page_name = _clean_text(item.get("page_name"))
		if page_name and page_name in name_to_id and not _clean_text(item.get("source_record_id")):
			item["source_record_id"] = name_to_id[page_name]
			item["source_record_name"] = page_name


def _build_generated_steps_from_source_records(
	cur,
	*,
	page_sequence: list[dict[str, str]],
	workflow_name: str,
) -> tuple[list[GeneratedStep], list[str]]:
	generated: list[GeneratedStep] = []
	skipped_pages: list[str] = []

	for page in page_sequence:
		source_record_id = _clean_text(page.get("source_record_id"))
		if not source_record_id:
			skipped_pages.append(_clean_text(page.get("page_name")) or "Untitled page")
			continue

		source_steps = _load_source_record_steps(cur, source_record_id)
		if not source_steps:
			# Fallback: find another record with the same name that has steps
			source_record_name = _clean_text(page.get("source_record_name")) or _clean_text(page.get("page_name"))
			if source_record_name:
				source_steps = _find_source_steps_by_name(cur, source_record_name, exclude_id=source_record_id)
			if not source_steps:
				skipped_pages.append(source_record_name or source_record_id)
				continue

		for row in source_steps:
			generated.append(_build_step_from_source_record_row(row, workflow_name=workflow_name))

	return generated, skipped_pages


def _find_source_steps_by_name(cur, record_name: str, exclude_id: str = "") -> list[dict[str, Any]]:
	"""Find steps from the most recent record matching the name that actually has steps."""
	cur.execute(
		"""
		SELECT m.record_id::text
		FROM session_meta m
		WHERE m.record_name = %s
		  AND m.record_id::text != %s
		  AND COALESCE(m.folder_name, '') != 'AI Gen'
		  AND EXISTS (SELECT 1 FROM steps s WHERE s.record_id = m.record_id)
		ORDER BY m.created_at DESC
		LIMIT 1
		""",
		[record_name, exclude_id or ""],
	)
	row = cur.fetchone()
	if not row:
		return []
	return _load_source_record_steps(cur, row[0])


def _load_source_record_steps(cur, record_id: str) -> list[dict[str, Any]]:
	cur.execute(
		"""
		SELECT s.id,
		       s.step_no,
		       s.action,
		       COALESCE(s.page_url, '') AS page_url,
		       COALESCE(s.element_tag, '') AS element_tag,
		       COALESCE(d.field_name, s.field_name, '') AS field_name,
		       COALESCE(d.value, s.field_value, '') AS field_value,
		       COALESCE(l.strategy, s.strategy, '') AS strategy,
		       COALESCE(l.locator, s.locator, '') AS locator,
		       s.raw_event,
		       COALESCE(s.locators_raw, '{}'::jsonb) AS locators_raw,
		       COALESCE(s.validation, '') AS validation,
		       COALESCE(s.steps_description, '') AS steps_description,
		       COALESCE(s.page_title, '') AS page_title,
		       l.pos_x,
		       l.pos_y,
		       COALESCE(m.record_name, s.record_id::text) AS record_name
		FROM steps s
		LEFT JOIN data d ON d.id = s.data_id
		LEFT JOIN locators l ON l.id = s.locator_id
		LEFT JOIN session_meta m ON m.record_id = s.record_id
		WHERE s.record_id = %s
		ORDER BY s.step_no
		""",
		[record_id],
	)
	columns = [
		"id", "step_no", "action", "page_url", "element_tag", "field_name", "field_value",
		"strategy", "locator", "raw_event", "locators_raw", "validation", "steps_description",
		"page_title", "pos_x", "pos_y", "record_name",
	]
	return [dict(zip(columns, row)) for row in cur.fetchall()]


def _build_step_from_source_record_row(row: dict[str, Any], *, workflow_name: str) -> GeneratedStep:
	raw_event = _json_dict(row.get("raw_event"))
	locators_raw = _json_dict(row.get("locators_raw"))
	source_record_name = _clean_text(row.get("record_name"))
	raw_event["source"] = {
		"type": "session_record",
		"workflow_name": workflow_name,
		"record_name": source_record_name,
		"step_no": row.get("step_no"),
	}
	return GeneratedStep(
		action=_clean_text(row.get("action")),
		page_name=_clean_text(row.get("page_title")) or source_record_name,
		page_url=_clean_text(row.get("page_url")),
		element_tag=_clean_text(row.get("element_tag")),
		field_name=_clean_text(row.get("field_name")),
		field_value=_clean_text(row.get("field_value")),
		strategy=_clean_text(row.get("strategy")),
		locator=_clean_text(row.get("locator")),
		raw_event=raw_event,
		locators_raw=locators_raw,
		validation=_clean_text(row.get("validation")),
		steps_description=_clean_text(row.get("steps_description")) or f"Generated from source session '{source_record_name}' step {row.get('step_no')}",
		page_title=_clean_text(row.get("page_title")) or source_record_name,
		source_row_id=row.get("id"),
		pos_x=row.get("pos_x"),
		pos_y=row.get("pos_y"),
	)


def _page_candidates(
	rows: list[dict[str, Any]],
	page_name: str,
	page_url: str,
	next_page: dict[str, str] | None,
	workflow_name: str,
	connection_hint: dict[str, Any] | None,
) -> tuple[list[GeneratedStep], dict[str, Any], list[GeneratedStep]]:
	actionable: list[GeneratedStep] = []
	click_candidates: list[GeneratedStep] = []
	input_candidates: list[GeneratedStep] = []

	for row in rows:
		step = _build_step_from_databank_row(row, page_name=page_name, page_url=page_url, workflow_name=workflow_name)
		if not step:
			continue
		actionable.append(step)
		if step.action in {"input", "change"}:
			input_candidates.append(step)
		else:
			click_candidates.append(step)

	candidate_payload = {
		"page_name": page_name,
		"page_url": page_url,
		"next_page_name": next_page.get("page_name") if next_page else "",
		"next_page_url": next_page.get("page_url") if next_page else "",
		"connection_hint": connection_hint or {},
		"candidates": [_serialize_candidate(step) for step in actionable],
	}

	if not actionable:
		return [], candidate_payload, []

	selected: list[GeneratedStep] = []
	selected.extend(input_candidates[:4])

	transition = _pick_transition_step(click_candidates, next_page, connection_hint)
	if transition is not None:
		selected.append(transition)
	elif click_candidates:
		selected.append(click_candidates[0])

	if not selected:
		selected.append(actionable[0])

	return selected, candidate_payload, actionable


def _build_connection_hints(workflow: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
	hints: dict[tuple[str, str], dict[str, Any]] = {}
	for connection in workflow.get("page_connections") or []:
		if not isinstance(connection, dict):
			continue
		key = (
			_normalize_page_name(connection.get("from_page_name")),
			_normalize_page_name(connection.get("to_page_name")),
		)
		hints[key] = _normalize_connection_hint(connection)

	workflow_payload = workflow.get("workflow_payload") if isinstance(workflow.get("workflow_payload"), dict) else {}
	for relationship in workflow_payload.get("element_relationships") or []:
		if not isinstance(relationship, dict):
			continue
		key = (
			_normalize_page_name(relationship.get("from_page_name")),
			_normalize_page_name(relationship.get("to_page_name")),
		)
		merged = dict(hints.get(key) or {})
		merged.update(_normalize_connection_hint(relationship))
		hints[key] = merged

	return hints


def _normalize_connection_hint(connection: dict[str, Any]) -> dict[str, Any]:
	return {
		"from_element_id": connection.get("from_element_id"),
		"from_element_label": _clean_text(connection.get("from_element_label")),
		"from_element_strategy": _clean_text(connection.get("from_element_strategy")),
		"from_element_locator": _clean_text(connection.get("from_element_locator")),
		"to_element_id": connection.get("to_element_id"),
		"to_element_label": _clean_text(connection.get("to_element_label")),
		"to_element_strategy": _clean_text(connection.get("to_element_strategy")),
		"to_element_locator": _clean_text(connection.get("to_element_locator")),
	}


def _connection_hint_for_pages(
	connection_hints: dict[tuple[str, str], dict[str, Any]],
	page_name: str,
	next_page: dict[str, str] | None,
) -> dict[str, Any] | None:
	if not next_page:
		return None
	return connection_hints.get((_normalize_page_name(page_name), _normalize_page_name(next_page.get("page_name"))))


def _serialize_candidate(step: GeneratedStep) -> dict[str, Any]:
	event = step.raw_event if isinstance(step.raw_event, dict) else {}
	return {
		"source_row_id": step.source_row_id,
		"page_name": step.page_name,
		"page_url": step.page_url,
		"action": step.action,
		"element_tag": step.element_tag,
		"field_name": step.field_name,
		"field_value": step.field_value,
		"strategy": step.strategy,
		"locator": step.locator,
		"text": _clean_text(event.get("text")),
		"href": _clean_text(event.get("href")),
		"href_raw": _clean_text(event.get("href_raw")),
		"title": _clean_text(event.get("title")),
		"aria_label": _clean_text(event.get("ariaLabel") or event.get("aria_label")),
		"name": _clean_text(event.get("name")),
		"id": _clean_text(event.get("id")),
		"placeholder": _clean_text(event.get("placeholder")),
		"steps_description": step.steps_description,
	}


def _llm_select_generated_steps(
	*,
	workflow_name: str,
	workflow: dict[str, Any],
	page_sequence: list[dict[str, str]],
	page_candidates: list[dict[str, Any]],
	candidate_index: dict[tuple[str, int], GeneratedStep],
	deterministic_steps: list[GeneratedStep],
	ollama_api: str,
	model: str,
	timeout: int,
) -> tuple[list[GeneratedStep] | None, str]:
	if not page_candidates:
		return None, ""

	prompt = _build_llm_selection_prompt(
		workflow_name=workflow_name,
		workflow=workflow,
		page_sequence=page_sequence,
		page_candidates=page_candidates,
		deterministic_steps=deterministic_steps,
	)

	try:
		response = requests.post(
			_ollama_generate_url(ollama_api),
			json={
				"model": model or DEFAULT_LLM_MODEL,
				"prompt": prompt,
				"stream": False,
				"format": "json",
			},
			timeout=timeout,
		)
		response.raise_for_status()
		data = response.json() if response.content else {}
		payload_text = str(data.get("response") or "").strip()
		parsed = _extract_json_payload(payload_text)
		selected = _materialize_llm_steps(parsed, candidate_index, deterministic_steps)
		if selected:
			return selected, _clean_text(parsed.get("notes"))
	except Exception as exc:
		return None, f"LLM fallback to deterministic selection: {exc}"

	return None, "LLM returned no valid step selection; used deterministic selection."


def _build_llm_selection_prompt(
	*,
	workflow_name: str,
	workflow: dict[str, Any],
	page_sequence: list[dict[str, str]],
	page_candidates: list[dict[str, Any]],
	deterministic_steps: list[GeneratedStep],
) -> str:
	workflow_summary = {
		"workflow_name": workflow_name,
		"page_sequence": page_sequence,
		"page_connections": workflow.get("page_connections") or [],
		"candidate_pages": page_candidates,
		"deterministic_steps": [_serialize_candidate(step) for step in deterministic_steps],
	}
	return (
		"You are choosing browser automation steps for a generated test case. "
		"Use the workflow route from ai_workflow and the candidate elements from ai_databank. "
		"Pick a minimal but complete route from the first page to later pages. "
		"Always keep the first open step from the deterministic plan. "
		"Prefer exact href matches, anchor links, button text that matches the next page, and realistic input entries on destination pages. "
		"Return strict JSON only with this shape: "
		"{\"selected_steps\":[{\"page_name\":\"...\",\"source_row_id\":123,\"field_value\":\"optional override\"}],\"notes\":\"short reason\"}. "
		"Do not invent source_row_id values. Only select from the candidate_pages list.\n\n"
		+ json.dumps(workflow_summary, ensure_ascii=True)
	)


def _extract_json_payload(payload_text: str) -> dict[str, Any]:
	if not payload_text:
		return {}
		
	try:
		parsed = json.loads(payload_text)
		return parsed if isinstance(parsed, dict) else {}
	except Exception:
		match = re.search(r"\{.*\}", payload_text, re.DOTALL)
		if not match:
			return {}
		try:
			parsed = json.loads(match.group(0))
			return parsed if isinstance(parsed, dict) else {}
		except Exception:
			return {}


def _materialize_llm_steps(
	parsed: dict[str, Any],
	candidate_index: dict[tuple[str, int], GeneratedStep],
	deterministic_steps: list[GeneratedStep],
) -> list[GeneratedStep] | None:
	selected_steps = parsed.get("selected_steps")
	if not isinstance(selected_steps, list):
		return None

	open_steps = [step for step in deterministic_steps if step.action == "open"]
	materialized: list[GeneratedStep] = list(open_steps)
	seen_keys: set[tuple[str, int]] = set()

	for item in selected_steps:
		if not isinstance(item, dict):
			continue
		page_name = _clean_text(item.get("page_name"))
		row_id = item.get("source_row_id")
		if not page_name or row_id is None:
			continue
		try:
			row_id = int(row_id)
		except (TypeError, ValueError):
			continue
		key = (page_name, row_id)
		if key in seen_keys or key not in candidate_index:
			continue
		step = candidate_index[key]
		field_override = _clean_text(item.get("field_value"))
		if field_override and step.action in {"input", "change"}:
			step = _clone_step_with_value(step, field_override)
		materialized.append(step)
		seen_keys.add(key)

	if len(materialized) <= len(open_steps):
		return None
	return materialized


def _clone_step_with_value(step: GeneratedStep, field_value: str) -> GeneratedStep:
	raw_event = dict(step.raw_event or {})
	raw_event["value"] = field_value
	return GeneratedStep(
		action=step.action,
		page_name=step.page_name,
		page_url=step.page_url,
		element_tag=step.element_tag,
		field_name=step.field_name,
		field_value=field_value,
		strategy=step.strategy,
		locator=step.locator,
		raw_event=raw_event,
		locators_raw=dict(step.locators_raw or {}),
		validation=step.validation,
		steps_description=step.steps_description,
		page_title=step.page_title,
		source_row_id=step.source_row_id,
	)


def _build_step_from_databank_row(
	row: dict[str, Any],
	*,
	page_name: str,
	page_url: str,
	workflow_name: str,
) -> GeneratedStep | None:
	locator_property = row.get("locator_property") or {}
	locator_block = locator_property.get("locators") if isinstance(locator_property.get("locators"), dict) else {}

	tag_name = _clean_text(locator_property.get("tag_name") or locator_property.get("tag") or row.get("element_type"))
	input_type = _clean_text(locator_property.get("inputType") or locator_property.get("type")).lower()
	element_role = _clean_text(locator_property.get("role")).lower()
	action = _infer_action(tag_name=tag_name, element_type=row.get("element_type"), input_type=input_type, role=element_role)
	locator_choice = _choose_locator(locator_property, action=action, tag_name=tag_name)
	if not locator_choice and action != "open":
		return None
	field_name = _infer_field_name(locator_property, row)
	field_value = _infer_field_value(locator_property, field_name=field_name, action=action, input_type=input_type)
	element_text = _clean_text(locator_property.get("text") or locator_property.get("label") or locator_property.get("title"))
	page_title = page_name
	validation = _clean_text(locator_property.get("validation_hint"))
	raw_href_value = _clean_text(
		locator_property.get("href")
		or locator_block.get("href")
		or locator_property.get("link")
		or locator_property.get("url")
	)
	href_value = _normalize_href(raw_href_value, page_url or _clean_text(row.get("page_url")))
	title_value = _clean_text(locator_property.get("title") or locator_property.get("text"))
	aria_value = _clean_text(
		locator_property.get("ariaLabel")
		or locator_property.get("aria_label")
		or locator_property.get("label")
	)

	locators_raw = {item["strategy"]: item["locator"] for item in _ordered_locators(locator_property)}
	if raw_href_value and "href" not in locators_raw:
		locators_raw["href"] = raw_href_value
	raw_event = {
		"id": _clean_text(locator_property.get("id")),
		"name": _clean_text(locator_property.get("name")),
		"tag": tag_name,
		"text": element_text,
		"value": field_value if action in {"input", "change"} else _clean_text(locator_property.get("value")),
		"url": page_url or _clean_text(row.get("page_url")),
		"action": action,
		"placeholder": _clean_text(locator_property.get("placeholder")),
		"inputType": input_type,
		"role": element_role,
		"href_raw": raw_href_value,
		"href": href_value,
		"title": title_value,
		"ariaLabel": aria_value,
		"locators": locators_raw,
		"source": {
			"type": "ai_databank",
			"row_id": row.get("id"),
			"workflow_name": workflow_name,
			"page_name": page_name,
		},
	}

	# Extract element position from the scraper-captured bounding rect
	_bounds = locator_property.get("bounds") or {}
	_pos_x: float | None = None
	_pos_y: float | None = None
	if isinstance(_bounds, dict):
		try:
			_left = _bounds.get("left")
			_top  = _bounds.get("top")
			_w    = _bounds.get("width", 0) or 0
			_h    = _bounds.get("height", 0) or 0
			if _left is not None and _top is not None:
				_pos_x = round(float(_left) + float(_w) / 2, 1)
				_pos_y = round(float(_top)  + float(_h) / 2, 1)
		except (TypeError, ValueError):
			pass
	if _pos_x is not None:
		raw_event["pos_x"] = _pos_x
		raw_event["pos_y"] = _pos_y

	description_parts = [
		f"Generated from workflow '{workflow_name}'",
		f"page '{page_name}'",
		f"using ai_databank row {row.get('id')}",
	]
	if field_name:
		description_parts.append(f"field '{field_name}'")
	elif element_text:
		description_parts.append(f"element '{element_text[:60]}'")

	return GeneratedStep(
		action=action,
		page_name=page_name,
		page_url=page_url or _clean_text(row.get("page_url")),
		element_tag=tag_name,
		field_name=field_name,
		field_value=field_value,
		strategy=locator_choice["strategy"],
		locator=locator_choice["locator"],
		raw_event=raw_event,
		locators_raw=locators_raw,
		validation=validation,
		steps_description=" · ".join(part for part in description_parts if part),
		page_title=page_title,
		source_row_id=row.get("id"),
		pos_x=_pos_x,
		pos_y=_pos_y,
	)


def _pick_transition_step(
	click_candidates: list[GeneratedStep],
	next_page: dict[str, str] | None,
	connection_hint: dict[str, Any] | None,
) -> GeneratedStep | None:
	if not click_candidates:
		return None
	if not next_page:
		return click_candidates[0]

	next_page_name = _clean_text(next_page.get("page_name")).lower()
	next_page_url = _clean_text(next_page.get("page_url")).lower()
	best_step: GeneratedStep | None = None
	best_score = -1

	for step in click_candidates:
		score = 0
		event = step.raw_event
		hinted_id = connection_hint.get("from_element_id") if isinstance(connection_hint, dict) else None
		hinted_strategy = _clean_text(connection_hint.get("from_element_strategy")) if isinstance(connection_hint, dict) else ""
		hinted_locator = _clean_text(connection_hint.get("from_element_locator")) if isinstance(connection_hint, dict) else ""
		hinted_label = _clean_text(connection_hint.get("from_element_label")).lower() if isinstance(connection_hint, dict) else ""
		next_tokens = [token for token in re.split(r"[^a-z0-9]+", next_page_name) if len(token) >= 2]
		url_tokens = _url_tokens(next_page_url)
		next_path = next_page_url.rstrip("/").split("/")[-1]
		text_value = _clean_text(event.get("text")).lower()
		name_value = _clean_text(event.get("name")).lower()
		id_value = _clean_text(event.get("id")).lower()
		placeholder_value = _clean_text(event.get("placeholder")).lower()
		role_value = _clean_text(event.get("role")).lower()
		href_raw_value = _clean_text(event.get("href_raw")).lower()
		href_value = _clean_text(event.get("href")).lower()
		title_value = _clean_text(event.get("title")).lower()
		aria_value = _clean_text(event.get("ariaLabel") or event.get("aria_label")).lower()
		strategy_value = _clean_text(step.strategy).lower()
		tag_value = _clean_text(step.element_tag).lower()
		haystacks = [
			text_value,
			placeholder_value,
			name_value,
			id_value,
			href_raw_value,
			href_value,
			title_value,
			aria_value,
			_clean_text(step.locator).lower(),
		]
		if hinted_id is not None and step.source_row_id is not None and str(hinted_id) == str(step.source_row_id):
			score += 500
		if hinted_strategy and hinted_strategy.lower() == strategy_value:
			score += 160
		if hinted_locator:
			hinted_locator_lc = hinted_locator.lower()
			locator_value_lc = _clean_text(step.locator).lower()
			if hinted_locator_lc == locator_value_lc:
				score += 320
			elif hinted_locator_lc in locator_value_lc or locator_value_lc in hinted_locator_lc:
				score += 140
		if hinted_label and any(hinted_label == value for value in haystacks if value):
			score += 120
		elif hinted_label and any(hinted_label in value for value in haystacks if value):
			score += 60
		if next_page_name:
			if any(next_page_name == value for value in haystacks if value):
				score += 80
			elif any(next_page_name in value for value in haystacks if value):
				score += 40
			score += sum(10 for token in next_tokens if any(token == value for value in haystacks if value))
			score += sum(6 for token in next_tokens if any(token in value for value in haystacks if value))
			if text_value in {"next", "continue", "submit", "save", "open", "view"}:
				score += 4
		if next_page_url:
			if next_page_url == href_value or next_page_url == _clean_text(step.locator).lower():
				score += 120
			elif next_page_url in step.locator.lower() or next_page_url in href_value:
				score += 60
			if href_raw_value in {"/" + next_path, next_path} and next_path:
				score += 140
			elif next_path and next_path in href_raw_value:
				score += 80
			path_tail = next_page_url.rstrip("/").split("/")[-1]
			if path_tail and any(path_tail in value for value in haystacks if value):
				score += 20
			score += sum(12 for token in url_tokens if any(token == value for value in haystacks if value))
			score += sum(7 for token in url_tokens if any(token in value for value in haystacks if value))
		if strategy_value == "href":
			score += 35
		if strategy_value in {"linkText", "partialLinkText", "text"}:
			score += 20
		if tag_value == "a":
			score += 25
		if role_value in {"link", "button", "menuitem", "tab"}:
			score += 5
		if title_value and next_page_name and next_page_name in title_value:
			score += 12
		if aria_value and next_page_name and next_page_name in aria_value:
			score += 12
		if score > best_score:
			best_score = score
			best_step = step

	return best_step or click_candidates[0]


def _url_tokens(value: str) -> list[str]:
	cleaned = _clean_text(value).lower()
	if not cleaned:
		return []
	cleaned = re.sub(r"^https?://", "", cleaned)
	return [token for token in re.split(r"[^a-z0-9]+", cleaned) if len(token) >= 2]



def _choose_locator(locator_property: dict[str, Any], *, action: str, tag_name: str) -> dict[str, str] | None:
	ordered = _ordered_locators(locator_property)
	if not ordered:
		return None
	preferred = _preferred_locator_strategies(action=action, tag_name=tag_name)
	for strategy in preferred:
		for item in ordered:
			if item["strategy"] == strategy and _clean_text(item["locator"]):
				return item
	return ordered[0]


def _preferred_locator_strategies(*, action: str, tag_name: str) -> tuple[str, ...]:
	tag = _clean_text(tag_name).lower()
	if action in {"input", "change"}:
		return ("id", "name", "placeholder", "css", "xpath", "text", "ariaLabel")
	if tag == "a":
		return ("href", "linkText", "partialLinkText", "text", "css", "xpath", "id", "name")
	if tag == "button":
		return ("id", "name", "text", "css", "xpath", "ariaLabel", "title")
	return ("id", "name", "text", "css", "xpath", "href", "ariaLabel")


def _ordered_locators(locator_property: dict[str, Any]) -> list[dict[str, str]]:
	explicit = locator_property.get("ordered_locators")
	ordered: list[dict[str, str]] = []
	seen: set[tuple[str, str]] = set()

	if isinstance(explicit, list):
		for item in explicit:
			if not isinstance(item, dict):
				continue
			strategy = _clean_text(item.get("strategy"))
			locator = _clean_text(item.get("locator") or item.get("prepared_locator"))
			if not strategy or not locator:
				continue
			key = (strategy, locator)
			if key in seen:
				continue
			seen.add(key)
			ordered.append({"strategy": strategy, "locator": locator})

	locators = locator_property.get("locators") if isinstance(locator_property.get("locators"), dict) else {}
	for strategy in _LOCATOR_STRATEGY_ORDER:
		raw_locator = _clean_text(locators.get(strategy))
		if not raw_locator:
			continue
		prepared = _prepare_locator(strategy, raw_locator)
		key = (strategy, prepared)
		if key in seen:
			continue
		seen.add(key)
		ordered.append({"strategy": strategy, "locator": prepared})

	return ordered


def _prepare_locator(strategy: str, locator: str) -> str:
	wrapper = _LOCATOR_WRAP_MAP.get(strategy)
	if wrapper is None:
		return locator
	return wrapper(locator)


def _normalize_href(value: Any, base_url: str) -> str:
	cleaned = _clean_text(value)
	if not cleaned:
		return ""
	if cleaned.startswith("javascript:"):
		return ""
	if cleaned.startswith("http://") or cleaned.startswith("https://"):
		return cleaned
	if base_url:
		return urljoin(base_url, cleaned)
	return cleaned


def _infer_action(*, tag_name: str, element_type: Any, input_type: str, role: str) -> str:
	tag = _clean_text(tag_name).lower()
	element = _clean_text(element_type).lower()
	normalized_input_type = _clean_text(input_type).lower()

	if tag == "select":
		return "change"
	if tag == "textarea":
		return "input"
	if tag == "input":
		if normalized_input_type in _CLICK_INPUT_TYPES:
			return "click"
		if normalized_input_type in _TEXT_ENTRY_INPUT_TYPES:
			return "change"
	if tag in {"button", "a", "summary"}:
		return "click"
	if role in {"button", "link", "menuitem", "tab"}:
		return "click"
	if element in {"button", "link", "anchor", "checkbox", "radio", "submit"}:
		return "click"
	if element in {"input", "textfield", "textarea", "select"}:
		return "change"
	return "click"


def _infer_field_name(locator_property: dict[str, Any], row: dict[str, Any]) -> str:
	candidates = [
		locator_property.get("name"),
		locator_property.get("id"),
		locator_property.get("ariaLabel"),
		locator_property.get("aria_label"),
		locator_property.get("placeholder"),
		locator_property.get("label"),
		locator_property.get("text"),
		row.get("element_type"),
	]
	for candidate in candidates:
		cleaned = _clean_text(candidate)
		if cleaned:
			return cleaned[:120]
	return "element"


def _infer_field_value(locator_property: dict[str, Any], *, field_name: str, action: str, input_type: str) -> str:
	if action not in {"input", "change"}:
		if _clean_text(input_type).lower() in {"checkbox", "radio"}:
			return "true"
		return ""

	direct_candidates = [
		locator_property.get("sample_value"),
		locator_property.get("value"),
		locator_property.get("text"),
		locator_property.get("default_value"),
	]
	for candidate in direct_candidates:
		cleaned = _clean_text(candidate)
		if cleaned:
			return cleaned[:200]

	normalized = field_name.lower()
	input_kind = _clean_text(input_type).lower()

	if "email" in normalized or input_kind == "email":
		return "test@example.com"
	if "password" in normalized or input_kind == "password":
		return "Password123!"
	if "phone" in normalized or "mobile" in normalized or input_kind == "tel":
		return "0123456789"
	if "date" in normalized or input_kind in {"date", "datetime-local", "month", "time", "week"}:
		return "2026-03-31"
	if "search" in normalized or input_kind == "search":
		return "sample search"
	if "url" in normalized or input_kind == "url":
		return "https://example.com"
	if "number" in normalized or input_kind == "number":
		return "123"
	if "name" in normalized:
		return "Sample Name"
	if "city" in normalized:
		return "Sample City"
	if "address" in normalized:
		return "Sample Address"
	return "Sample Value"


def _insert_data_row(cur, record_id: uuid.UUID, step_no: int, step: GeneratedStep, folder_name: str) -> int | None:
	cur.execute(
		"""
		INSERT INTO data (record_id, step_no, field_name, value, folder_name)
		VALUES (%s, %s, %s, %s, %s)
		RETURNING id
		""",
		[str(record_id), step_no, step.field_name or None, step.field_value or None, folder_name or None],
	)
	row = cur.fetchone()
	return int(row[0]) if row and row[0] is not None else None


def _insert_locator_row(cur, record_id: uuid.UUID, step_no: int, step: GeneratedStep, folder_name: str) -> int | None:
	if not step.strategy or not step.locator:
		return None

	# Insert primary locator
	cur.execute(
		"""
		INSERT INTO locators (record_id, step_no, strategy, locator, is_primary, locator_rank, pos_x, pos_y, folder_name)
		VALUES (%s, %s, %s, %s, TRUE, 1, %s, %s, %s)
		RETURNING id
		""",
		[str(record_id), step_no, step.strategy, step.locator, step.pos_x, step.pos_y, folder_name or None],
	)
	row = cur.fetchone()
	primary_id = int(row[0]) if row and row[0] is not None else None

	# Insert additional locators from locators_raw
	if step.locators_raw:
		rank = 2
		for strategy in _LOCATOR_STRATEGY_ORDER:
			if strategy == step.strategy:
				continue
			value = step.locators_raw.get(strategy)
			if not value or not str(value).strip():
				continue
			cur.execute(
				"""
				INSERT INTO locators (record_id, step_no, strategy, locator, is_primary, locator_rank, pos_x, pos_y, folder_name)
				VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s)
				""",
				[str(record_id), step_no, strategy, str(value).strip(), rank, step.pos_x, step.pos_y, folder_name or None],
			)
			rank += 1

	return primary_id


def _resolve_folder_ids(cur, folder_name: str) -> dict[str, Any]:
	result = {"parent_folder_id": None, "sub_folder_id": None, "end_folder_id": None}
	if not folder_name:
		return result

	parts = [part.strip() for part in str(folder_name).split("/") if part.strip()]
	if len(parts) >= 1:
		cur.execute("SELECT parent_folder_id FROM parent_folders WHERE parent_folder = %s", [parts[0]])
		row = cur.fetchone()
		if row:
			result["parent_folder_id"] = row[0]

	if len(parts) >= 2 and result["parent_folder_id"]:
		cur.execute(
			"""
			SELECT sub_folder_id
			FROM sub_folders
			WHERE sub_folder = %s AND sub_folder_parent = %s
			""",
			[parts[1], result["parent_folder_id"]],
		)
		row = cur.fetchone()
		if row:
			result["sub_folder_id"] = row[0]

	if len(parts) >= 3 and result["sub_folder_id"]:
		cur.execute(
			"""
			SELECT end_folder_id
			FROM end_folders
			WHERE end_folder = %s AND end_folder_parent = %s
			""",
			[parts[2], result["sub_folder_id"]],
		)
		row = cur.fetchone()
		if row:
			result["end_folder_id"] = row[0]

	return result


def _next_file_order(cur, folder_name: str) -> int:
	if folder_name:
		cur.execute("SELECT COALESCE(MAX(file_order), 0) + 1 FROM steps WHERE folder_name = %s", [folder_name])
	else:
		cur.execute("SELECT 1")
	row = cur.fetchone()
	return int(row[0] or 1) if row else 1


def _first_page_url(rows: list[dict[str, Any]]) -> str:
	for row in rows:
		candidate = _clean_text(row.get("page_url"))
		if candidate:
			return candidate
	return ""


def _normalize_page_name(value: Any) -> str:
	cleaned = _clean_text(value)
	return cleaned or "Untitled page"


def _json_dict(value: Any) -> dict[str, Any]:
	if isinstance(value, dict):
		return value
	if isinstance(value, str):
		try:
			decoded = json.loads(value)
		except Exception:
			return {}
		return decoded if isinstance(decoded, dict) else {}
	return {}


def _json_list(value: Any) -> list[Any]:
	if isinstance(value, list):
		return value
	if isinstance(value, str):
		try:
			decoded = json.loads(value)
		except Exception:
			return []
		return decoded if isinstance(decoded, list) else []
	return []


def _clean_text(value: Any) -> str:
	if value is None:
		return ""
	return re.sub(r"\s+", " ", str(value)).strip()
