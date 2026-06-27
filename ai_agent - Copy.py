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
        return {"text": f"No test scripts found matching \"{query}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** with a different keyword."}

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
        return {"text": "No project folders found.\n\n"
                        "**Suggestion:** Create one with: \"create project <name>\""}

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
        return {"text": "Error: Folder name is required."}
    if folder_name.lower() in {"baseline", "unfiled", "recordings", ""}:
        return {"text": f"Error: \"{folder_name}\" is a reserved name."}

    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM parent_folders WHERE parent_folder = %s", [folder_name])
            if cur.fetchone():
                return {"text": f"Project folder \"{folder_name}\" already exists."}

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


def _name_ilike_clause(name: str, column: str = "m.record_name"):
    """Build a WHERE clause that matches all words in *name* independently.

    Common filler words (test, case, script, …) are stripped so that
    "populate city test case" still matches "Populate Current City".

    ``"Populate City"`` → ``column ILIKE '%Populate%' AND column ILIKE '%City%'``
    Returns (sql_fragment, params_list).
    """
    _STOP_WORDS = {"test", "case", "script", "scripts", "cases", "the",
                   "a", "an", "for", "of", "to", "from", "and", "or",
                   "steps", "step", "download", "delete", "show", "search",
                   "find", "get", "my", "please", "me", "it", "its"}
    words = [w for w in name.split() if w.lower() not in _STOP_WORDS]
    if not words:
        # All words were stop-words; fall back to original string
        return f"{column} ILIKE %s", [f"%{name}%"]
    clauses = [f"{column} ILIKE %s" for _ in words]
    params = [f"%{w}%" for w in words]
    return " AND ".join(clauses), params


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
        r'^(?:search|look(?:\s+for)?|find(?:\s+for)?)\s+(?P<query>.+?)\s*$',
        r'^(?:is\s+there(?:\s+any)?|any\s+file|look\s+file)\s+(?P<query>.+?)\s*$',
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


def _clean_show_steps_query(raw_query: str) -> str:
    """Normalize a freeform show-steps phrase into a session-name query."""
    query = (raw_query or "").strip()
    if not query:
        return ""

    query = re.sub(r'^(?:for|of|in)\s+', '', query, flags=re.IGNORECASE)
    query = re.sub(r'\s+steps?$', '', query, flags=re.IGNORECASE)
    query = re.sub(r'^(?:the\s+)?(?:test\s+case|test\s+script|script|session|file)\s+', '', query, flags=re.IGNORECASE)
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


def _resolve_record_id(value: str) -> Optional[str]:
    """Resolve a record_id from a UUID string or test script name."""
    try:
        _uuid.UUID(value)
        return value
    except ValueError:
        return _find_record_id_by_name(value)


