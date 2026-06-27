"""
ai_agent.py — Chatbot Agent
----------------------------------
An LLM-powered chatbot agent for the WebConX Automation Platform.

Capabilities:
  1. List / search test scripts (steps table)
  2. Download test script steps as CSV, PDF, or DOC
  3. Create a project folder (parent_folders table)
  4. Update test script data values (data table)

Usage:
  python ai_agent.py                     # interactive chat
  python ai_agent.py --chat              # interactive chat (explicit)
  python ai_agent.py --list              # list test scripts

Environment variables:
  PGDATABASE, PGUSER, PGPASSWORD, PGHOST, PGPORT
  OLLAMA_API, LLM_MODEL, LLM_MAX_TOKENS
"""

import csv
import datetime
import difflib
import io
import json
import os
import re
import sys
import textwrap
import uuid as _uuid
from typing import Optional

import psycopg2
import psycopg2.extras
import psycopg2.pool
import requests

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DB_CONFIG = {
    "dbname":   os.getenv("PGDATABASE", "automation_db"),
    "user":     os.getenv("PGUSER",     "postgres"),
    "password": os.getenv("PGPASSWORD", "password"),
    "host":     os.getenv("PGHOST",     "localhost"),
    "port":     os.getenv("PGPORT",     "5432"),
}

_pool: psycopg2.pool.ThreadedConnectionPool | None = None


def _get_pool() -> psycopg2.pool.ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = psycopg2.pool.ThreadedConnectionPool(1, 5, **DB_CONFIG)
    return _pool


def _get_conn():
    return _get_pool().getconn()


def _put_conn(conn, *, discard: bool = False) -> None:
    try:
        _get_pool().putconn(conn, close=discard)
    except Exception:
        pass


def _tool_result(text: str, **extra) -> dict:
    payload = {"text": text}
    payload.update(extra)
    return payload


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first JSON object from an LLM response."""
    if not text:
        return None

    fenced_match = re.search(r'```json\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
    if fenced_match:
        try:
            payload = json.loads(fenced_match.group(1).strip())
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    object_match = re.search(r'\{.*\}', text, re.DOTALL)
    if object_match:
        try:
            payload = json.loads(object_match.group(0).strip())
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            payload = json.loads(stripped)
            if isinstance(payload, dict):
                return payload
        except json.JSONDecodeError:
            pass

    return None


OLLAMA_API     = os.getenv("OLLAMA_API",     "http://localhost:11434/api")
LLM_MODEL      = os.getenv("LLM_MODEL",      "llama3")
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "4096"))

# ═══════════════════════════════════════════════════════════════════════════
# TOOL FUNCTIONS — each returns a dict with "text" (user-facing message)
# and optionally "download" (dict with url/filename/content/content_type).
# ═══════════════════════════════════════════════════════════════════════════


# (removed _available_scripts_hint – not-found messages are now kept brief)


def tool_list_sessions(limit: int = 20) -> dict:
    """List the most recent recorded test scripts."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.record_id,
                       COALESCE(m.record_name, '') AS record_name,
                       MIN(s.folder_name)          AS folder_name,
                       MIN(s.created_at)           AS started_at,
                       COUNT(DISTINCT s.step_no)   AS step_count
                FROM steps s
                LEFT JOIN session_meta m ON m.record_id = s.record_id
                GROUP BY s.record_id, m.record_name
                ORDER BY MIN(s.created_at) DESC
                LIMIT %s
            """, [limit])
            rows = cur.fetchall()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not rows:
        return {"text": "No test scripts found in the database.\n\n"
                        "**Suggestions:**\n"
                        "- Create a new test case: \"create test case <name>\"\n"
                        "- Create a project folder first: \"create project <name>\""}

    lines = ["| # | Test Script Name | Folder | Steps | Created |",
             "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        name = r["record_name"] or str(r["record_id"])[:8]
        folder = r["folder_name"] or "—"
        ts = str(r["started_at"])[:19] if r["started_at"] else "—"
        lines.append(f"| {i} | {name} | {folder} | {r['step_count']} | {ts} |")
    return {"text": "\n".join(lines)}


def tool_search_sessions(query: str) -> dict:
    """Search test scripts by name or folder."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.record_id,
                       COALESCE(m.record_name, '') AS record_name,
                       MIN(s.folder_name)          AS folder_name,
                       MIN(s.created_at)           AS started_at,
                       COUNT(DISTINCT s.step_no)   AS step_count
                FROM steps s
                LEFT JOIN session_meta m ON m.record_id = s.record_id
                WHERE ({name_clause}) OR s.folder_name ILIKE %s
                GROUP BY s.record_id, m.record_name
                ORDER BY MIN(s.created_at) DESC
                LIMIT 20
            """.format(name_clause=_name_ilike_clause(query, "m.record_name")[0]),
            _name_ilike_clause(query, "m.record_name")[1] + [f"%{query}%"])
            rows = cur.fetchall()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not rows:
        return _tool_result(
            f"No test scripts found matching \"{query}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** with a different keyword.",
            status="no_search_results",
            query=query,
            suggestions=["list scripts", "search <keyword>"],
        )

    lines = [f"**Found {len(rows)} test script(s) matching \"{query}\":**\n",
             "| # | Test Script Name | Record ID | Folder | Steps |",
             "|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        name = r["record_name"] or "—"
        rid = str(r["record_id"])[:8]
        folder = r["folder_name"] or "—"
        lines.append(f"| {i} | {name} | {rid}… | {folder} | {r['step_count']} |")
    return {"text": "\n".join(lines)}


def tool_list_projects() -> dict:
    """List available top-level projects from the project_folders registry."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                WITH registered_projects AS (
                    SELECT split_part(folder_name, '/', 1) AS project_name,
                           MIN(COALESCE(folder_order, 2147483647)) AS folder_order
                    FROM project_folders
                    WHERE TRIM(COALESCE(folder_name, '')) <> ''
                    GROUP BY split_part(folder_name, '/', 1)
                )
                SELECT rp.project_name,
                       rp.folder_order,
                       pf.author,
                       pf.public,
                       pf.is_baseline,
                       pf.created_at
                FROM registered_projects rp
                LEFT JOIN parent_folders pf ON pf.parent_folder = rp.project_name
                WHERE TRIM(COALESCE(rp.project_name, '')) <> ''
                ORDER BY rp.folder_order, LOWER(rp.project_name), rp.project_name
            """)
            rows = [r for r in cur.fetchall() if (r["project_name"] or "").strip()]
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not rows:
        return _tool_result(
            "No project folders found.\n\n"
            "**Suggestion:** Create one with: \"create project <name>\"",
            status="no_projects_found",
            suggestions=["create project <name>"],
        )

    lines = ["| # | Project Folder | Author | Public | Baseline | Created |",
             "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows, 1):
        ts = str(r["created_at"])[:10] if r["created_at"] else "—"
        lines.append(
            f"| {i} | {r['project_name'].strip()} | {r['author'] or '—'} "
            f"| {'Yes' if r['public'] else 'No'} "
            f"| {'Yes' if r['is_baseline'] else 'No'} | {ts} |"
        )
    return {"text": "\n".join(lines)}


def tool_create_project(folder_name: str, author: str = "admin") -> dict:
    """Create a new top-level project folder."""
    folder_name = folder_name.strip()
    if not folder_name:
        return _tool_result("Error: Folder name is required.", status="needs_folder_name", missing=["folder_name"])
    if folder_name.lower() in {"baseline", "unfiled", "recordings", ""}:
        return _tool_result(f"Error: \"{folder_name}\" is a reserved name.", status="reserved_folder_name", folder_name=folder_name)

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM parent_folders WHERE parent_folder = %s", [folder_name])
            if cur.fetchone():
                return _tool_result(f"Project folder \"{folder_name}\" already exists.", status="project_exists", folder_name=folder_name)

            folder_id = str(_uuid.uuid4())
            cur.execute("""
                INSERT INTO parent_folders
                    (parent_folder_id, parent_folder, author, public, is_baseline, created_at, last_updated)
                VALUES (%s, %s, %s, TRUE, FALSE, NOW(), NOW())
            """, [folder_id, folder_name, author])

            cur.execute("SELECT 1 FROM project_folders WHERE folder_name = %s", [folder_name])
            if cur.fetchone() is None:
                cur.execute(
                    "SELECT COALESCE(MAX(folder_order), 0) + 1 AS next_order FROM project_folders"
                )
                next_order = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO project_folders (folder_name, folder_order) VALUES (%s, %s)",
                    [folder_name, next_order],
                )
            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error creating project: {exc}"}
    else:
        _put_conn(conn)

    return {"text": f"Project folder **\"{folder_name}\"** created successfully."}


def _ensure_project_folder(folder_name: str, author: str = "admin") -> None:
    normalized_folder = (folder_name or "").strip()
    if not normalized_folder:
        return

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM parent_folders WHERE parent_folder = %s", [normalized_folder])
            if cur.fetchone() is None:
                folder_id = str(_uuid.uuid4())
                cur.execute(
                    """
                    INSERT INTO parent_folders
                        (parent_folder_id, parent_folder, author, public, is_baseline, created_at, last_updated)
                    VALUES (%s, %s, %s, TRUE, FALSE, NOW(), NOW())
                    """,
                    [folder_id, normalized_folder, author],
                )

            cur.execute("SELECT 1 FROM project_folders WHERE folder_name = %s", [normalized_folder])
            if cur.fetchone() is None:
                cur.execute("SELECT COALESCE(MAX(folder_order), 0) + 1 AS next_order FROM project_folders")
                next_order = cur.fetchone()[0]
                cur.execute(
                    "INSERT INTO project_folders (folder_name, folder_order) VALUES (%s, %s)",
                    [normalized_folder, next_order],
                )
            conn.commit()
    except Exception:
        conn.rollback()
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)


def _stem_for_search(word: str) -> str:
    """Return the shortest useful substring for an ILIKE search.

    Strips common verb suffixes so that action-verb queries like "entering"
    still match test-case names like "Enter Name".

    Examples:
      entering  → enter   (-ing)
      submitting → submit  (-ting with doubled consonant)
      populating → populat (-ing, stem is still a good substring)
      searched  → search  (-ed)
      names     → name    (trailing -s when not -ss)
    """
    w = word.lower()
    # Strip gerund -ing (length guard avoids mangling short words like "ring")
    if len(w) >= 6 and w.endswith("ing"):
        stem = w[:-3]
        # Un-double a doubled consonant: "submitt" → "submit", "sitt" → "sit"
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        return stem
    # Strip past-tense -ed
    if len(w) >= 5 and w.endswith("ed") and not w.endswith("seed"):
        stem = w[:-2]
        if len(stem) >= 3 and stem[-1] == stem[-2] and stem[-1] not in "aeiou":
            stem = stem[:-1]
        return stem
    # Strip plain plural -s (but not -ss, -us, -is)
    if len(w) >= 4 and w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _name_ilike_clause(name: str, column: str = "m.record_name"):
    """Build a WHERE clause that matches all words in *name* independently.

    Common filler words (test, case, script, …) are stripped so that
    "populate city test case" still matches "Populate Current City".
    Verb-form words are stemmed so "entering" matches "Enter Name".

    ``"Populate City"`` → ``column ILIKE '%Populate%' AND column ILIKE '%City%'``
    Returns (sql_fragment, params_list).
    """
    _STOP_WORDS = {"test", "case", "script", "scripts", "cases", "the",
                   "a", "an", "for", "of", "to", "from", "and", "or",
                   "steps", "step", "download", "delete", "show", "search",
                   "find", "get", "my", "please", "me", "it", "its",
                   # filler words from conversational queries
                   "related", "about", "any", "there", "file", "files",
                   "how", "what", "anything"}
    words = [w for w in name.split() if w.lower() not in _STOP_WORDS]
    if not words:
        # All words were stop-words; fall back to original string
        return f"{column} ILIKE %s", [f"%{name}%"]
    clauses = []
    params = []
    for w in words:
        stem = _stem_for_search(w)
        if stem != w.lower():
            # Use OR so both the original word and the stem can match
            clauses.append(f"({column} ILIKE %s OR {column} ILIKE %s)")
            params.extend([f"%{w}%", f"%{stem}%"])
        else:
            clauses.append(f"{column} ILIKE %s")
            params.append(f"%{w}%")
    return " AND ".join(clauses), params


def _parse_generate_workflow_request(user_message: str) -> Optional[dict]:
    text = re.sub(r"\s+", " ", str(user_message or "")).strip()
    if not text:
        return None

    lower = text.lower()
    if "generate" not in lower:
        return None
    # Accept common misspellings of "workflow"
    if not re.search(r"\b(?:workflow|wrokflow|workfow|worflow|worklow)\b", lower):
        return None

    # Normalize typos to "workflow" for pattern matching
    normalized_text = re.sub(r"\b(?:wrokflow|workfow|worflow|worklow)\b", "workflow", text, flags=re.IGNORECASE)

    workflow_query = ""
    patterns = [
        r"\bgenerate\b\s+(?:a\s+|an\s+|the\s+)?(.+?)\s+\bworkflow\b",
        r"\bgenerate\b\s+\bworkflow\b(?:\s+(.*))?$",
    ]
    for pattern in patterns:
        match = re.search(pattern, normalized_text, re.IGNORECASE)
        if not match:
            continue
        workflow_query = (match.group(1) or "").strip(" .,!?:;\"'")
        break

    workflow_query = re.sub(
        r"\b(test|case|script|from|for|in|inside|using|based|on|please|folder|ai|gen)\b",
        " ",
        workflow_query,
        flags=re.IGNORECASE,
    )
    workflow_query = re.sub(r"\s+", " ", workflow_query).strip()
    return {"workflow_query": workflow_query}


def _find_workflow_matches(workflow_query: str = "") -> list[dict]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cleaned = (workflow_query or "").strip()
            if cleaned:
                where_sql, params = _name_ilike_clause(cleaned, "workflow_name")
                cur.execute(
                    f"""
                    SELECT workflow_name, created_at, updated_at
                    FROM ai_workflow
                    WHERE {where_sql}
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, workflow_name
                    LIMIT 10
                    """,
                    params,
                )
            else:
                cur.execute(
                    """
                    SELECT workflow_name, created_at, updated_at
                    FROM ai_workflow
                    ORDER BY updated_at DESC NULLS LAST, created_at DESC NULLS LAST, workflow_name
                    LIMIT 10
                    """
                )
            rows = list(cur.fetchall())
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)
    return rows


def tool_generate_workflow_test_case(workflow_query: str = "", folder_name: str = "AI Gen", author: str = "admin") -> dict:
    matches = _find_workflow_matches(workflow_query)
    if not matches:
        if workflow_query:
            return _tool_result(
                f"No saved workflow matched \"{workflow_query}\". Open AI Databank Flow and save the workflow first.",
                status="workflow_not_found",
                workflow_query=workflow_query,
                suggestions=["Open AI Databank Flow and save the workflow", "Use the exact saved workflow name"],
            )
        return _tool_result(
            "No saved workflows were found. Open AI Databank Flow and save a workflow first.",
            status="no_workflows_found",
        )

    if workflow_query and len(matches) > 1:
        lines = [
            f"Multiple saved workflows matched \"{workflow_query}\". Please use a more specific workflow name.",
            "",
            "| # | Workflow | Updated |",
            "|---|---|---|",
        ]
        for index, item in enumerate(matches[:5], start=1):
            updated = str(item.get("updated_at") or item.get("created_at") or "")[:19] or "-"
            lines.append(f"| {index} | {item.get('workflow_name') or '-'} | {updated} |")
        return _tool_result(
            "\n".join(lines),
            status="ambiguous_workflow_name",
            workflow_query=workflow_query,
            matches=[item.get("workflow_name") for item in matches[:5]],
        )

    workflow_name = str(matches[0].get("workflow_name") or "").strip()
    if not workflow_name:
        return _tool_result("The matched workflow did not have a valid workflow_name.", status="workflow_missing_name")

    try:
        _ensure_project_folder(folder_name, author=author)
        from workflow_agent import WorkflowGenerationError, WorkflowNotFoundError, create_test_case_from_workflow

        result = create_test_case_from_workflow(
            workflow_name,
            folder_name=folder_name,
            author=author,
        )
    except WorkflowNotFoundError as exc:
        return _tool_result(str(exc), status="workflow_not_found", workflow_name=workflow_name)
    except WorkflowGenerationError as exc:
        return _tool_result(str(exc), status="workflow_generation_failed", workflow_name=workflow_name)
    except Exception as exc:
        return _tool_result(f"Error generating workflow test case: {exc}", status="workflow_generation_error", workflow_name=workflow_name)

    pages_used = result.get("pages_used") or []
    skipped_pages = result.get("skipped_pages") or []
    steps = result.get("steps") or []
    _record_id = result.get("record_id") or "-"
    lines = [
        f"Workflow **\"{workflow_name}\"** generated a test case successfully.",
        f"- Generated record: **[{result.get('record_name') or workflow_name + ' Generated'}](/sessions/{_record_id}/)**",
        f"- Record ID: `{_record_id}`",
        f"- Folder: **{result.get('folder_name') or folder_name}**",
        f"- Steps: **{result.get('step_count') or len(steps)}**",
        f"- Selection mode: **{result.get('selection_mode') or 'deterministic'}**",
    ]
    if pages_used:
        lines.append(f"- Pages used: {', '.join(str(page) for page in pages_used[:8])}")
    if skipped_pages:
        lines.append(f"- Skipped pages: {', '.join(str(page) for page in skipped_pages[:8])}")
    if steps:
        lines.extend([
            "",
            "| # | Action | Page | Field | Locator |",
            "|---|---|---|---|---|",
        ])
        for item in steps[:8]:
            lines.append(
                f"| {item.get('step_no') or '-'} | {item.get('action') or '-'} | {(item.get('page_name') or '-')[:40]} | {(item.get('field_name') or '-')[:32]} | {(item.get('locator') or '-')[:48]} |"
            )
        if len(steps) > 8:
            lines.append(f"\nShowing first 8 of {len(steps)} generated steps.")
    return _tool_result(
        "\n".join(lines),
        status="workflow_generated",
        workflow_name=workflow_name,
        record_id=result.get("record_id"),
        record_name=result.get("record_name"),
        folder_name=result.get("folder_name") or folder_name,
        step_count=result.get("step_count") or len(steps),
    )


def _extract_create_keywords(user_message: str) -> list[str]:
    """Extract raw copy-from keywords from create-test-case phrasing.

    Example:
      "create test case 2 to populate email and address"
      -> ["email", "address"]
    """
    text = (user_message or "").strip()
    if not text:
        return []

    match = re.search(r"\bto\s+populate\s+(.+?)(?:[.!?]|$)", text, re.IGNORECASE)
    if not match:
        match = re.search(r"\bpopulate\s+(.+?)(?:[.!?]|$)", text, re.IGNORECASE)
    if not match:
        return []

    phrase = match.group(1).strip()
    parts = [part.strip(" .,!?") for part in re.split(r"\s*(?:,|\band\b)\s*", phrase, flags=re.IGNORECASE)]
    parts = [part for part in parts if part]

    stop_words = {
        "test", "case", "script", "scripts", "cases", "the", "a", "an",
        "for", "of", "to", "from", "steps", "step", "download", "delete",
        "show", "search", "find", "get", "my", "please", "me", "it", "its",
        "populate",
    }

    cleaned = []
    for part in parts:
        words = [word for word in part.split() if word.lower() not in stop_words]
        normalized = " ".join(words).strip()
        if normalized and normalized not in cleaned:
            cleaned.append(normalized)
    return cleaned


def _search_terms(text: str) -> list[str]:
    stop_words = {
        "test", "case", "script", "scripts", "cases", "the", "a", "an",
        "for", "of", "to", "from", "and", "or", "steps", "step",
        "download", "delete", "show", "search", "find", "get", "my",
        "please", "me", "it", "its", "populate",
    }
    return [word.lower() for word in re.findall(r"[A-Za-z0-9_]+", text or "")
            if word.lower() not in stop_words]


def _best_source_match(matches: list[dict], query: str, seen_rids: set[str] | None = None) -> Optional[dict]:
    """Pick the nearest existing test case for a copy-from keyword.

    Ranking prefers:
    1. Fewer extra words beyond the query
    2. Non-composite names (penalize names containing "and" / commas)
    3. Fewer total content words
    4. Newer records as a final tie-breaker
    """
    if not matches:
        return None

    query_terms = set(_search_terms(query))
    seen_rids = seen_rids or set()

    def _score(match: dict):
        record_name = match.get("record_name") or ""
        record_terms = _search_terms(record_name)
        record_term_set = set(record_terms)
        extra_words = len(record_term_set - query_terms)
        composite_penalty = 1 if re.search(r"\b(and|or)\b|,", record_name, re.IGNORECASE) else 0
        seen_penalty = 1 if str(match.get("record_id")) in seen_rids else 0
        created_at = match.get("created_at")
        created_key = created_at.isoformat() if created_at else ""
        return (
            seen_penalty,
            extra_words,
            composite_penalty,
            len(record_terms),
            -len(query_terms & record_term_set),
            created_key,
        )

    return sorted(matches, key=_score)[0]


def _pending_delete_from_history(conversation_history: list | None) -> Optional[dict]:
    """Extract the pending delete target from the latest bot summary, if any."""
    if not conversation_history:
        return None

    for msg in reversed(conversation_history):
        if msg.get("role") not in {"bot", "assistant"}:
            continue
        content = msg.get("content") or ""
        if "You are about to delete:" not in content:
            continue

        name_match = re.search(r'-\s+(?:\*\*|)Test case:(?:\*\*|)\s+(.+)', content)
        proj_match = re.search(r'-\s+(?:\*\*|)Project:(?:\*\*|)\s+`?([^`\n]+)`?', content)
        if not name_match:
            return None

        record_name = name_match.group(1).strip()
        record_name = re.sub(r'\*+', '', record_name).strip()
        folder = proj_match.group(1).strip() if proj_match else ""
        if folder == "(no project)":
            folder = ""
        return {"record_name": record_name, "folder": folder}

    return None


def _parse_delete_request(user_message: str) -> Optional[dict]:
    """Parse direct delete commands without relying on the LLM.

    Examples:
      Delete Populate Name
      Delete "Populate Name"
      Delete "Delete Populate Name"
      Delete Populate Name from Project001/Sub001/End001
    """
    text = (user_message or "").strip()
    if not text:
        return None

    match = re.match(r'^(delete|remove|omit|ommit)\s+(.+?)\s*$', text, re.IGNORECASE)
    if not match:
        return None

    remainder = match.group(2).strip()
    folder = ""

    folder_match = re.match(r'^(.*?)(?:\s+from\s+)(.+)$', remainder, re.IGNORECASE)
    if folder_match:
        remainder = folder_match.group(1).strip()
        folder = folder_match.group(2).strip().strip('"').strip("'")

    was_quoted = (
        len(remainder) >= 2 and
        remainder[0] == remainder[-1] and
        remainder[0] in {'"', "'"}
    )

    record_name = remainder.strip().strip('"').strip("'")
    if not was_quoted and record_name.lower().startswith("delete "):
        record_name = record_name[7:].strip()
    if not was_quoted and record_name.lower().startswith("remove "):
        record_name = record_name[7:].strip()
    if not was_quoted and record_name.lower().startswith("omit "):
        record_name = record_name[5:].strip()
    if not was_quoted and record_name.lower().startswith("ommit "):
        record_name = record_name[6:].strip()

    if not record_name:
        return None
    return {"record_name": record_name, "folder": folder}


def _is_delete_confirmation(user_message: str) -> bool:
    """Return True when the user explicitly confirms a pending delete."""
    normalized = re.sub(r'\s+', ' ', (user_message or '').strip().lower()).strip(' .,!?:;')
    return normalized in {
        "yes",
        "confirm",
        "confirm delete",
        "go ahead",
        "yes please",
        "remove",
        "ok",
        "okay",
        "fine",
        "yeah",
        "of course",
        "ofcourse",
        "yep",
        "cool",
    }


def _clean_search_sessions_query(raw_query: str) -> str:
    """Normalize a freeform search phrase into a session-name query."""
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r'^\{\%\s*like\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\s*\%\}$', '', query)
    query = re.sub(r'^(?:for\s+)?(?:any\s+)?(?:file\s+name|record_?name)\s*[:=]\s*', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^(?:for\s+)?(?:any\s+)?(?:file|test\s+case|test\s+script|script|session)s?\s+', '', query, flags=re.IGNORECASE)
    # Strip conversational filler prefixes: "related to", "about", "any", "for"
    query = re.sub(r'^(?:related\s+to|about)\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^(?:any|for)\s+', '', query, flags=re.IGNORECASE)
    query = query.strip().strip('"').strip("'")
    query = re.sub(r'\s+', ' ', query).strip(' .,!?:;')
    return query


def _parse_search_sessions_request(user_message: str) -> Optional[str]:
    """Parse direct search intent for test scripts without relying on the LLM.

    Supported phrases include examples like:
      Search for test case 1
      look for login
      find for address
      is there any file smoke
      look file regression
      {%like file name=test case 1%}
      {%like record_name=test case 1%}
    """
    text = (user_message or "").strip()
    if not text:
        return None

    lowered = text.lower()
    session_terms = ("test case", "test script", "script", "session", "file", "record_name", "record name")
    step_terms = ("step", "steps", "locator", "field", "value", "data")

    structured_match = re.search(
        r'(?:\{\%\s*like\s+)?(?:file\s+name|record_?name)\s*[:=]\s*(.+?)(?:\s*\%\}|$)',
        text,
        re.IGNORECASE,
    )
    if structured_match:
        query = _clean_search_sessions_query(structured_match.group(1))
        return query or None

    patterns = [
        r'^(?:search(?:\s+for)?|look(?:\s+for)?|find(?:\s+for)?)\s+(?P<query>.+?)\s*$',
        # Specific: "any [file] related to X" / "is there any file about X"
        r'^(?:is\s+there(?:\s+any)?|any(?:\s+file)?)\s+(?:related\s+to|about)\s+(?P<query>.+?)\??\s*$',
        r'^(?:is\s+there(?:\s+any)?|any\s+file|look\s+file)\s+(?P<query>.+?)\s*$',
        # Conversational follow-ups: "how about entering names?", "related to submit?"
        r'^(?:how\s+about|what\s+about|anything\s+about|anything\s+related\s+to)\s+'
        r'(?:related\s+to\s+|about\s+)?(?P<query>.+?)\??\s*$',
        r'^(?:related\s+to|about)\s+(?P<query>.+?)\??\s*$',
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue
        query = _clean_search_sessions_query(match.group("query"))
        if not query:
            return None

        query_lower = query.lower()
        has_session_term = any(term in lowered for term in session_terms) or any(term in query_lower for term in session_terms)
        has_step_term = any(term in query_lower for term in step_terms)
        if has_step_term and not has_session_term:
            return None
        return query

    return None


def _contains_any_phrase(text: str, phrases: tuple[str, ...]) -> bool:
    lowered = (text or "").strip().lower()
    return any(re.search(r'(?<!\w)' + re.escape(phrase.lower()) + r'(?!\w)', lowered) for phrase in phrases)


# ---------------------------------------------------------------------------
# Fuzzy word normalization — corrects misspellings before trigger checks
# ---------------------------------------------------------------------------

_FUZZY_VOCABULARY: frozenset = frozenset({
    # search / lookup intent  (base + -ing forms)
    "search", "searching", "find", "finding", "look", "looking", "lookup",
    "locate", "locating", "check", "checking", "verify", "verifying",
    "confirm", "confirming", "exists", "exist", "available", "present",
    "named", "called",
    # display / show intent
    "list", "listing", "show", "showing", "view", "viewing", "display",
    "displaying", "inspect", "open", "see",
    # download / export intent
    "download", "downloading", "export", "exporting", "copy", "copying",
    "document", "save", "saving", "send", "sending", "share", "sharing", "pull",
    # delete intent
    "delete", "deleting", "remove", "removing", "omit", "omitting",
    "archive", "archiving", "drop", "dropping", "purge", "purging",
    "erase", "erasing", "discard", "discarding", "trash", "wipe", "wiping",
    "permanently",
    # update / edit intent
    "update", "updating", "change", "changing", "modify", "modifying",
    "edit", "editing", "replace", "replacing", "revise", "revising",
    "adjust", "adjusting", "correct", "correcting", "patch", "patching",
    "overwrite", "rename", "renaming", "create", "creating",
    # entity nouns
    "test", "case", "script", "scripts", "session", "sessions", "record",
    "project", "folder", "step", "steps", "flow", "sequence", "outline",
    "walkthrough", "breakdown", "details", "baseline", "file", "files",
    # file formats
    "pdf", "csv", "word", "spreadsheet", "excel",
    # web automation verbs (base + -ing forms)
    "click", "clicking", "input", "inputting", "type", "typing",
    "submit", "submitting", "navigate", "navigating", "select", "selecting",
    "hover", "hovering", "scroll", "scrolling", "populate", "populating",
    "fill", "filling", "enter", "entering", "press", "pressing",
    "upload", "uploading", "drag", "dragging", "login", "logout",
    # common field / element names
    "name", "names", "address", "email", "username", "password",
    "field", "value", "locator", "strategy", "element", "action", "page",
    "validation", "recent", "latest",
    # confirmations
    "cancel", "okay",
})


def _fuzzy_normalize_message(text: str, cutoff: float = 0.82) -> str:
    """Replace misspelled words with the closest vocabulary match (if confident).

    Only words of 4+ characters not already in the vocabulary are corrected.
    Short words (4-5 chars) use a slightly relaxed cutoff (0.75) to catch
    transpositions like "shwo" → "show". The correction preserves the original
    word boundary context so downstream regex checks still work.
    """
    if not text:
        return text

    cache: dict[str, str | None] = {}

    def _fix(word: str) -> str:
        lowered = word.lower()
        if len(lowered) <= 3 or lowered in _FUZZY_VOCABULARY:
            return word
        if lowered not in cache:
            _cutoff = 0.75 if len(lowered) <= 5 else cutoff
            matches = difflib.get_close_matches(lowered, _FUZZY_VOCABULARY, n=1, cutoff=_cutoff)
            cache[lowered] = matches[0] if matches else None
        replacement = cache[lowered]
        return replacement if replacement else word

    return re.sub(r'[A-Za-z]+', lambda m: _fix(m.group()), text)


def _looks_like_search_candidate(user_message: str) -> bool:
    """Cheap guard for messages that may be asking whether a test script exists."""
    text = (user_message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    session_terms = (
        "test case",
        "test script",
        "script",
        "session",
        "file",
        "record",
        "record name",
        "record_name",
    )
    search_shape_terms = (
        "named",
        "called",
        "exists",
        "exist",
        "available",
        "present",
        "has",
        "have",
        "there",
        "verify",
        "confirm",
        "check",
        "see if",
        "tell me if",
        "related to",
        "how about",
        "what about",
        "anything about",
        "anything related",
    )
    blocked_terms = (
        "download",
        "delete",
        "remove",
        "omit",
        "update",
        "change",
        "set",
        "modify",
        "edit",
        "replace",
        "show steps",
        "view steps",
        "where are the steps",
        "what are the steps",
    )

    if _contains_any_phrase(lowered, blocked_terms):
        return False

    has_session_term = _contains_any_phrase(lowered, session_terms)
    has_search_shape = _contains_any_phrase(lowered, search_shape_terms) or "?" in text
    # Conversational follow-ups like "how about related to X?" carry their own
    # search signal without needing an explicit session term.
    is_conversational_search = _contains_any_phrase(
        lowered, ("how about", "what about", "anything about", "related to")
    ) and ("?" in text or has_session_term)
    word_count = len(re.findall(r"\w+", text))
    return (has_session_term and has_search_shape or is_conversational_search) and word_count <= 30


def _llm_should_try_search_parse(user_message: str) -> bool:
    """Use the LLM only for ambiguous search-like phrasing that missed the static triggers."""
    if not _looks_like_search_candidate(user_message):
        return False

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this message likely asks to search for or check the existence of a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=SEARCH_TRIGGER_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return False

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return False

    return payload.get("should_parse") is True


def _looks_like_show_steps_candidate(user_message: str) -> bool:
    """Cheap guard for messages that may be asking to inspect a script's steps."""
    text = (user_message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    session_terms = (
        "test case",
        "test script",
        "script",
        "session",
        "record",
        "flow",
    )
    step_terms = (
        "step",
        "steps",
        "flow",
        "sequence",
        "outline",
        "walkthrough",
        "walk through",
        "breakdown",
        "details",
    )
    blocked_terms = (
        "download",
        "export",
        "save as",
        "delete",
        "remove",
        "omit",
        "update",
        "change",
        "set",
        "modify",
        "edit",
        "replace",
        "search",
        "find",
        "lookup",
        "locate",
    )

    if _contains_any_phrase(lowered, blocked_terms):
        return False

    has_session_term = _contains_any_phrase(lowered, session_terms)
    has_step_term = _contains_any_phrase(lowered, step_terms)
    word_count = len(re.findall(r"\w+", text))
    return has_session_term and has_step_term and word_count <= 30


def _llm_should_try_show_steps_parse(user_message: str) -> bool:
    if not _looks_like_show_steps_candidate(user_message):
        return False

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this message likely asks to show, inspect, or walk through the steps of a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=SHOW_STEPS_TRIGGER_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return False

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return False

    return payload.get("should_parse") is True


def _looks_like_download_candidate(user_message: str) -> bool:
    """Cheap guard for messages that may be asking for an export or copy of a script."""
    text = (user_message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    session_terms = (
        "test case",
        "test script",
        "script",
        "session",
        "file",
        "record",
    )
    download_terms = (
        "export",
        "download",
        "copy",
        "document",
        "pdf",
        "csv",
        "doc",
        "docx",
        "word",
        "spreadsheet",
        "excel",
        "save",
        "send",
        "share",
    )
    blocked_terms = (
        "delete",
        "remove",
        "omit",
        "update",
        "change",
        "set",
        "modify",
        "edit",
        "replace",
        "show steps",
        "view steps",
        "where are the steps",
        "what are the steps",
        "search",
        "find",
        "lookup",
        "locate",
    )

    if _contains_any_phrase(lowered, blocked_terms):
        return False

    has_session_term = _contains_any_phrase(lowered, session_terms)
    has_download_term = _contains_any_phrase(lowered, download_terms)
    word_count = len(re.findall(r"\w+", text))
    return has_session_term and has_download_term and word_count <= 30


def _llm_should_try_download_parse(user_message: str) -> bool:
    if not _looks_like_download_candidate(user_message):
        return False

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this message likely asks to download, export, save, or send a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=DOWNLOAD_TRIGGER_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return False

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return False

    return payload.get("should_parse") is True


def _looks_like_update_candidate(user_message: str) -> bool:
    """Cheap guard for messages that may be asking to revise script data or steps."""
    text = (user_message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    session_terms = (
        "test case",
        "test script",
        "script",
        "session",
        "step",
        "steps",
        "locator",
        "field",
        "value",
        "data",
    )
    update_terms = (
        "revise",
        "adjust",
        "correct",
        "fix",
        "patch",
        "overwrite",
        "rename",
        "make it",
        "make the",
    )
    blocked_terms = (
        "download",
        "export",
        "save as",
        "delete",
        "remove",
        "omit",
        "show steps",
        "view steps",
        "where are the steps",
        "what are the steps",
        "search",
        "find",
        "lookup",
        "locate",
        "create project",
        "create test case",
    )

    if _contains_any_phrase(lowered, blocked_terms):
        return False

    has_session_term = _contains_any_phrase(lowered, session_terms)
    has_update_term = _contains_any_phrase(lowered, update_terms)
    word_count = len(re.findall(r"\w+", text))
    return has_session_term and has_update_term and word_count <= 35


def _llm_should_try_update_parse(user_message: str) -> bool:
    if not _looks_like_update_candidate(user_message):
        return False

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this message likely asks to update or revise data or step properties in a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=UPDATE_TRIGGER_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return False

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return False

    return payload.get("should_parse") is True


def _looks_like_delete_candidate(user_message: str) -> bool:
    """Cheap guard for messages that may be asking to remove a recorded script."""
    text = (user_message or "").strip()
    if not text:
        return False

    lowered = text.lower()
    session_terms = (
        "test case",
        "test script",
        "script",
        "session",
        "record",
        "file",
    )
    delete_terms = (
        "archive",
        "drop",
        "purge",
        "erase",
        "discard",
        "permanent",
        "permanently",
        "remove permanently",
        "get rid of",
        "trash",
        "wipe",
    )
    blocked_terms = (
        "download",
        "export",
        "save as",
        "update",
        "change",
        "set",
        "modify",
        "edit",
        "replace",
        "show steps",
        "view steps",
        "where are the steps",
        "what are the steps",
        "search",
        "find",
        "lookup",
        "locate",
        "create project",
        "create test case",
    )

    if _contains_any_phrase(lowered, blocked_terms):
        return False

    has_session_term = _contains_any_phrase(lowered, session_terms)
    has_delete_term = _contains_any_phrase(lowered, delete_terms)
    word_count = len(re.findall(r"\w+", text))
    return has_session_term and has_delete_term and word_count <= 30


def _llm_should_try_delete_parse(user_message: str) -> bool:
    if not _looks_like_delete_candidate(user_message):
        return False

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this message likely asks to delete, remove, archive, drop, or permanently discard a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=DELETE_TRIGGER_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return False

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return False

    return payload.get("should_parse") is True


def _split_query_and_folder(raw_value: str) -> tuple[str, str]:
    text = (raw_value or "").strip()
    if not text:
        return "", ""

    folder = ""
    folder_match = re.match(r'^(.*?)(?:\s+from\s+)(.+)$', text, re.IGNORECASE)
    if folder_match:
        text = folder_match.group(1).strip()
        folder = folder_match.group(2).strip().strip('"').strip("'")
    return text, folder


def _clean_update_record_query(raw_query: str) -> str:
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r'^(?:the\s+)?', '', query, flags=re.IGNORECASE)
    query = query.strip().strip('"').strip("'")
    query = re.sub(r'\s+', ' ', query).strip(' .,!?:;')
    return query


def _normalize_step_field_name(value: str) -> str:
    normalized = re.sub(r'\s+', ' ', (value or '').strip().lower())
    aliases = {
        'action': 'action',
        'page url': 'page_url',
        'url': 'page_url',
        'page': 'page_url',
        'element': 'element_tag',
        'element tag': 'element_tag',
        'tag': 'element_tag',
        'strategy': 'strategy',
        'locator': 'locator',
        'field': 'field_name',
        'field name': 'field_name',
        'field value': 'field_value',
        'value': 'field_value',
        'validation': 'validation',
    }
    return aliases.get(normalized, '')


def _parse_update_data_value_request(user_message: str) -> Optional[dict]:
    text = (user_message or '').strip()
    if not text:
        return None

    patterns = [
        r'^(?:update|change|set)\s+step\s+(?P<step_no>\d+)\s+(?:in|for|on)\s+(?P<record>.+?)\s+to\s+(?P<new_value>.+?)\s*$',
        r'^(?:update|change|set)\s+(?P<field_name>[A-Za-z0-9_][A-Za-z0-9_\- ]*?)\s+(?:in|for|on)\s+(?P<record>.+?)\s+to\s+(?P<new_value>.+?)\s*$',
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue

        record_part, folder = _split_query_and_folder(match.group('record'))
        record_id = _clean_update_record_query(record_part)
        new_value = match.group('new_value').strip().strip('"').strip("'")
        if not record_id or not new_value:
            return None

        step_no = int(match.group('step_no')) if match.groupdict().get('step_no') else 0
        field_name = (match.groupdict().get('field_name') or '').strip()
        if field_name.lower() in {'step', 'steps'}:
            field_name = ''
        return {
            'tool': 'update_data_value',
            'args': {
                'record_id': record_id,
                'new_value': new_value,
                'step_no': step_no,
                'field_name': field_name,
                'folder': folder,
            },
        }
    return None


def _parse_bulk_update_data_request(user_message: str) -> Optional[dict]:
    text = (user_message or '').strip()
    if not text:
        return None

    match = re.match(r'^(?:update|change|set)\s+(?P<assignments>.+?)\s+(?:in|for|on)\s+(?P<record>.+?)\s*$', text, re.IGNORECASE)
    if not match:
        return None

    assignments_text = match.group('assignments').strip()
    if assignments_text.count(' to ') < 2:
        return None

    record_part, folder = _split_query_and_folder(match.group('record'))
    record_id = _clean_update_record_query(record_part)
    if not record_id:
        return None

    assignments = re.findall(r'([^,]+?)\s+to\s+([^,]+?)(?=(?:\s+and\s+[^,]+?\s+to\s+)|$)', assignments_text, flags=re.IGNORECASE)
    updates = []
    for field_name, new_value in assignments:
        cleaned_field = re.sub(r'^and\s+', '', field_name.strip(), flags=re.IGNORECASE).strip('"').strip("'")
        cleaned_value = new_value.strip().strip('"').strip("'")
        if cleaned_field and cleaned_value:
            updates.append({'field_name': cleaned_field, 'new_value': cleaned_value})
    if len(updates) < 2:
        return None

    return {
        'tool': 'bulk_update_data',
        'args': {
            'record_id': record_id,
            'updates': updates,
            'folder': folder,
        },
    }


def _parse_update_step_request(user_message: str) -> Optional[dict]:
    text = (user_message or '').strip()
    if not text:
        return None

    match = re.match(
        r'^(?:update|change|set|modify|edit|replace)\s+step\s+(?P<step_no>\d+)\s+(?P<field>action|page\s+url|url|page|element\s+tag|element|tag|strategy|locator|field\s+name|field\s+value|validation)\s+(?:in|for|on)\s+(?P<record>.+?)\s+to\s+(?P<value>.+?)\s*$',
        text,
        re.IGNORECASE,
    )
    if not match:
        return None

    record_part, folder = _split_query_and_folder(match.group('record'))
    record_id = _clean_update_record_query(record_part)
    field_name = _normalize_step_field_name(match.group('field'))
    value = match.group('value').strip().strip('"').strip("'")
    if not record_id or not field_name or not value:
        return None

    return {
        'tool': 'update_step',
        'args': {
            'record_id': record_id,
            'step_no': int(match.group('step_no')),
            field_name: value,
            'folder': folder,
        },
    }


def _should_try_llm_search_parse(user_message: str) -> bool:
    """Avoid an extra LLM call when the message is clearly unrelated."""
    triggers = (
        "search",
        "find",
        "look",
        "lookup",
        "locate",
        "check for",
        "check whether",
        "check whether we have",
        "can you check whether",
        "can you check whether we have",
        "is there",
        "do we have",
        "whether we have",
        "see if we have",
        "have any",
        "any file",
        "file name",
        "record_name",
        "record name",
        "related to",
        "how about",
        "what about",
        "anything about",
    )
    if _contains_any_phrase(user_message, triggers):
        return True
    return _llm_should_try_search_parse(user_message)


def _llm_parse_search_sessions_request(user_message: str) -> Optional[str]:
    """Use the LLM as a fallback search-intent parser when regex misses."""
    if not _should_try_llm_search_parse(user_message):
        return None

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this is a search request for a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=SEARCH_SESSION_PARSE_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return None

    payload = _extract_json_object(llm_response)
    if not payload:
        return None

    query = payload.get("query")
    if not isinstance(query, str):
        return None

    cleaned_query = _clean_search_sessions_query(query)
    if not cleaned_query:
        return None

    query_lower = cleaned_query.lower()
    message_lower = (user_message or "").lower()
    session_terms = ("test case", "test script", "script", "session", "file", "record_name", "record name")
    step_terms = ("step", "steps", "locator", "field", "value", "data")
    has_session_term = any(term in message_lower for term in session_terms) or any(term in query_lower for term in session_terms)
    has_step_term = any(term in query_lower for term in step_terms)
    if has_step_term and not has_session_term:
        return None

    return cleaned_query


def _should_try_llm_show_steps_parse(user_message: str) -> bool:
    triggers = (
        "show",
        "view",
        "display",
        "inspect",
        "open",
        "steps",
        "where are the steps",
        "what are the steps",
        "can you show",
        "can you view",
        "walk me through",
        "list out the steps",
    )
    if _contains_any_phrase(user_message, triggers):
        return True
    return _llm_should_try_show_steps_parse(user_message)


def _llm_parse_show_steps_request(user_message: str) -> Optional[str]:
    if not _should_try_llm_show_steps_parse(user_message):
        return None

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this is a request to show the steps for a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=SHOW_STEPS_PARSE_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return None

    payload = _extract_json_object(llm_response)
    if not payload:
        return None

    record_id = payload.get("record_id")
    if not isinstance(record_id, str):
        return None

    cleaned_query = _clean_show_steps_query(record_id)
    return cleaned_query or None


def _clean_show_steps_query(raw_query: str) -> str:
    """Normalize a freeform show-steps phrase into a session-name query."""
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r'^(?:for|of|in)\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\s+steps?$', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^the\s+', '', query, flags=re.IGNORECASE)
    query = query.strip().strip('"').strip("'")
    query = re.sub(r'\s+', ' ', query).strip(' .,!?:;')
    return query


def _parse_show_steps_request(user_message: str) -> Optional[str]:
    """Parse direct requests to show/view the steps for a test script."""
    text = (user_message or "").strip()
    if not text:
        return None

    patterns = [
        r'^(?:show|view)\s+steps?\s+(?P<query>.+?)\s*$',
        r'^(?:show|view)\s+(?P<query>.+?)\s+steps?\s*$',
        r'^(?:where\s+are|what\s+are)\s+(?:the\s+)?steps?\s+(?P<query>.+?)\s*$',
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue
        query = _clean_show_steps_query(match.group("query"))
        return query or None
    return None


def _normalize_download_format(value: str) -> str:
    normalized = (value or "").strip().lower().lstrip(".")
    aliases = {
        "csv": "csv",
        "spreadsheet": "csv",
        "excel": "csv",
        "xls": "csv",
        "xlsx": "csv",
        "pdf": "pdf",
        "doc": "doc",
        "docx": "doc",
        "word": "doc",
        "document": "doc",
    }
    return aliases.get(normalized, "")


def _clean_download_query(raw_query: str) -> str:
    """Normalize a freeform download phrase into a session-name query."""
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r'^(?:for|of)\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^(?:a\s+copy\s+of|copy\s+of|copy|document)\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^(?:the\s+)?(?:test\s+case|test\s+script|script|session|file)\s+', '', query, flags=re.IGNORECASE)
    query = query.strip().strip('"').strip("'")
    query = re.sub(r'\s+', ' ', query).strip(' .,!?:;')
    return query


def _parse_download_request(user_message: str) -> Optional[dict]:
    """Parse direct requests to download a test script export."""
    text = (user_message or "").strip()
    if not text:
        return None

    patterns = [
        r'^(?P<verb>download|pull|get)(?:\s+(?:me\s+)?(?:a\s+copy\s+of|copy\s+of|copy|document))?\s+(?P<query>.+?)\s*$',
        r'^(?:a\s+copy\s+of|copy\s+of|document)\s+(?P<query>.+?)\s*$',
    ]
    for pattern in patterns:
        match = re.match(pattern, text, re.IGNORECASE)
        if not match:
            continue

        remainder = match.group("query").strip()
        folder = ""
        folder_match = re.match(r'^(.*?)(?:\s+from\s+)(.+)$', remainder, re.IGNORECASE)
        if folder_match:
            remainder = folder_match.group(1).strip()
            folder = folder_match.group(2).strip().strip('"').strip("'")

        fmt = ""
        fmt_match = re.search(r'\b(?:as|in)\s+(csv|pdf|doc|docx|word|document|spreadsheet|excel|xls|xlsx)\b', remainder, re.IGNORECASE)
        if fmt_match:
            fmt = _normalize_download_format(fmt_match.group(1))
            remainder = re.sub(r'\b(?:as|in)\s+(csv|pdf|doc|docx|word|document|spreadsheet|excel|xls|xlsx)\b', '', remainder, flags=re.IGNORECASE).strip()
        else:
            trailing_match = re.search(r'\b(csv|pdf|doc|docx|word|document|spreadsheet|excel|xls|xlsx)\b\s*$', remainder, re.IGNORECASE)
            if trailing_match:
                fmt = _normalize_download_format(trailing_match.group(1))
                remainder = remainder[:trailing_match.start()].strip()

        query = _clean_download_query(remainder)
        if not query:
            return None
        return {"record_id": query, "fmt": fmt, "folder": folder}

    return None


def _should_try_llm_download_parse(user_message: str) -> bool:
    triggers = (
        "download",
        "pull",
        "get",
        "copy of",
        "a copy",
        "document",
        "export",
        "save as",
    )
    if _contains_any_phrase(user_message, triggers):
        return True
    return _llm_should_try_download_parse(user_message)


def _llm_parse_download_request(user_message: str) -> Optional[dict]:
    if not _should_try_llm_download_parse(user_message):
        return None

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this is a request to download or export a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=DOWNLOAD_PARSE_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return None

    payload = _extract_json_object(llm_response)
    if not payload:
        return None

    record_id = payload.get("record_id")
    if not isinstance(record_id, str):
        return None

    fmt = payload.get("fmt") if isinstance(payload.get("fmt"), str) else ""
    folder = payload.get("folder") if isinstance(payload.get("folder"), str) else ""
    cleaned_query = _clean_download_query(record_id)
    if not cleaned_query:
        return None

    return {
        "record_id": cleaned_query,
        "fmt": _normalize_download_format(fmt),
        "folder": folder.strip().strip('"').strip("'"),
    }


def _should_try_llm_delete_parse(user_message: str) -> bool:
    triggers = (
        "delete",
        "remove",
        "omit",
        "ommit",
        "delete test case",
        "delete test script",
        "remove test case",
        "remove test script",
    )
    if _contains_any_phrase(user_message, triggers):
        return True
    return _llm_should_try_delete_parse(user_message)


def _llm_parse_delete_request(user_message: str) -> Optional[dict]:
    if not _should_try_llm_delete_parse(user_message):
        return None

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this is a request to delete a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=DELETE_PARSE_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return None

    payload = _extract_json_object(llm_response)
    if not payload:
        return None

    record_name = payload.get("record_name")
    if not isinstance(record_name, str):
        return None

    folder = payload.get("folder") if isinstance(payload.get("folder"), str) else ""
    cleaned_name = record_name.strip().strip('"').strip("'")
    if not cleaned_name:
        return None

    return {
        "record_name": cleaned_name,
        "folder": folder.strip().strip('"').strip("'"),
    }


def _should_try_llm_update_parse(user_message: str) -> bool:
    triggers = (
        "update",
        "change",
        "set",
        "modify",
        "edit",
        "replace",
    )
    if _contains_any_phrase(user_message, triggers):
        return True
    return _llm_should_try_update_parse(user_message)


def _llm_parse_update_request(user_message: str) -> Optional[dict]:
    if not _should_try_llm_update_parse(user_message):
        return None

    prompt = (
        f"User message:\n{user_message}\n\n"
        "Decide whether this is an update request for a recorded test script. "
        "Return only the required JSON object."
    )
    llm_response = _call_ollama(prompt, system=UPDATE_PARSE_SYSTEM_PROMPT)
    if not llm_response or llm_response.startswith("[Error]"):
        return None

    payload = _extract_json_object(llm_response)
    if not isinstance(payload, dict):
        return None

    tool_name = payload.get("tool")
    tool_args = payload.get("args")
    allowed_tools = {"update_data_value", "bulk_update_data", "update_step"}
    if tool_name not in allowed_tools or not isinstance(tool_args, dict):
        return None

    normalized_args = dict(tool_args)
    record_id = normalized_args.get("record_id")
    if isinstance(record_id, str):
        normalized_args["record_id"] = record_id.strip().strip('"').strip("'")
    folder = normalized_args.get("folder")
    if isinstance(folder, str):
        normalized_args["folder"] = folder.strip().strip('"').strip("'")

    if tool_name == "update_data_value":
        new_value = normalized_args.get("new_value")
        if not isinstance(new_value, str):
            return None
        step_no = normalized_args.get("step_no", 0)
        if not isinstance(step_no, int):
            step_no = 0
        field_name = normalized_args.get("field_name", "")
        if not isinstance(field_name, str):
            field_name = ""
        return {
            "tool": tool_name,
            "args": {
                "record_id": normalized_args.get("record_id", ""),
                "new_value": new_value,
                "step_no": step_no,
                "field_name": field_name.strip(),
                "folder": normalized_args.get("folder", ""),
            },
        }

    if tool_name == "bulk_update_data":
        updates = normalized_args.get("updates")
        if not isinstance(updates, list):
            return None
        cleaned_updates = []
        for entry in updates:
            if not isinstance(entry, dict):
                continue
            clean_entry = {}
            if isinstance(entry.get("step_no"), int):
                clean_entry["step_no"] = entry["step_no"]
            if isinstance(entry.get("field_name"), str):
                clean_entry["field_name"] = entry["field_name"].strip()
            if isinstance(entry.get("new_value"), str):
                clean_entry["new_value"] = entry["new_value"]
            if clean_entry.get("new_value") and (clean_entry.get("step_no") or clean_entry.get("field_name")):
                cleaned_updates.append(clean_entry)
        if not cleaned_updates:
            return None
        return {
            "tool": tool_name,
            "args": {
                "record_id": normalized_args.get("record_id", ""),
                "updates": cleaned_updates,
                "folder": normalized_args.get("folder", ""),
            },
        }

    step_no = normalized_args.get("step_no")
    if not isinstance(step_no, int):
        return None
    allowed_fields = {"action", "page_url", "element_tag", "strategy", "locator", "field_name", "field_value", "validation"}
    cleaned_fields = {key: value for key, value in normalized_args.items() if key in allowed_fields and isinstance(value, str)}
    if not cleaned_fields:
        return None
    return {
        "tool": tool_name,
        "args": {
            "record_id": normalized_args.get("record_id", ""),
            "step_no": step_no,
            "folder": normalized_args.get("folder", ""),
            **cleaned_fields,
        },
    }


def _step_summary(row: dict) -> str:
    """Build a human-readable step summary for the table."""
    action = (row.get("action") or "").strip()
    field_name = (row.get("field_name") or "").strip()
    field_value = (row.get("field_value") or "").strip()
    element_tag = (row.get("element_tag") or "").strip()

    if field_name and field_value:
        summary = f"{action} {field_name} = {field_value}"
    elif field_name:
        summary = f"{action} {field_name}"
    elif element_tag:
        summary = f"{action} {element_tag}"
    else:
        summary = action or "step"

    return re.sub(r'\s+', ' ', summary).strip()


def _find_record_id_by_name(name: str, folder: str = "") -> Optional[str]:
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            name_sql, name_params = _name_ilike_clause(name, "m.record_name")
            if folder:
                # If folder/project specified, narrow the search
                cur.execute(
                    "SELECT m.record_id FROM session_meta m "
                    "JOIN steps s ON s.record_id = m.record_id "
                    f"WHERE ({name_sql}) AND s.folder_name ILIKE %s "
                    "GROUP BY m.record_id, m.created_at "
                    "ORDER BY m.created_at DESC LIMIT 1",
                    name_params + [f"%{folder}%"],
                )
            else:
                name_sql2, name_params2 = _name_ilike_clause(name, "record_name")
                cur.execute(
                    f"SELECT record_id FROM session_meta "
                    f"WHERE ({name_sql2}) ORDER BY created_at DESC LIMIT 1",
                    name_params2,
                )
            row = cur.fetchone()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)
    return str(row["record_id"]) if row else None


def _call_ollama(prompt: str, *, system: str = "") -> str:
    full = f"{system}\n\nUser:\n{prompt}" if system else prompt
    try:
        resp = requests.post(
            f"{OLLAMA_API}/generate",
            json={"model": LLM_MODEL, "prompt": full,
                  "stream": True, "num_predict": LLM_MAX_TOKENS},
            stream=True, timeout=300,
        )
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        return "[Error] Cannot connect to Ollama. Make sure it is running."
    except Exception as e:
        return f"[Error] LLM request failed: {e}"

    parts = []
    for raw_line in resp.iter_lines():
        if not raw_line:
            continue
        chunk = json.loads(raw_line)
        token = chunk.get("response", "")
        if token:
            parts.append(token)
        if chunk.get("done"):
            break
    return "".join(parts).strip()


def _resolve_record_id(value: str, folder: str = "") -> Optional[str]:
    """Resolve a record_id from a UUID string or test script name."""
    try:
        _uuid.UUID(value)
        return value
    except ValueError:
        return _find_record_id_by_name(value, folder=folder)


def tool_download_session(record_id: str, fmt: str = "", folder: str = "") -> dict:
    """Return a download link for test script steps (csv/pdf/doc)."""

    if not fmt:
        return _tool_result(
            "What file format would you like?\n\n"
            "- **csv** — spreadsheet\n"
            "- **pdf** — PDF document\n"
            "- **doc** — Word document",
            status="needs_download_format",
            missing=["fmt"],
            record_id=record_id,
            folder=folder,
            allowed_formats=["csv", "pdf", "doc"],
        )

    # If record_id is a name (not UUID), check for duplicates first
    is_uuid = True
    try:
        _uuid.UUID(record_id)
    except ValueError:
        is_uuid = False

    if not is_uuid and not folder:
        # Check for multiple test scripts with the same name
        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                name_sql, name_params = _name_ilike_clause(record_id)
                cur.execute(
                    "SELECT m.record_id, m.record_name, MIN(s.folder_name) AS folder_name "
                    "FROM session_meta m "
                    "LEFT JOIN steps s ON s.record_id = m.record_id "
                    f"WHERE ({name_sql}) "
                    "GROUP BY m.record_id, m.record_name "
                    "ORDER BY MIN(m.created_at) DESC",
                    name_params,
                )
                matches = cur.fetchall()
        except Exception:
            _put_conn(conn, discard=True)
            matches = []
        else:
            _put_conn(conn)

        if len(matches) > 1:
            lines = [f"Multiple test scripts found named **\"{record_id}\"**. "
                     f"Which project do you mean?\n"]
            for i, m in enumerate(matches, 1):
                proj = m["folder_name"] or "(no project)"
                lines.append(f"{i}. **{m['record_name']}** — Project: `{proj}`")
            lines.append("\nPlease specify the project name, e.g. "
                         f"\"download {record_id} from Project001\"")
            return _tool_result(
                "\n".join(lines),
                status="ambiguous_download_target",
                record_id=record_id,
                folder_required=True,
                matches=[{"record_id": str(m["record_id"]), "record_name": m["record_name"], "folder_name": m["folder_name"] or ""} for m in matches],
                download=None,
            )

    # Resolve with folder if provided
    if folder and not is_uuid:
        rid = _find_record_id_by_name(record_id, folder=folder)
    else:
        rid = _resolve_record_id(record_id)

    if not rid:
        return _tool_result(
            f"No test script found for \"{record_id}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_id"],
            record_id=record_id,
            suggestions=["list scripts", "search <keyword>"],
        )

    fmt = fmt.lower().strip()
    if fmt not in ("csv", "pdf", "doc"):
        return _tool_result(
            f"Unsupported format \"{fmt}\". Use csv, pdf, or doc.",
            status="unsupported_format",
            fmt=fmt,
            allowed_formats=["csv", "pdf", "doc"],
        )

    # Look up the test script name and folder for the filename
    name = ""
    rec_folder = ""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT record_name FROM session_meta WHERE record_id = %s", [rid])
            row = cur.fetchone()
            if row:
                name = row["record_name"] or ""

            cur.execute(
                "SELECT folder_name FROM steps WHERE record_id = %s AND folder_name IS NOT NULL "
                "LIMIT 1", [rid]
            )
            frow = cur.fetchone()
            if frow:
                rec_folder = frow["folder_name"] or ""
    except Exception:
        _put_conn(conn, discard=True)
        name = ""
    else:
        _put_conn(conn)

    safe_name = "".join(c for c in name if c.isalnum() or c in " _-").strip() if name else ""

    # Include project folder in filename for clarity
    if rec_folder:
        project = rec_folder.split("/")[0]
        safe_project = "".join(c for c in project if c.isalnum() or c in " _-").strip()
        display_name = f"{safe_name} ({safe_project})" if safe_name else f"session_{rid[:8]}"
    else:
        display_name = safe_name or f"session_{rid[:8]}"

    return {
        "text": f"Here is your download link:",
        "download": {
            "url": f"/sessions/{rid}/download/{fmt}/",
            "filename": f"{display_name}.{fmt}",
        },
    }


def tool_update_data_value(record_id: str, new_value: str,
                           step_no: int = 0, field_name: str = "", folder: str = "") -> dict:
    """Update the data value for a specific step (by step_no or field_name)."""
    rid = _resolve_record_id(record_id, folder=folder)
    if not rid:
        return _tool_result(
            f"No test script found for \"{record_id}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_id"],
            record_id=record_id,
            suggestions=["list scripts", "search <keyword>"],
        )

    if not step_no and not field_name:
        return _tool_result(
            "Please provide either `step_no` or `field_name` to identify the step.",
            status="needs_step_identifier",
            missing=["step_no", "field_name"],
            record_id=rid,
        )

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Resolve step_no from field_name if needed
            if not step_no and field_name:
                cur.execute(
                    "SELECT d.step_no FROM data d "
                    "JOIN steps s ON s.record_id = d.record_id AND s.step_no = d.step_no "
                    "WHERE d.record_id = %s AND d.field_name ILIKE %s "
                    "ORDER BY CASE WHEN s.action IN ('change','input') THEN 0 ELSE 1 END, "
                    "d.step_no LIMIT 1",
                    [rid, f"%{field_name}%"],
                )
                match = cur.fetchone()
                if not match:
                    return _tool_result(
                        f"No data field matching \"{field_name}\" in test script {rid[:8]}….\n\n"
                        f"**Suggestions:**\n"
                        f"- Show available steps: \"show steps for {record_id}\"\n"
                        f"- Try a different field name",
                        status="field_not_found",
                        record_id=rid,
                        field_name=field_name,
                        suggestions=[f"show steps for {record_id}", "try a different field name"],
                    )
                step_no = match["step_no"]

            # Fetch old values for before/after display
            cur.execute(
                "SELECT field_name, value FROM data WHERE record_id = %s AND step_no = %s",
                [rid, step_no],
            )
            old_row = cur.fetchone()
            old_value = old_row["value"] if old_row else "(none)"
            fname = old_row["field_name"] if old_row else "(unknown)"

            # Update data table
            cur.execute(
                "UPDATE data SET value = %s WHERE record_id = %s AND step_no = %s",
                [new_value, rid, step_no],
            )
            data_updated = cur.rowcount

            # Update steps table
            cur.execute(
                "UPDATE steps SET field_value = %s, updated_at = NOW() "
                "WHERE record_id = %s AND step_no = %s",
                [new_value, rid, step_no],
            )
            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error updating data: {exc}"}
    else:
        _put_conn(conn)

    if data_updated == 0:
        return _tool_result(
            f"No data row found for record {rid[:8]}… step {step_no}.\n\n"
            f"**Suggestion:** Show available steps: \"show steps for {record_id}\"",
            status="data_row_not_found",
            record_id=rid,
            step_no=step_no,
            suggestions=[f"show steps for {record_id}"],
        )
    return {
        "text": (f"Updated **step {step_no}** field **\"{fname}\"**:\n\n"
                 f"| | Value |\n|---|---|\n"
                 f"| Before | `{old_value}` |\n"
                 f"| After  | `{new_value}` |")
    }