def tool_download_session(record_id: str, fmt: str = "", folder: str = "") -> dict:
    """Return a download link for test script steps (csv/pdf/doc)."""

    if not fmt:
        return {"text": "What file format would you like?\n\n"
                        "- **csv** — spreadsheet\n"
                        "- **pdf** — PDF document\n"
                        "- **doc** — Word document"}

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
            return {"text": "\n".join(lines), "download": None}

    # Resolve with folder if provided
    if folder and not is_uuid:
        rid = _find_record_id_by_name(record_id, folder=folder)
    else:
        rid = _resolve_record_id(record_id)

    if not rid:
        return {"text": f"No test script found for \"{record_id}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

    fmt = fmt.lower().strip()
    if fmt not in ("csv", "pdf", "doc"):
        return {"text": f"Unsupported format \"{fmt}\". Use csv, pdf, or doc."}

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
                           step_no: int = 0, field_name: str = "") -> dict:
    """Update the data value for a specific step (by step_no or field_name)."""
    rid = _resolve_record_id(record_id)
    if not rid:
        return {"text": f"No test script found for \"{record_id}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

    if not step_no and not field_name:
        return {"text": "Please provide either `step_no` or `field_name` to identify the step."}

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
                    return {"text": f"No data field matching \"{field_name}\" in test script {rid[:8]}….\n\n"
                                    f"**Suggestions:**\n"
                                    f"- Show available steps: \"show steps for {record_id}\"\n"
                                    f"- Try a different field name"}
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
        return {"text": f"No data row found for record {rid[:8]}… step {step_no}.\n\n"
                        f"**Suggestion:** Show available steps: \"show steps for {record_id}\""}
    return {
        "text": (f"Updated **step {step_no}** field **\"{fname}\"**:\n\n"
                 f"| | Value |\n|---|---|\n"
                 f"| Before | `{old_value}` |\n"
                 f"| After  | `{new_value}` |")
    }


def tool_bulk_update_data(record_id: str, updates: list) -> dict:
    """Update multiple steps' data values in one call.

    Args:
        record_id: UUID or test script name.
        updates: list of dicts, each with {step_no, new_value} or {field_name, new_value}.
    """
    rid = _resolve_record_id(record_id)
    if not rid:
        return {"text": f"No test script found for \"{record_id}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

    if not updates or not isinstance(updates, list):
        return {"text": "Please provide a list of updates, e.g. "
                         '`[{"step_no": 1, "new_value": "Alice"}, ...]`'}

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


def tool_update_step(record_id: str, step_no: int, **fields) -> dict:
    """Update step-level columns (action, page_url, element_tag, strategy, locator, field_name)."""
    rid = _resolve_record_id(record_id)
    if not rid:
        return {"text": f"No test script found for \"{record_id}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

    allowed = {"action", "page_url", "element_tag", "strategy", "locator",
               "field_name", "field_value", "validation"}
    to_set = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not to_set:
        return {"text": "No valid fields to update. Allowed: " + ", ".join(sorted(allowed))}

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
                return {"text": f"Step {step_no} not found in test script {rid[:8]}….\n\n"
                                f"**Suggestion:** Show available steps: \"show steps for {record_id}\""}

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
        return {"text": "Please provide the **filename** (test case name) for the new test case."}
    if not folder_name:
        return {"text": "Please specify **which project** this test case belongs to "
                        "(e.g. `Project001/Sub001/End001`).\n\n"
                        "You can say **list projects** to see available projects."}

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
            return {"text": f"No steps found for: {', '.join(not_found)}.\n\n"
                            f"Try **list scripts** to see available test cases."}

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
                    if strategy == "robot":
                        locators_raw["robot"] = locator_val
                        # Parse robot locator like "id:userName" for other strategies
                        if ":" in locator_val:
                            prefix, loc_value = locator_val.split(":", 1)
                            locators_raw[prefix] = f"#{loc_value}" if prefix == "id" else loc_value
                    else:
                        locators_raw[strategy] = locator_val
                        # Auto-generate robot locator
                        locators_raw["robot"] = f"{strategy}:{locator_val}"
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
                        " folder_name) "
                        "VALUES (%s, %s, %s, %s, TRUE, 1, %s) RETURNING id",
                        [record_id, i, strategy, locator_val, folder_name or None],
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
        return {"text": f"No steps found matching \"{query}\" (searched: {search_in}). "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** with a different keyword."}

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
        return {"text": f"No test script found for \"{record_id}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

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
        return {"text": f"No steps found for test script {rid[:8]}….\n\n"
                        f"**Suggestion:** The test script exists but has no steps. "
                        f"You can create steps with: \"create test case <name>\""}

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
        return {"text": "Which test case do you want to delete? "
                        "Please provide the **filename** (test case name)."}

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
        return {"text": f"No test script found for \"{record_name}\". "
                        f"Try **list scripts** to see available test scripts "
                        f"or **search <keyword>** to find one."}

    # Multiple matches → ask which project
    if len(matches) > 1 and not folder and not rid:
        lines = [f"Multiple test scripts found matching **\"{record_name}\"**. "
                 f"Which one do you want to delete?\n"]
        for i, m in enumerate(matches, 1):
            proj = m["folder_name"] or "(no project)"
            lines.append(f"{i}. **{m['record_name']}** — Project: `{proj}` ({m['step_count']} steps)")
        lines.append("\nPlease specify the project name, e.g. "
                     f"\"delete {record_name} from Project001\"")
        return {"text": "\n".join(lines)}

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
- Treat search-style phrases like "search for", "look", "look for", "find", "find for", "is there", "any file", "look file", and patterns like "file name=..." or "record_name=..." as test script search requests when the user is asking about files/test cases/projects.
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

TOOL_DISPATCH = {
    "list_sessions":             tool_list_sessions,
    "search_sessions":           tool_search_sessions,
    "list_projects":             tool_list_projects,
    "create_project":            tool_create_project,
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
    # 1) Look for ```json ... ``` fenced block
    m = re.search(r'```json\s*\n?(.*?)\n?\s*```', llm_response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 2) Look for a JSON object with "tool" key (supports nested braces for args)
    m = re.search(r'\{[^}]*"tool"\s*:\s*"[^"]+?".*\}', llm_response, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    # 3) Try to parse entire response as JSON
    stripped = llm_response.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and "tool" in obj:
                return obj
        except json.JSONDecodeError:
            pass
    return None


def handle_chat_message(user_message: str, username: str = "admin",
                        conversation_history: list | None = None) -> dict:
    """
    Process a single chat message. Returns:
      {"reply": str, "download": dict|None}
    """
    if conversation_history is None:
        conversation_history = []

    delete_request = _parse_delete_request(user_message)
    if delete_request:
        try:
            result = tool_delete_test_case(
                record_name=delete_request["record_name"],
                folder=delete_request.get("folder", ""),
                confirm=False,
            )
        except Exception as exc:
            return {"reply": f"Error executing delete_test_case: {exc}", "download": None}
        return {
            "reply": result.get("text", "Done."),
            "download": result.get("download"),
        }

    if _is_delete_confirmation(user_message):
        pending_delete = _pending_delete_from_history(conversation_history)
        if pending_delete:
            try:
                result = tool_delete_test_case(
                    record_name=pending_delete["record_name"],
                    folder=pending_delete.get("folder", ""),
                    confirm=True,
                )
            except Exception as exc:
                return {"reply": f"Error executing delete_test_case: {exc}", "download": None}
            return {
                "reply": result.get("text", "Done."),
                "download": result.get("download"),
            }
        return {
            "reply": "There is no pending delete to confirm.",
            "download": None,
        }

    show_steps_query = _parse_show_steps_request(user_message)
    if show_steps_query:
        try:
            result = tool_show_steps(show_steps_query)
        except Exception as exc:
            return {"reply": f"Error executing show_steps: {exc}", "download": None}
        return {
            "reply": result.get("text", "Done."),
            "download": result.get("download"),
        }

    download_request = _parse_download_request(user_message)
    if download_request:
        try:
            result = tool_download_session(**download_request)
        except Exception as exc:
            return {"reply": f"Error executing download_session: {exc}", "download": None}
        return {
            "reply": result.get("text", "Done."),
            "download": result.get("download"),
        }

    search_query = _parse_search_sessions_request(user_message)
    if search_query:
        try:
            result = tool_search_sessions(search_query)
        except Exception as exc:
            return {"reply": f"Error executing search_sessions: {exc}", "download": None}
        return {
            "reply": result.get("text", "Done."),
            "download": result.get("download"),
        }

    # Build prompt with recent conversation context
    history_text = ""
    for msg in conversation_history[-6:]:
        role = "User" if msg["role"] == "user" else "Assistant"
        history_text += f"{role}: {msg['content']}\n\n"

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

        if tool_name not in TOOL_DISPATCH:
            return {"reply": f"Unknown tool \"{tool_name}\". Try rephrasing your request.",
                    "download": None}

        if tool_name == "create_project" and "author" not in tool_args:
            tool_args["author"] = username

        if tool_name == "create_test_case":
            derived_keywords = _extract_create_keywords(user_message)
            if derived_keywords:
                tool_args["copy_from"] = ", ".join(derived_keywords)
            if "author" not in tool_args:
                tool_args["author"] = username

        try:
            result = TOOL_DISPATCH[tool_name](**tool_args)
        except Exception as exc:
            return {"reply": f"Error executing {tool_name}: {exc}", "download": None}

        return {
            "reply": result.get("text", "Done."),
            "download": result.get("download"),
        }

    # No tool call — return the LLM's direct text
    return {"reply": llm_response, "download": None}


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