def tool_bulk_update_data(record_id: str, updates: list, folder: str = "") -> dict:
    """Update multiple steps' data values in one call.

    Args:
        record_id: UUID or test script name.
        updates: list of dicts, each with {step_no, new_value} or {field_name, new_value}.
    """
    rid = _resolve_record_id(record_id, folder=folder)
    if not rid:
        return _tool_result(
            f"No test script found for \"{record_id}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_id"],
            record_id=record_id,
            suggestions=["list scripts", "search <keyword>"],
        )

    if not updates or not isinstance(updates, list):
        return _tool_result(
            "Please provide a list of updates, e.g. "
            '`[{"step_no": 1, "new_value": "Alice"}, ...]`',
            status="needs_updates_list",
            missing=["updates"],
            record_id=rid,
        )

    conn = _get_conn()
    results = []
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            for entry in updates:
                sno = entry.get("step_no", 0)
                fname = entry.get("field_name", "")
                nv = entry.get("new_value", "")

                if not sno and fname:
                    cur.execute(
                        "SELECT d.step_no FROM data d "
                        "JOIN steps s ON s.record_id = d.record_id AND s.step_no = d.step_no "
                        "WHERE d.record_id = %s AND d.field_name ILIKE %s "
                        "ORDER BY CASE WHEN s.action IN ('change','input') THEN 0 ELSE 1 END, "
                        "d.step_no LIMIT 1",
                        [rid, f"%{fname}%"],
                    )
                    match = cur.fetchone()
                    if not match:
                        results.append(f"- Field \"{fname}\": not found")
                        continue
                    sno = match["step_no"]

                if not sno:
                    results.append(f"- Entry skipped (no step_no or field_name)")
                    continue

                # Fetch old value
                cur.execute(
                    "SELECT field_name, value FROM data "
                    "WHERE record_id = %s AND step_no = %s",
                    [rid, sno],
                )
                old = cur.fetchone()
                old_val = old["value"] if old else "(none)"
                field_label = old["field_name"] if old else f"step {sno}"

                cur.execute(
                    "UPDATE data SET value = %s WHERE record_id = %s AND step_no = %s",
                    [nv, rid, sno],
                )
                cur.execute(
                    "UPDATE steps SET field_value = %s, updated_at = NOW() "
                    "WHERE record_id = %s AND step_no = %s",
                    [nv, rid, sno],
                )
                results.append(f"- Step {sno} **{field_label}**: `{old_val}` → `{nv}`")
            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error during bulk update: {exc}"}
    else:
        _put_conn(conn)

    header = f"**Bulk update for test script {rid[:8]}…** ({len(results)} items):\n\n"
    return {"text": header + "\n".join(results)}


def tool_update_step(record_id: str, step_no: int, folder: str = "", **fields) -> dict:
    """Update step-level columns (action, page_url, element_tag, strategy, locator, field_name)."""
    rid = _resolve_record_id(record_id, folder=folder)
    if not rid:
        return _tool_result(
            f"No test script found for \"{record_id}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_id"],
            record_id=record_id,
            suggestions=["list scripts", "search <keyword>"],
        )

    allowed = {"action", "page_url", "element_tag", "strategy", "locator",
               "field_name", "field_value", "validation"}
    to_set = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not to_set:
        return _tool_result(
            "No valid fields to update. Allowed: " + ", ".join(sorted(allowed)),
            status="needs_update_fields",
            allowed_fields=sorted(allowed),
            record_id=rid,
            step_no=step_no,
        )

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Fetch old values
            cur.execute(
                "SELECT " + ", ".join(allowed) + " FROM steps "
                "WHERE record_id = %s AND step_no = %s LIMIT 1",
                [rid, step_no],
            )
            old_row = cur.fetchone()
            if not old_row:
                _put_conn(conn)
                return _tool_result(
                    f"Step {step_no} not found in test script {rid[:8]}….\n\n"
                    f"**Suggestion:** Show available steps: \"show steps for {record_id}\"",
                    status="step_not_found",
                    record_id=rid,
                    step_no=step_no,
                    suggestions=[f"show steps for {record_id}"],
                )

            set_clause = ", ".join(f"{k} = %s" for k in to_set)
            values = list(to_set.values()) + [rid, step_no]
            cur.execute(
                f"UPDATE steps SET {set_clause}, updated_at = NOW() "
                f"WHERE record_id = %s AND step_no = %s",
                values,
            )

            # If field_value changed, also update the data table
            if "field_value" in to_set:
                cur.execute(
                    "UPDATE data SET value = %s WHERE record_id = %s AND step_no = %s",
                    [to_set["field_value"], rid, step_no],
                )

            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error updating step: {exc}"}
    else:
        _put_conn(conn)

    lines = [f"**Updated step {step_no}:**\n",
             "| Field | Before | After |",
             "|---|---|---|"]
    for k, v in to_set.items():
        old_v = old_row.get(k, "—") or "—"
        lines.append(f"| {k} | `{old_v}` | `{v}` |")
    return {"text": "\n".join(lines)}


def _resolve_folder_ids(folder_name: str, cur) -> dict:
    """Resolve a folder path like 'Project001/Sub001/End001' to its UUIDs.
    Returns dict with parent_folder_id, sub_folder_id, end_folder_id (or None for each)."""
    result = {"parent_folder_id": None, "sub_folder_id": None, "end_folder_id": None}
    if not folder_name:
        return result
    parts = [p.strip() for p in folder_name.split("/") if p.strip()]
    if len(parts) >= 1:
        cur.execute("SELECT parent_folder_id FROM parent_folders WHERE parent_folder = %s", [parts[0]])
        row = cur.fetchone()
        if row:
            result["parent_folder_id"] = row["parent_folder_id"]
    if len(parts) >= 2 and result["parent_folder_id"]:
        cur.execute(
            "SELECT sub_folder_id FROM sub_folders "
            "WHERE sub_folder = %s AND sub_folder_parent = %s",
            [parts[1], result["parent_folder_id"]],
        )
        row = cur.fetchone()
        if row:
            result["sub_folder_id"] = row["sub_folder_id"]
    if len(parts) >= 3 and result["sub_folder_id"]:
        cur.execute(
            "SELECT end_folder_id FROM end_folders "
            "WHERE end_folder = %s AND end_folder_parent = %s",
            [parts[2], result["sub_folder_id"]],
        )
        row = cur.fetchone()
        if row:
            result["end_folder_id"] = row["end_folder_id"]
    return result


def tool_create_test_case(record_name: str = "", steps: list = None,
                          folder_name: str = "", copy_from: str = "",
                          author: str = "admin") -> dict:
    """Create a new test case/test script.

    If copy_from is provided (comma-separated list), searches session_meta
    for each name and combines all steps into one new test case.
    """
    if not record_name:
        return _tool_result(
            "Please provide the **filename** (test case name) for the new test case.",
            status="needs_record_name",
            missing=["record_name"],
        )
    if not folder_name:
        return _tool_result(
            "Please specify **which project** this test case belongs to "
            "(e.g. `Project001/Sub001/End001`).\n\n"
            "You can say **list projects** to see available projects.",
            status="needs_project_folder",
            missing=["folder_name"],
            record_name=record_name,
            suggestions=["list projects"],
        )

    copied_sources = []
    not_found = []

    # ── If no steps and no copy_from, ask which existing test case(s) to copy ──
    if (not steps or not isinstance(steps, list)) and not copy_from:
        return {"text": f"Got it! I'll create **\"{record_name}\"** in `{folder_name}`.\n\n"
                        "Which existing test case(s) should I copy the steps from?\n\n"
                        "You can provide **multiple names** separated by commas, e.g.:\n"
                        "`email, address`\n\n"
                        "I'll search `session_meta` for each and combine all steps "
                        "into the new test case.\n\n"
                        "You can say **list scripts** to see available test cases."}

    # ── Copy from one or more existing test cases ──
    if copy_from and (not steps or not isinstance(steps, list)):
        # Split comma-separated source names
        source_names = [s.strip() for s in copy_from.split(",") if s.strip()]

        # Resolve each source and collect steps
        all_steps = []
        copied_sources = []
        not_found = []
        seen_rids = set()  # deduplicate when multiple keywords match the same record

        conn = _get_conn()
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                for src_name in source_names:
                    # Find best match in session_meta
                    name_sql, name_params = _name_ilike_clause(src_name)
                    cur.execute(
                        "SELECT m.record_id, m.record_name, MIN(m.created_at) AS created_at "
                        "FROM session_meta m "
                        "LEFT JOIN steps s ON s.record_id = m.record_id "
                        f"WHERE ({name_sql}) "
                        "GROUP BY m.record_id, m.record_name "
                        "ORDER BY MIN(m.created_at) DESC",
                        name_params,
                    )
                    match = _best_source_match(cur.fetchall(), src_name, seen_rids)
                    if not match:
                        not_found.append(src_name)
                        continue

                    src_rid = str(match["record_id"])
                    src_rname = match["record_name"]

                    # Skip if we already copied this record from a previous keyword
                    if src_rid in seen_rids:
                        if src_rname not in copied_sources:
                            copied_sources.append(src_rname)
                        continue
                    seen_rids.add(src_rid)

                    # Fetch steps from this source
                    cur.execute(
                        "SELECT step_no, action, page_url, element_tag, "
                        "       field_name, field_value, strategy, locator, "
                        "       validation "
                        "FROM steps WHERE record_id = %s ORDER BY step_no",
                        [src_rid],
                    )
                    src_steps = cur.fetchall()

                    # Fetch data for this source
                    cur.execute(
                        "SELECT step_no, field_name, value "
                        "FROM data WHERE record_id = %s ORDER BY step_no",
                        [src_rid],
                    )
                    src_data = {r["step_no"]: r for r in cur.fetchall()}

                    if not src_steps:
                        not_found.append(f"{src_rname} (no steps)")
                        continue

                    copied_sources.append(src_rname)
                    for ss in src_steps:
                        d = src_data.get(ss["step_no"]) or {}
                        all_steps.append({
                            "action":      ss["action"] or "click",
                            "page_url":    ss["page_url"] or "",
                            "element_tag":  ss["element_tag"] or "",
                            "field_name":  d.get("field_name", ss.get("field_name", "")),
                            "field_value": d.get("value", ss.get("field_value", "")),
                            "strategy":    ss["strategy"] or "",
                            "locator":     ss["locator"] or "",
                            "validation":  ss["validation"] or "",
                        })
        except Exception:
            _put_conn(conn, discard=True)
            raise
        else:
            _put_conn(conn)

        if not all_steps:
            return _tool_result(
                f"No steps found for: {', '.join(not_found)}.\n\n"
                f"Try **list scripts** to see available test cases.",
                status="no_source_steps_found",
                record_name=record_name,
                copy_from=copy_from,
                not_found=not_found,
                suggestions=["list scripts"],
            )

        if not_found:
            # Some sources were found, some not — warn but continue
            pass

        steps = all_steps
        # Fall through with combined steps + record which sources were used


    record_id = str(_uuid.uuid4())

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Resolve folder IDs
            folder_ids = _resolve_folder_ids(folder_name, cur)

            # Determine file_order: next available in the target folder
            if folder_name:
                cur.execute(
                    "SELECT COALESCE(MAX(file_order), 0) + 1 AS next_order "
                    "FROM steps WHERE folder_name = %s",
                    [folder_name],
                )
                file_order = cur.fetchone()["next_order"]
            else:
                file_order = 1

            # Insert session_meta
            cur.execute(
                "INSERT INTO session_meta "
                "(record_id, record_name, recorder, folder_name, "
                " parent_folder_id, sub_folder_id, end_folder_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                [record_id, record_name, author, folder_name or None,
                 folder_ids["parent_folder_id"],
                 folder_ids["sub_folder_id"],
                 folder_ids["end_folder_id"]],
            )

            created_steps = []
            for i, step in enumerate(steps, 1):
                action     = step.get("action", "click")
                page_url   = step.get("page_url", "")
                element_tag = step.get("element_tag", "")
                field_name = step.get("field_name", "")
                field_value = step.get("field_value", "")
                strategy   = step.get("strategy", "")
                locator_val = step.get("locator", "")
                validation = step.get("validation", "")
                _pos_x     = step.get("pos_x")
                _pos_y     = step.get("pos_y")

                # Build raw_event JSON
                raw_event = {
                    "id": field_name,
                    "tag": element_tag,
                    "url": page_url,
                    "text": field_value,
                    "value": field_value if action in ("change", "input") else "",
                    "action": action,
                    "locators": {},
                }
                # Build locators_raw
                locators_raw = {}
                if strategy and locator_val:
                    normalized_strategy = strategy.strip().lower()
                    normalized_locator = locator_val.strip()
                    if normalized_strategy not in {"xpath", "id", "name", "css", "linktext", "partiallinktext", "value", "placeholder", "class", "classname", "tagname", "href", "text", "type", "role", "title", "alt", "arialabel", "datatestid"} and ":" in normalized_locator:
                        prefix, loc_value = normalized_locator.split(":", 1)
                        normalized_strategy = prefix.strip().lower()
                        normalized_locator = loc_value.strip()
                        if normalized_strategy == "id" and not normalized_locator.startswith("#"):
                            normalized_locator = f"#{normalized_locator}"
                    elif normalized_strategy not in {"xpath", "id", "name", "css", "linktext", "partiallinktext", "value", "placeholder", "class", "classname", "tagname", "href", "text", "type", "role", "title", "alt", "arialabel", "datatestid"}:
                        normalized_strategy = "xpath"

                    if normalized_strategy:
                        locators_raw[normalized_strategy] = normalized_locator
                raw_event["locators"] = locators_raw

                # Insert into data table
                cur.execute(
                    "INSERT INTO data (record_id, step_no, field_name, value, folder_name) "
                    "VALUES (%s, %s, %s, %s, %s) RETURNING id",
                    [record_id, i, field_name, field_value, folder_name or None],
                )
                data_id = cur.fetchone()["id"]

                # Insert primary locator into locators table
                locator_id = None
                if strategy and locator_val:
                    cur.execute(
                        "INSERT INTO locators "
                        "(record_id, step_no, strategy, locator, is_primary, locator_rank, "
                        " pos_x, pos_y, folder_name) "
                        "VALUES (%s, %s, %s, %s, TRUE, 1, %s, %s, %s) RETURNING id",
                        [record_id, i, strategy, locator_val, _pos_x, _pos_y, folder_name or None],
                    )
                    locator_id = cur.fetchone()["id"]

                # Insert into steps table
                cur.execute(
                    "INSERT INTO steps "
                    "(record_id, step_no, action, page_url, element_tag, "
                    " locator_id, data_id, raw_event, recorder, folder_name, "
                    " locators_raw, field_name, field_value, "
                    " strategy, locator, is_primary, locator_rank, "
                    " folder_order, file_order, author, file_type, "
                    " parent_folder_id, sub_folder_id, end_folder_id, validation) "
                    "VALUES (%s, %s, %s, %s, %s, "
                    "        %s, %s, %s, %s, %s, "
                    "        %s, %s, %s, "
                    "        %s, %s, TRUE, 1, "
                    "        1, %s, %s, 'step', "
                    "        %s, %s, %s, %s)",
                    [record_id, i, action, page_url, element_tag,
                     locator_id, data_id, json.dumps(raw_event), author,
                     folder_name or None,
                     json.dumps(locators_raw) if locators_raw else None,
                     field_name, field_value,
                     strategy, locator_val,
                     file_order, author,
                     folder_ids["parent_folder_id"],
                     folder_ids["sub_folder_id"],
                     folder_ids["end_folder_id"],
                     validation or None],
                )

                created_steps.append(
                    f"| {i} | {action} | {element_tag or '—'} "
                    f"| {strategy}:{locator_val} | {field_name or '—'} "
                    f"| {(field_value or '—')[:20]} |"
                )

            # Update end_folder file count if applicable
            if folder_ids["end_folder_id"]:
                cur.execute(
                    "UPDATE end_folders SET end_file_order = end_file_order + 1, "
                    "last_updated = NOW() WHERE end_folder_id = %s",
                    [folder_ids["end_folder_id"]],
                )

            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error creating test case: {exc}"}
    else:
        _put_conn(conn)

    copied_line = ""
    if copy_from:
        src_display = ", ".join(copied_sources) if copied_sources else copy_from
        copied_line = f"- Copied from: `{src_display}`\n"
        if not_found:
            copied_line += f"- Not found: {', '.join(not_found)}\n"
    lines = [
        f"Test case **\"{record_name}\"** created successfully!",
        f"- Record ID: `{record_id}`",
        f"- Folder: `{folder_name or '(none)'}`",
        f"- Steps: {len(steps)}",
        copied_line,
        "| # | Action | Element | Locator | Field | Value |",
        "|---|---|---|---|---|---|",
    ] + created_steps
    return {"text": "\n".join(lines)}


def tool_search_steps(query: str, search_in: str = "all") -> dict:
    """Search across all scripts for steps matching a data value or locator.

    Args:
        query: The text to search for (case-insensitive partial match).
        search_in: Where to search — "data", "locator", or "all" (default).
    """
    search_in = search_in.lower().strip()
    if search_in not in ("data", "locator", "all"):
        search_in = "all"

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            conditions = []
            params = []
            if search_in in ("data", "all"):
                conditions.append("s.field_name ILIKE %s")
                params.append(f"%{query}%")
                conditions.append("s.field_value ILIKE %s")
                params.append(f"%{query}%")
            if search_in in ("locator", "all"):
                conditions.append("s.locator ILIKE %s")
                params.append(f"%{query}%")
                conditions.append("s.strategy ILIKE %s")
                params.append(f"%{query}%")

            where = " OR ".join(conditions)
            cur.execute(f"""
                SELECT s.record_id, s.step_no, s.action, s.element_tag,
                       s.field_name, s.field_value, s.strategy, s.locator,
                       COALESCE(m.record_name, '') AS record_name,
                       s.folder_name
                FROM steps s
                LEFT JOIN session_meta m ON m.record_id = s.record_id
                WHERE {where}
                ORDER BY s.created_at DESC
                LIMIT 30
            """, params)
            rows = cur.fetchall()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not rows:
        return _tool_result(
            f"No steps found matching \"{query}\" (searched: {search_in}). "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** with a different keyword.",
            status="no_step_search_results",
            query=query,
            search_in=search_in,
            suggestions=["list scripts", "search <keyword>"],
        )

    lines = [f"**Found {len(rows)} step(s) matching \"{query}\"** (in: {search_in}):\n",
             "| Test Script | Step | Action | Field | Value | Locator |",
             "|---|---|---|---|---|---|"]
    for r in rows:
        name = r["record_name"] or str(r["record_id"])[:8]
        loc = f"{r['strategy']}:{r['locator']}" if r.get("strategy") else "—"
        lines.append(
            f"| {name[:20]} | {r['step_no']} | {r['action']} "
            f"| {r['field_name'] or '—'} | {(r['field_value'] or '—')[:20]} "
            f"| {loc[:30]} |"
        )
    return {"text": "\n".join(lines)}


def tool_show_steps(record_id: str) -> dict:
    """Show all steps for a test script."""
    rid = _resolve_record_id(record_id)
    if not rid:
        return _tool_result(
            f"No test script found for \"{record_id}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_id"],
            record_id=record_id,
            suggestions=["list scripts", "search <keyword>"],
        )

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT s.step_no,
                       s.page_url,
                       s.action,
                       s.element_tag,
                       COALESCE(l.strategy, s.strategy, '') AS identity,
                       COALESCE(l.locator, s.locator, '') AS label,
                       COALESCE(d.field_name, s.field_name, '') AS field_name,
                       COALESCE(d.value, s.field_value, '') AS field_value
                FROM steps s
                LEFT JOIN locators l ON l.id = s.locator_id
                LEFT JOIN data d ON d.id = s.data_id
                WHERE s.record_id = %s
                ORDER BY s.step_no
            """, [rid])
            rows = cur.fetchall()
            cur.execute("SELECT record_name, folder_name FROM session_meta WHERE record_id = %s", [rid])
            meta = cur.fetchone()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not rows:
        return _tool_result(
            f"No steps found for test script {rid[:8]}….\n\n"
            f"**Suggestion:** The test script exists but has no steps. "
            f"You can create steps with: \"create test case <name>\"",
            status="no_steps_in_script",
            record_id=rid,
            suggestions=["create test case <name>"],
        )

    name = meta["record_name"] if meta else rid[:8]
    folder = (meta["folder_name"] if meta else "") or "—"
    lines = [
        f"**Steps for \"{name}\"** ({len(rows)} steps)",
        f"**Project:** {folder}",
        "",
        "| # | Steps | Page URL | Action | Element | Identity | Label | Fieldname | Data |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        lines.append(
            f"| {r['step_no']}"
            f" | {_step_summary(r)[:40] or '—'}"
            f" | {(r['page_url'] or '—')[:40]}"
            f" | {r['action'] or '—'}"
            f" | {r['element_tag'] or '—'}"
            f" | {r['identity'] or '—'}"
            f" | {(r['label'] or '—')[:40]}"
            f" | {r['field_name'] or '—'}"
            f" | {(r['field_value'] or '—')[:40]} |"
        )
    return {"text": "\n".join(lines)}


def tool_delete_test_case(record_name: str = "", folder: str = "",
                          confirm: bool = False) -> dict:
    """Delete a test case and all related data (session_meta, steps, data, locators).

    Flow:
      1. If record_name is empty → ask for the filename.
      2. If multiple matches exist → list them and ask which project.
      3. If confirm is False → show summary and ask for confirmation.
      4. If confirm is True → delete everything.
    """
    if not record_name:
        return _tool_result(
            "Which test case do you want to delete? "
            "Please provide the **filename** (test case name).",
            status="needs_record_name",
            missing=["record_name"],
        )

    record_name = record_name.strip().strip('"').strip("'")

    rid = None
    try:
        _uuid.UUID(record_name)
        rid = record_name
    except ValueError:
        rid = None

    # Look up matching test scripts
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if rid:
                cur.execute(
                    "SELECT m.record_id, m.record_name, "
                    "       MIN(s.folder_name) AS folder_name, "
                    "       COUNT(s.step_no) AS step_count "
                    "FROM session_meta m "
                    "LEFT JOIN steps s ON s.record_id = m.record_id "
                    "WHERE m.record_id = %s "
                    "GROUP BY m.record_id, m.record_name",
                    [rid],
                )
            else:
                name_sql, name_params = _name_ilike_clause(record_name)
                if folder:
                    cur.execute(
                        "SELECT m.record_id, m.record_name, "
                        "       MIN(s.folder_name) AS folder_name, "
                        "       COUNT(s.step_no) AS step_count "
                        "FROM session_meta m "
                        "LEFT JOIN steps s ON s.record_id = m.record_id "
                        f"WHERE ({name_sql}) AND s.folder_name ILIKE %s "
                        "GROUP BY m.record_id, m.record_name "
                        "ORDER BY MIN(m.created_at) DESC",
                        name_params + [f"%{folder}%"],
                    )
                else:
                    cur.execute(
                        "SELECT m.record_id, m.record_name, "
                        "       MIN(s.folder_name) AS folder_name, "
                        "       COUNT(s.step_no) AS step_count "
                        "FROM session_meta m "
                        "LEFT JOIN steps s ON s.record_id = m.record_id "
                        f"WHERE ({name_sql}) "
                        "GROUP BY m.record_id, m.record_name "
                        "ORDER BY MIN(m.created_at) DESC",
                        name_params,
                    )
            matches = cur.fetchall()
    except Exception:
        _put_conn(conn, discard=True)
        raise
    else:
        _put_conn(conn)

    if not matches:
        return _tool_result(
            f"No test script found for \"{record_name}\". "
            f"Try **list scripts** to see available test scripts "
            f"or **search <keyword>** to find one.",
            status="needs_record",
            missing=["record_name"],
            record_name=record_name,
            folder=folder,
            suggestions=["list scripts", "search <keyword>"],
        )

    # Multiple matches → ask which project
    if len(matches) > 1 and not folder and not rid:
        lines = [f"Multiple test scripts found matching **\"{record_name}\"**. "
                 f"Which one do you want to delete?\n"]
        for i, m in enumerate(matches, 1):
            proj = m["folder_name"] or "(no project)"
            lines.append(f"{i}. **{m['record_name']}** — Project: `{proj}` ({m['step_count']} steps)")
        lines.append("\nPlease specify the project name, e.g. "
                     f"\"delete {record_name} from Project001\"")
        return _tool_result(
            "\n".join(lines),
            status="ambiguous_delete_target",
            record_name=record_name,
            folder_required=True,
            matches=[{"record_id": str(m["record_id"]), "record_name": m["record_name"], "folder_name": m["folder_name"] or "", "step_count": m["step_count"]} for m in matches],
        )

    target = matches[0]
    rid = str(target["record_id"])
    name = target["record_name"]
    proj = target["folder_name"] or "(no project)"
    steps = target["step_count"]

    # Not confirmed → show summary and ask
    if not confirm:
        return {
            "text": f"You are about to delete:\n\n"
                    f"- **Test case:** {name}\n"
                    f"- **Project:** `{proj}`\n"
                    f"- **Steps:** {steps}\n"
                    f"- **Record ID:** `{rid}`\n\n"
                    f"This will permanently remove all related data from "
                    f"**session_meta**, **steps**, **data**, and **locators**.\n\n"
                    f"To confirm, reply with **yes**, **remove**, **ok**, **okay**, **fine**, **yeah**, **of course**, **yep**, **cool**, or say **\"delete {name}\"**."
        }

    # Confirmed → delete related rows across all relevant tables.
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE run_table SET locator_id = NULL WHERE record_id = %s AND locator_id IS NOT NULL",
                [rid],
            )
            cur.execute(
                "UPDATE run_table SET data_id = NULL WHERE record_id = %s AND data_id IS NOT NULL",
                [rid],
            )
            cur.execute(
                "UPDATE steps SET locator_id = NULL WHERE record_id = %s AND locator_id IS NOT NULL",
                [rid],
            )
            cur.execute(
                "UPDATE steps SET data_id = NULL WHERE record_id = %s AND data_id IS NOT NULL",
                [rid],
            )

            cur.execute("DELETE FROM run_table WHERE record_id = %s", [rid])
            run_del = cur.rowcount
            cur.execute("DELETE FROM steps WHERE record_id = %s", [rid])
            step_del = cur.rowcount
            cur.execute("DELETE FROM data WHERE record_id = %s", [rid])
            data_del = cur.rowcount
            cur.execute("DELETE FROM locators WHERE record_id = %s", [rid])
            loc_del = cur.rowcount
            cur.execute("DELETE FROM session_meta WHERE record_id = %s", [rid])
            meta_del = cur.rowcount
            conn.commit()
    except Exception as exc:
        conn.rollback()
        _put_conn(conn, discard=True)
        return {"text": f"Error deleting test case: {exc}"}
    else:
        _put_conn(conn)

    return {
        "text": f"Test case **\"{name}\"** has been deleted.\n\n"
                f"- session_meta: {meta_del} record(s)\n"
                f"- run_table: {run_del} row(s)\n"
                f"- steps: {step_del} row(s)\n"
                f"- data: {data_del} row(s)\n"
                f"- locators: {loc_del} row(s)"
    }


# ═══════════════════════════════════════════════════════════════════════════
# AGENT — interprets user messages through the LLM and dispatches tools
# ═══════════════════════════════════════════════════════════════════════════

AGENT_SYSTEM_PROMPT = textwrap.dedent("""\
You are a helpful assistant for the WebConX Automation Platform.
You help users manage their test automation projects stored in a PostgreSQL database.

You have these tools available. To use one, respond with EXACTLY one JSON block:
```json
{"tool": "<tool_name>", "args": {<arguments>}}
```

Available tools:
1. list_sessions(limit=20) — List recent test scripts / test cases
2. search_sessions(query) — Search test scripts by name or folder
3. list_projects() — List all project folders
4. create_project(folder_name, author="admin") — Create a new project folder
5. download_session(record_id, fmt, folder="") — Get download link for test script steps (fmt: csv, pdf, doc). If multiple test scripts share the same name, pass folder to specify which project.
6. update_data_value(record_id, new_value, step_no=0, field_name="") — Update a step's data value (by step_no or field_name)
7. bulk_update_data(record_id, updates) — Update multiple steps at once. updates is a list: [{"step_no":1,"new_value":"x"}, ...] or [{"field_name":"f","new_value":"y"}, ...]
8. update_step(record_id, step_no, action=, page_url=, element_tag=, strategy=, locator=, field_name=, field_value=, validation=) — Update step columns
9. show_steps(record_id) — Show all steps for a test script (accepts UUID or test script name)
10. search_steps(query, search_in="all") — Search across all test scripts for steps by data value or locator. search_in: "data", "locator", or "all"
11. create_test_case(record_name, folder_name, copy_from="", author="admin") — Create a new test case by copying steps/data/locators from existing test cases. copy_from is a comma-separated list of KEYWORDS to search in session_meta (fuzzy match). Pass the user's raw words as-is, e.g. "email, address". Do NOT guess or fabricate full test case names.
12. delete_test_case(record_name, folder="", confirm=False) — Delete a test case and all related data. record_name may be either the test case name or a UUID. First call without confirm to see a summary, then call with confirm=true to execute the deletion.

IMPORTANT: "Test scripts" and "test cases" both refer to recorded sessions (steps filename / record_name in session_meta).
When the user says "test script", "test case", or "session", they mean the same thing.

RULES:
- If the user asks to list/show test scripts/test cases/sessions, use list_sessions or search_sessions.
- Treat search-style phrases like "search for", "look", "look for", "find", "find for", "is there", "any file", "look file", "how about", "what about", "related to", "anything about", and patterns like "file name=..." or "record_name=..." as test script search requests when the user is asking about files/test cases/projects. Conversational follow-ups like "how about related to entering names?" should call search_sessions with the extracted subject ("entering names").
- If the user asks to create a project/folder, use create_project.
- If the user asks to create a test case/test script, ask for: 1) **filename** (new test case name), 2) **project** (folder path), 3) **copy from** (comma-separated keywords to search for existing test cases to combine). Then call create_test_case with record_name, folder_name, and copy_from. Do NOT fabricate steps yourself. Pass the user's raw keywords as-is — the search is fuzzy. Example: if user says "populate email and address", pass copy_from="email, address".
- If the user asks to download, pull, get, or ask for a copy/document, ALWAYS ask which **file format** they want (csv, pdf, or doc) before calling download_session. If the user already specified the format, use it directly.
- If the user specifies a project/folder for the download, pass it as the folder argument.
- If the user asks to download and there are duplicates, the tool will list them — do NOT pick one yourself.
- If the user asks to update a single step's data/value, use update_data_value.
- If the user asks to update multiple steps' data at once, use bulk_update_data.
- If the user asks to update step properties (action, locator, strategy, url, etc.), use update_step.
- If the user asks to show steps, use show_steps. Treat phrases like "show ... steps", "view ... steps", and "where are the steps for ..." as show_steps requests.
- If the user asks to search/find steps by data, value, field, or locator, use search_steps.
- If the user asks to delete/remove/omit a test case/test script, use delete_test_case. ALWAYS call it first WITHOUT confirm=true to show the summary. Only call with confirm=true AFTER the user explicitly confirms the deletion (e.g. "yes", "remove", "ok", "okay", "fine", "yeah", "of course", "yep", "cool").
- If the user sends a short confirmation after seeing a delete summary, call delete_test_case again with the same record_name (and folder if specified) and confirm=true.
- For general questions, answer directly without a tool call.
- ALWAYS respond with either a tool call JSON block OR a direct text answer, never both.
- When you need a record_id but only have a name, pass the name as record_id — the tool will resolve it.
""")

TOOL_RESPONSE_SYSTEM_PROMPT = textwrap.dedent("""\
You are the final response assistant for the WebConX Automation Platform.
You will be given the user's request, the selected tool, the tool arguments, and the raw tool result.

Rules:
- Do not invent data, IDs, projects, counts, filenames, or links.
- Preserve markdown tables, download prompts, confirmation prompts, and structured lists from the tool result when they are already useful.
- If the tool result is already a complete answer, return it with little or no rewriting.
- Keep the wording concise and user-facing.
- Do not mention internal tools, JSON, chain-of-thought, or hidden reasoning.
- If the tool result contains an error or asks a follow-up question, preserve that clearly.
""")

DIRECT_RESPONSE_SYSTEM_PROMPT = textwrap.dedent("""\
You are the final user-facing assistant for the WebConX Automation Platform.
You will be given a user request and a draft LLM response.

Rules:
- Rewrite the draft into the exact text the user should see.
- Never mention tool calls, internal planning, hidden reasoning, or whether a tool is needed.
- Keep casual replies natural and short.
- Preserve factual content from the draft when it is useful.
- If the draft is already a good user-facing reply, return it with minimal changes.
""")

SEARCH_SESSION_PARSE_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is asking to search for a recorded test script.

Return EXACTLY one JSON object:
{"query": "..."}
or
{"query": null}

Use a non-null query only when the user is looking for a test script, test case, session, or file by name.
Examples that should return a query:
- "search for login test case"
- "is there any script for smoke"
- "do we have a file named address"
- "file name=test case 1"

Return null for other intents such as:
- showing steps
- downloading a file
- deleting a test case
- creating a project or test case
- updating data, steps, locators, or values

If it is a search request, return only the search phrase, not extra explanation.
""")

SEARCH_TRIGGER_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is likely asking to search for, verify, or check the existence of a recorded test script.

Return EXACTLY one JSON object:
{"should_parse": true}
or
{"should_parse": false}

Return true only when the next step should be to run the search-intent parser.
Return false for unrelated intents such as showing steps, downloading, deleting, creating, or updating.
""")

SHOW_STEPS_PARSE_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is asking to show the steps for a recorded test script.

Return EXACTLY one JSON object:
{"record_id": "..."}
or
{"record_id": null}

Use a non-null record_id only when the user is asking to show, view, inspect, or list the steps of a test script.
Return only the test script name or record identifier.

Return null for unrelated intents such as searching scripts, downloading, deleting, creating, or updating.
""")

SHOW_STEPS_TRIGGER_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is likely asking to show, inspect, outline, or walk through the steps of a recorded test script.

Return EXACTLY one JSON object:
{"should_parse": true}
or
{"should_parse": false}

Return true only when the next step should be to run the show-steps parser.
Return false for unrelated intents such as searching scripts, downloading, deleting, creating, or updating.
""")

DOWNLOAD_PARSE_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is asking to download or export a recorded test script.

Return EXACTLY one JSON object:
{"record_id": "...", "fmt": "csv|pdf|doc|", "folder": "..."}
or
{"record_id": null, "fmt": "", "folder": ""}

Use a non-null record_id only when the user wants to download, export, pull, or get a copy of a test script.
If the format is not stated, return an empty fmt string.
If no folder/project is stated, return an empty folder string.

Return null for unrelated intents such as searching scripts, showing steps, deleting, creating, or updating.
""")

DOWNLOAD_TRIGGER_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is likely asking to download, export, save, send, or share a recorded test script.

Return EXACTLY one JSON object:
{"should_parse": true}
or
{"should_parse": false}

Return true only when the next step should be to run the download parser.
Return false for unrelated intents such as searching scripts, showing steps, deleting, creating, or updating.
""")

DELETE_PARSE_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is asking to delete a recorded test script.

Return EXACTLY one JSON object:
{"record_name": "...", "folder": "..."}
or
{"record_name": null, "folder": ""}

Use a non-null record_name only when the user wants to delete, remove, omit, ommit, archive, drop, purge, erase, discard, trash, wipe, or permanently remove a test script.
If no folder/project is stated, return an empty folder string.

Return null for unrelated intents such as searching scripts, showing steps, downloading, creating, or updating.
""")

DELETE_TRIGGER_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is likely asking to delete, remove, archive, drop, purge, erase, discard, trash, wipe, or permanently remove a recorded test script.

Return EXACTLY one JSON object:
{"should_parse": true}
or
{"should_parse": false}

Return true only when the next step should be to run the delete parser.
Return false for unrelated intents such as searching scripts, showing steps, downloading, creating, or updating.
""")

UPDATE_PARSE_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is asking to update data in a recorded test script.

Return EXACTLY one JSON object in one of these forms:
{"tool": "update_data_value", "args": {"record_id": "...", "new_value": "...", "step_no": 0, "field_name": "...", "folder": "..."}}
{"tool": "bulk_update_data", "args": {"record_id": "...", "updates": [{"step_no": 1, "new_value": "..."}, {"field_name": "...", "new_value": "..."}], "folder": "..."}}
{"tool": "update_step", "args": {"record_id": "...", "step_no": 1, "action": "...", "page_url": "...", "element_tag": "...", "strategy": "...", "locator": "...", "field_name": "...", "field_value": "...", "validation": "...", "folder": "..."}}
or
{"tool": null, "args": {}}

Rules:
- Use update_data_value for a single data/value change.
- Use bulk_update_data only when the user clearly wants multiple values updated at once.
- Use update_step for step properties like action, url, element tag, strategy, locator, field name, field value, or validation.
- Keep only fields that are explicitly provided.
- Use step_no only when the user explicitly gives a step number.
- Use field_name when the user identifies a field by name.
- Use the test script name as record_id when no UUID is given.
- If the user specifies a project or folder, include it as folder.
- When duplicate test case names are possible, preserve any project/folder wording instead of dropping it.
- Return tool=null for unrelated intents.
""")

UPDATE_TRIGGER_SYSTEM_PROMPT = textwrap.dedent("""\
You classify whether a user message is likely asking to update or revise data or step properties in a recorded test script.

Return EXACTLY one JSON object:
{"should_parse": true}
or
{"should_parse": false}

Return true only when the next step should be to run the update parser.
Return false for unrelated intents such as searching scripts, showing steps, downloading, deleting, or creating.
""")

SPELLING_CORRECTION_SYSTEM_PROMPT = textwrap.dedent("""\
You are a typo and misspelling corrector for a test automation chatbot.

Your ONLY job is to fix words that are clearly misspelled (i.e. garbled / scrambled letters).
Do NOT change words that are already correct English words, even if they seem grammatically awkward in context
— they may be test case names, proper nouns, or technical identifiers.

Return ONLY the corrected sentence — no explanation, no commentary, no extra text.
If the message is already correct, return it unchanged.
Do NOT change word order, tense, plurality, or phrasing.
Do NOT translate or paraphrase.

Examples of what to fix (garbled letters):
  sumitting  → submitting
  searcing   → searching
  deleate    → delete
  stpes      → steps
  downlod    → download
  shwo       → show

Examples of what NOT to change (already valid words):
  city       → city        (do NOT change to "cities")
  name       → name        (do NOT change to "names")
  populate   → populate    (do NOT change)
  login      → login       (do NOT change)
""")


def _llm_correct_spelling(text: str) -> str:
    """Use the LLM to correct spelling and grammar in the user message.

    Returns the corrected text, or the original if the LLM fails or returns
    something implausible (e.g. the model started explaining instead of correcting).
    A word-level guard reverts any LLM "correction" where the original word was
    already a valid English word (to prevent false pluralisation / rephrasing).
    """
    if not text or not text.strip():
        return text

    corrected = _call_ollama(text.strip(), system=SPELLING_CORRECTION_SYSTEM_PROMPT)
    if not corrected or corrected.startswith("[Error]"):
        return text

    corrected = corrected.strip()
    # Sanity guard: if the response is way longer than the input, the LLM explained
    # instead of correcting — discard it and keep the original.
    if len(corrected) > max(len(text) * 2, len(text) + 80):
        return text

    # Word-level guard: revert individual word changes where the original word
    # is already a real word (to stop false corrections like "city" → "cities").
    # Also always preserves original casing (stops the LLM capitalising "How").
    orig_words = re.findall(r'\S+', text)
    corr_words = re.findall(r'\S+', corrected)
    if len(orig_words) != len(corr_words):
        # Word count changed — LLM added or removed words. Discard.
        return text
    result_words = []
    for ow, cw in zip(orig_words, corr_words):
        ol, cl = ow.lower().strip('.,!?;:'), cw.lower().strip('.,!?;:')
        if ol == cl:
            # Same word, possibly different case — always keep original casing.
            result_words.append(ow)
        else:
            # Different word: only keep the LLM fix if the original was a misspelling.
            is_already_valid = bool(
                difflib.get_close_matches(ol, _FUZZY_VOCABULARY, n=1, cutoff=0.92)
            ) or len(ol) <= 4
            result_words.append(ow if is_already_valid else cw)
    return " ".join(result_words)


TOOL_DISPATCH = {
    "list_sessions":             tool_list_sessions,
    "search_sessions":           tool_search_sessions,
    "list_projects":             tool_list_projects,
    "create_project":            tool_create_project,
    "generate_workflow_test_case": tool_generate_workflow_test_case,
    "download_session":          tool_download_session,
    "update_data_value":         tool_update_data_value,
    "bulk_update_data":          tool_bulk_update_data,
    "update_step":               tool_update_step,
    "show_steps":                tool_show_steps,
    "search_steps":              tool_search_steps,
    "create_test_case":          tool_create_test_case,
    "delete_test_case":          tool_delete_test_case,
}


def _extract_tool_call(llm_response: str) -> Optional[dict]:
    """Try to extract a JSON tool call from the LLM response."""
    payload = _extract_json_object(llm_response)
    if isinstance(payload, dict) and "tool" in payload:
        return payload
    return None


def _build_history_text(conversation_history: list | None, limit: int = 6) -> str:
    if not conversation_history:
        return ""

    history_text = ""
    for msg in conversation_history[-limit:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_text += f"{role}: {msg['content']}\n\n"
    return history_text


def _clean_final_reply(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r'^(?:here is the final response|final response|response)\s*:\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'^(?:sure|certainly)\s*,?\s*', '', cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _render_tool_result_with_llm(*, user_message: str, tool_name: str, tool_args: dict, result: dict) -> str:
    tool_text = str(result.get("text") or "Done.")
    prompt = (
        f"User request:\n{user_message}\n\n"
        f"Tool used:\n{tool_name}\n\n"
        f"Tool arguments:\n{json.dumps(tool_args, ensure_ascii=True, default=str)}\n\n"
        f"Tool result object:\n{json.dumps(result, ensure_ascii=True, default=str)}\n\n"
        f"Raw tool result:\n{tool_text}\n\n"
        "Write the final assistant reply. Preserve any useful markdown tables or structured output."
    )
    llm_reply = _clean_final_reply(_call_ollama(prompt, system=TOOL_RESPONSE_SYSTEM_PROMPT))
    if not llm_reply or llm_reply.startswith("[Error]"):
        return tool_text

    if "|" in tool_text and "|" not in llm_reply:
        return tool_text
    if "What file format would you like?" in tool_text and "What file format would you like?" not in llm_reply:
        return tool_text
    if "You are about to delete:" in tool_text and "You are about to delete:" not in llm_reply:
        return tool_text
    return llm_reply


def _render_direct_reply_with_llm(*, user_message: str, llm_response: str) -> str:
    prompt = (
        f"User request:\n{user_message}\n\n"
        f"Draft response:\n{llm_response}\n\n"
        "Write the final assistant reply for the user."
    )
    final_reply = _clean_final_reply(_call_ollama(prompt, system=DIRECT_RESPONSE_SYSTEM_PROMPT))
    if not final_reply or final_reply.startswith("[Error]"):
        return llm_response.strip()
    return final_reply


def _execute_tool_interaction(tool_name: str, tool_args: dict, *, user_message: str, username: str) -> dict:
    if tool_name not in TOOL_DISPATCH:
        return {"reply": f"Unknown tool \"{tool_name}\". Try rephrasing your request.", "download": None}

    normalized_args = dict(tool_args or {})
    if tool_name == "create_project" and "author" not in normalized_args:
        normalized_args["author"] = username
    if tool_name == "generate_workflow_test_case" and "author" not in normalized_args:
        normalized_args["author"] = username

    if tool_name == "create_test_case":
        derived_keywords = _extract_create_keywords(user_message)
        if derived_keywords:
            normalized_args["copy_from"] = ", ".join(derived_keywords)
        if "author" not in normalized_args:
            normalized_args["author"] = username

    try:
        result = TOOL_DISPATCH[tool_name](**normalized_args)
    except Exception as exc:
        return {"reply": f"Error executing {tool_name}: {exc}", "download": None}

    return {
        "reply": _render_tool_result_with_llm(
            user_message=user_message,
            tool_name=tool_name,
            tool_args=normalized_args,
            result=result,
        ),
        "download": result.get("download"),
    }


def handle_chat_message(user_message: str, username: str = "admin",
                        conversation_history: list | None = None) -> dict:
    """
    Process a single chat message. Returns:
      {"reply": str, "download": dict|None}
    """
    if conversation_history is None:
        conversation_history = []

    # 1. LLM spelling/grammar correction (catches anything difflib misses).
    # 2. difflib fuzzy normalization on top (ultra-fast, no extra LLM round-trip).
    # The original user_message is kept for all LLM prompts and user-facing output.
    _msg = _fuzzy_normalize_message(_llm_correct_spelling(user_message))

    delete_request = _parse_delete_request(_msg)
    if delete_request:
        return _execute_tool_interaction(
            "delete_test_case",
            {
                "record_name": delete_request["record_name"],
                "folder": delete_request.get("folder", ""),
                "confirm": False,
            },
            user_message=user_message,
            username=username,
        )

    if _is_delete_confirmation(_msg):
        pending_delete = _pending_delete_from_history(conversation_history)
        if pending_delete:
            return _execute_tool_interaction(
                "delete_test_case",
                {
                    "record_name": pending_delete["record_name"],
                    "folder": pending_delete.get("folder", ""),
                    "confirm": True,
                },
                user_message=user_message,
                username=username,
            )
        return {
            "reply": "There is no pending delete to confirm.",
            "download": None,
        }

    if not delete_request:
        delete_request = _llm_parse_delete_request(_msg)
    if delete_request:
        return _execute_tool_interaction(
            "delete_test_case",
            {
                "record_name": delete_request["record_name"],
                "folder": delete_request.get("folder", ""),
                "confirm": False,
            },
            user_message=user_message,
            username=username,
        )

    show_steps_query = _parse_show_steps_request(_msg)
    if not show_steps_query:
        show_steps_query = _llm_parse_show_steps_request(_msg)
    if show_steps_query:
        return _execute_tool_interaction(
            "show_steps",
            {"record_id": show_steps_query},
            user_message=user_message,
            username=username,
        )

    download_request = _parse_download_request(_msg)
    if not download_request:
        download_request = _llm_parse_download_request(_msg)
    if download_request:
        return _execute_tool_interaction(
            "download_session",
            download_request,
            user_message=user_message,
            username=username,
        )

    update_request = _parse_update_step_request(_msg)
    if not update_request:
        update_request = _parse_bulk_update_data_request(_msg)
    if not update_request:
        update_request = _parse_update_data_value_request(_msg)
    if not update_request:
        update_request = _llm_parse_update_request(_msg)
    if update_request:
        return _execute_tool_interaction(
            update_request["tool"],
            update_request["args"],
            user_message=user_message,
            username=username,
        )

    search_query = _parse_search_sessions_request(_msg)
    if not search_query:
        search_query = _llm_parse_search_sessions_request(_msg)
    if search_query:
        return _execute_tool_interaction(
            "search_sessions",
            {"query": search_query},
            user_message=user_message,
            username=username,
        )

    workflow_generate_request = _parse_generate_workflow_request(_msg)
    if workflow_generate_request is not None:
        return _execute_tool_interaction(
            "generate_workflow_test_case",
            {
                "workflow_query": workflow_generate_request.get("workflow_query", ""),
                "folder_name": "AI Gen",
            },
            user_message=user_message,
            username=username,
        )

    # Build prompt with recent conversation context
    history_text = _build_history_text(conversation_history)

    prompt = f"{history_text}User: {user_message}"

    llm_response = _call_ollama(prompt, system=AGENT_SYSTEM_PROMPT)
    # Check if the LLM wants to call a tool
    tool_call = _extract_tool_call(llm_response)

    # Handle truncated / malformed tool call (LLM ran out of tokens)
    if tool_call is None and '"tool"' in llm_response and '{' in llm_response:
        # Looks like it tried to call a tool but JSON was truncated
        if 'create_test_case' in llm_response:
            return {
                "reply": "I'd be happy to help create a test case! "
                         "Please provide:\n\n"
                         "1. **Filename** — the new test case name\n"
                         "2. **Project** — which project folder it belongs to\n"
                         "3. **Copy from** — keywords to search for existing test cases to copy from\n\n"
                         "You can say **list projects** or **list scripts** to see what's available.",
                "download": None,
            }
        return {
            "reply": "I couldn't complete that request — please try rephrasing "
                     "or breaking it into smaller steps.",
            "download": None,
        }
    if tool_call and "tool" in tool_call:
        tool_name = tool_call["tool"]
        tool_args = tool_call.get("args", {})
        return _execute_tool_interaction(
            tool_name,
            tool_args,
            user_message=user_message,
            username=username,
        )

    # No tool call — rewrite the draft into a user-facing reply
    return {"reply": _render_direct_reply_with_llm(user_message=user_message, llm_response=llm_response), "download": None}


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════

def main():
    import argparse
    parser = argparse.ArgumentParser(description="WebConX Chatbot Agent")
    parser.add_argument("--chat", action="store_true",
                        help="Start interactive chat mode.")
    parser.add_argument("--list", action="store_true",
                        help="List recent test scripts and exit.")
    parser.add_argument("--ollama-api", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--max-tokens", type=int, default=None)
    args = parser.parse_args()

    global OLLAMA_API, LLM_MODEL, LLM_MAX_TOKENS
    if args.ollama_api:
        OLLAMA_API = args.ollama_api
    if args.model:
        LLM_MODEL = args.model
    if args.max_tokens:
        LLM_MAX_TOKENS = args.max_tokens

    if args.list:
        result = tool_list_sessions()
        print(result["text"])
        return

    # Interactive chat mode
    print("+" + "=" * 50 + "+")
    print("|   WebConX Automation — Chatbot Agent            |")
    print("|   Type your request or 'quit' to exit.          |")
    print("+" + "=" * 50 + "+")
    print()

    history = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break
        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "bye"}:
            print("Goodbye!")
            break

        history.append({"role": "user", "content": user_input})
        result = handle_chat_message(user_input, conversation_history=history)
        reply = result["reply"]
        print(f"\nAssistant: {reply}\n")
        history.append({"role": "assistant", "content": reply})

        if result.get("download"):
            dl = result["download"]
            if dl.get("url"):
                print(f"  Download: http://localhost:8000{dl['url']}")
            if dl.get("content") and dl.get("filename"):
                print(f"  File ready: {dl['filename']}")


if __name__ == "__main__":
    main()
