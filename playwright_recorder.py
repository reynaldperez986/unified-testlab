"""
Playwright-based web action recorder.

Launches a Chromium browser using Playwright, injects recording instrumentation,
and writes captured events to the same PostgreSQL schema as main.py (Selenium recorder).

Usage (standalone):
    python playwright_recorder.py --url https://example.com

Usage (from Django start_recording view):
    Launched as subprocess with same CLI args as main.py.

The generated events are stored in the `steps` table (or `recordings` for baseline)
using identical column layout, so all existing views/exports/replay work unchanged.
"""

import argparse
from datetime import datetime, timezone
import json
import os
import re
import signal
import sys
import tempfile
import time
import uuid
from typing import Any

import psycopg2
import psycopg2.pool
from psycopg2.extras import Json, register_uuid

register_uuid()


def _normalize_field_name(value: Any) -> str:
    return re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).strip()


def _ensure_unique_field_name(cur: Any, record_id: uuid.UUID, field_name: str) -> str:
    if not field_name:
        return ""
    cur.execute(
        """
        SELECT field_name
        FROM data
        WHERE field_name IS NOT NULL
          AND field_name LIKE %s;
        """,
        (f"{field_name}%",),
    )
    used_names = {str(row[0]) for row in cur.fetchall() if row and row[0]}
    if field_name not in used_names:
        return field_name

    suffix = 2
    while True:
        candidate = f"{field_name}-{suffix}"
        if candidate not in used_names:
            return candidate
        suffix += 1

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record web actions using Playwright and save to PostgreSQL."
    )
    parser.add_argument("--url", default=None, help="Initial URL to open.")
    parser.add_argument("--headless", action="store_true", help="Run in headless mode.")
    parser.add_argument("--poll-interval", type=float, default=0.4,
                        help="Event polling interval in seconds.")
    parser.add_argument("--db-host",     default=os.getenv("PGHOST",     "localhost"))
    parser.add_argument("--db-port",     type=int,
                                         default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--db-name",     default=os.getenv("PGDATABASE", "automation_db"))
    parser.add_argument("--db-user",     default=os.getenv("PGUSER",     "postgres"))
    parser.add_argument("--db-password", default=os.getenv("PGPASSWORD", "password"))
    parser.add_argument("--record-name", default="")
    parser.add_argument("--recorder", default="")
    parser.add_argument("--folder-name", default="")
    parser.add_argument("--is-baseline", action="store_true")
    parser.add_argument("--record-id", default="")
    parser.add_argument("--start-step", type=int, default=0)
    parser.add_argument("--no-navigate", action="store_true")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

class PgWriter:
    """Writes recorded events into PostgreSQL (steps or recordings table)."""

    def __init__(self, host, port, database, user, password,
                 recorder="", folder_name="", is_baseline=False):
        self._pool = psycopg2.pool.SimpleConnectionPool(
            1, 3,
            host=host, port=port, database=database,
            user=user, password=password,
        )
        self.recorder = recorder
        self.folder_name = folder_name
        self.is_baseline = is_baseline
        self._table = "recordings" if is_baseline else "steps"
        self._last_event_timestamp_ms: int | None = None
        self._next_recorded_delay_s: float | None = None

    def _conn(self):
        return self._pool.getconn()

    def _put(self, conn, discard=False):
        self._pool.putconn(conn, close=discard)

    def ensure_session_meta(self, record_id: uuid.UUID, record_name: str):
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO session_meta (record_id, record_name, recorder, folder_name, engine, is_baseline) "
                    "VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (record_id) DO UPDATE SET is_baseline = EXCLUDED.is_baseline;",
                    (record_id, record_name, self.recorder, self.folder_name, "playwright", self.is_baseline),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    @staticmethod
    def _event_timestamp_ms(event: dict[str, Any]) -> int | None:
        raw = event.get("timestamp")
        if raw in (None, ""):
            return None
        if isinstance(raw, (int, float)):
            return int(raw)
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            try:
                return int(float(text))
            except Exception:
                pass
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=timezone.utc)
                return int(parsed.timestamp() * 1000)
            except Exception:
                return None
        return None

    def _annotate_recorded_delay(self, event: dict[str, Any]) -> dict[str, Any]:
        annotated = dict(event)
        if self._next_recorded_delay_s is not None:
            annotated["_recorded_step_delay_s"] = round(max(0.0, float(self._next_recorded_delay_s)), 3)
            self._next_recorded_delay_s = None
            current_ms = self._event_timestamp_ms(annotated)
            if current_ms is not None:
                self._last_event_timestamp_ms = current_ms
            return annotated
        current_ms = self._event_timestamp_ms(annotated)
        delay_s = 0.0
        if current_ms is not None and self._last_event_timestamp_ms is not None:
            delay_s = max(0.0, (current_ms - self._last_event_timestamp_ms) / 1000.0)
        annotated["_recorded_step_delay_s"] = round(delay_s, 3)
        if current_ms is not None:
            self._last_event_timestamp_ms = current_ms
        return annotated

    def set_next_recorded_delay(self, delay_s: float | None) -> None:
        if delay_s is None:
            self._next_recorded_delay_s = None
            return
        self._next_recorded_delay_s = max(0.0, float(delay_s))

    def insert_event(self, record_id: uuid.UUID, step_no: int, event: dict):
        event = self._annotate_recorded_delay(event)
        action = event.get("action", "")
        page_url = event.get("url", "")
        element_tag = event.get("tag", "")
        page_title = event.get("pageTitle") or event.get("title") or ""
        value = event.get("value", "")
        selenium_info = event.get("selenium_info") if isinstance(event.get("selenium_info"), dict) else {}
        playwright_info = event.get("playwright_info") if isinstance(event.get("playwright_info"), dict) else {}
        field_name = (
            event.get("accessibleName")
            or selenium_info.get("accessibleName")
            or playwright_info.get("accessibleName")
            or event.get("textContent")
            or selenium_info.get("textContent")
            or playwright_info.get("textContent")
            or event.get("value")
            or selenium_info.get("value")
            or playwright_info.get("value")
            or event.get("text")
            or selenium_info.get("text")
            or playwright_info.get("text")
            or event.get("innerText")
            or selenium_info.get("innerText")
            or playwright_info.get("innerText")
            or ""
        )
        field_name = _normalize_field_name(field_name) or _normalize_field_name(value)
        key = event.get("key", "")
        locators = event.get("locators") or {}
        pos_x = event.get("pos_x") or 0
        pos_y = event.get("pos_y") or 0
        playwright_info = playwright_info or {}

        conn = self._conn()
        try:
            with conn.cursor() as cur:
                # Insert data entry
                data_id = None
                field_name = _ensure_unique_field_name(cur, record_id, field_name)
                if value or field_name:
                    cur.execute(
                        "INSERT INTO data (record_id, step_no, field_name, value, folder_name, engine) "
                        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id;",
                        (record_id, step_no, field_name, value, self.folder_name, "playwright"),
                    )
                    data_id = cur.fetchone()[0]

                # Insert primary locator
                locator_id = None
                primary_strategy, primary_locator = self._pick_primary_locator(locators, element_tag)
                if primary_strategy and primary_locator:
                    cur.execute(
                        "INSERT INTO locators (record_id, step_no, strategy, locator, is_primary, locator_rank, pos_x, pos_y, folder_name, engine) "
                        "VALUES (%s, %s, %s, %s, TRUE, 1, %s, %s, %s, %s) RETURNING id;",
                        (record_id, step_no, primary_strategy, primary_locator, pos_x, pos_y, self.folder_name, "playwright"),
                    )
                    locator_id = cur.fetchone()[0]

                # Insert all other locators (enriched with playwright_info attributes)
                rank = 2
                for strat, loc_val in self._all_locators(locators, playwright_info):
                    if strat == primary_strategy and loc_val == primary_locator:
                        continue
                    cur.execute(
                        "INSERT INTO locators (record_id, step_no, strategy, locator, is_primary, locator_rank, pos_x, pos_y, folder_name, engine) "
                        "VALUES (%s, %s, %s, %s, FALSE, %s, %s, %s, %s, %s);",
                        (record_id, step_no, strat, loc_val, rank, pos_x, pos_y, self.folder_name, "playwright"),
                    )
                    rank += 1

                # Build step description
                steps_description = self._build_step_description(event)

                # Insert step (same columns as Selenium main.py)
                cur.execute(
                    f"INSERT INTO {self._table} "
                    "(record_id, step_no, action, page_url, element_tag, "
                    " locator_id, data_id, raw_event, recorder, folder_name, "
                    " locators_raw, field_name, field_value, pos_x, pos_y, "
                    " strategy, locator, is_primary, locator_rank, file_order, is_baseline, author, "
                    " steps_description, page_title, engine, raw_event_playwright) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);",
                    (
                        record_id, step_no, action, page_url, element_tag,
                        locator_id, data_id, Json(event), self.recorder, self.folder_name,
                        Json(locators), field_name or None, value or None, pos_x, pos_y,
                        primary_strategy, primary_locator, True if primary_strategy else None,
                        1 if primary_strategy else None, 1, self.is_baseline, self.recorder,
                        steps_description, page_title or None, "playwright",
                        Json(playwright_info) if playwright_info else None,
                    ),
                )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._put(conn)

    def _pick_primary_locator(self, locators: dict, tag: str) -> tuple[str, str]:
        priority = ["dataTestId", "id", "name", "ariaLabel", "css", "xpath",
                    "linkText", "partialLinkText", "className", "tagName"]
        for strategy in priority:
            val = locators.get(strategy, "")
            if isinstance(val, str) and val.strip():
                return strategy, val.strip()
        return "", ""

    def _all_locators(self, locators: dict, playwright_info: dict = None):
        # Yield in the same priority order as _pick_primary_locator so the
        # locator_rank values stored in DB match replay priority.
        priority_order = (
            "dataTestId", "id", "name", "ariaLabel", "label", "css", "xpath",
            "linkText", "partialLinkText", "className", "placeholder",
            "title", "alt", "href", "value", "role", "type", "tagName",
        )
        seen: set[str] = set()
        for strategy in priority_order:
            val = locators.get(strategy, "")
            if isinstance(val, str) and val.strip():
                seen.add(strategy)
                yield strategy, val.strip()

        # Enrich from playwright_info attributes (fills gaps not captured by JS recorder)
        if playwright_info:
            attrs = playwright_info.get("attributes") or {}
            for attr_name, strategy in _PLAYWRIGHT_ATTR_STRATEGY_MAP.items():
                if strategy in seen:
                    continue
                val = attrs.get(attr_name, "")
                if isinstance(val, str) and val.strip():
                    seen.add(strategy)
                    yield strategy, val.strip()
            # tagName from playwright_info (normalised to lowercase)
            if "tagName" not in seen and playwright_info.get("tagName"):
                yield "tagName", playwright_info["tagName"].lower()

    @staticmethod
    def _build_step_description(event: dict) -> str:
        """Build a human-readable description of the recorded browser event."""
        action = event.get("action", "unknown")
        tag = event.get("tag") or ""
        text = event.get("text") or ""
        value = event.get("value") or ""
        name = event.get("name") or event.get("id") or ""

        if action == "click":
            if text:
                return f"Left-mouse-click on '{text}'"
            if name:
                return f"Left-mouse-click on {tag} '{name}'" if tag else f"Left-mouse-click on '{name}'"
            return f"Left-mouse-click is pressed" + (f" on <{tag}>" if tag else "")
        if action == "dblclick":
            if text:
                return f"Double-click on '{text}'"
            return f"Double-click is pressed" + (f" on <{tag}>" if tag else "")
        if action in ("input", "change"):
            if value:
                return f"User input recorded: '{value}'"
            return f"User input on {name or tag or 'field'}"
        if action == "keydown":
            key = event.get("key") or ""
            return f"Key pressed: '{key}'" if key else "Key pressed"
        if action == "submit":
            return f"Form submitted" + (f" on <{tag}>" if tag else "")
        if action == "navigate":
            url = event.get("url") or ""
            return f"Navigate to {url}" if url else "Page navigation"
        if action == "scroll":
            return "Page scrolled"
        return f"{action.capitalize()} event" + (f" on <{tag}>" if tag else "")

    def close(self):
        try:
            self._pool.closeall()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Recording JavaScript (injected into every page via Playwright)
# ---------------------------------------------------------------------------

RECORDER_JS = r"""
(function() {
    if (window.__pwRecorderInstalled) return;
    window.__pwRecorderInstalled = true;
    window.__pwRecorderEvents = window.__pwRecorderEvents || [];

    try {
        var _saved = sessionStorage.getItem('__pwRecorderPending');
        if (_saved) {
            var _parsed = JSON.parse(_saved);
            sessionStorage.removeItem('__pwRecorderPending');
            if (Array.isArray(_parsed)) {
                window.__pwRecorderEvents = _parsed.concat(window.__pwRecorderEvents);
            }
        }
    } catch(e) {}

    window.addEventListener('beforeunload', function() {
        var _pending = window.__pwRecorderEvents || [];
        if (_pending.length > 0) {
            try {
                var _existing = sessionStorage.getItem('__pwRecorderPending');
                var _existingArr = _existing ? JSON.parse(_existing) : [];
                sessionStorage.setItem('__pwRecorderPending', JSON.stringify(_existingArr.concat(_pending)));
                window.__pwRecorderEvents = [];
            } catch(e) {}
        }
    });

    function getLocators(el) {
        if (!el || !el.tagName) return {};
        var loc = {};
        if (el.id) loc.id = el.id;
        if (el.name) loc.name = el.name;
        if (el.getAttribute('data-testid')) loc.dataTestId = el.getAttribute('data-testid');
        if (el.getAttribute('data-test-id')) loc.dataTestId = loc.dataTestId || el.getAttribute('data-test-id');
        if (el.getAttribute('aria-label')) loc.ariaLabel = el.getAttribute('aria-label');
        if (el.placeholder) loc.placeholder = el.placeholder;
        if (el.getAttribute('role')) loc.role = el.getAttribute('role');
        if (el.title) loc.title = el.title;
        if (el.alt) loc.alt = el.alt;
        if (el.type) loc.type = el.type;
        if (el.className && typeof el.className === 'string') loc.className = el.className.trim();
        if (el.tagName.toLowerCase() === 'a') {
            var lt = (el.textContent || '').trim();
            if (lt) { loc.linkText = lt.substring(0, 80); loc.partialLinkText = lt.substring(0, 40); }
            if (el.href) loc.href = el.href;
        }
        var text = '';
        if (el.tagName.match(/^(BUTTON|A|SPAN|LABEL|H[1-6])$/i)) {
            text = (el.textContent || '').trim().substring(0, 80);
        }
        if (text) loc.text = text;
        loc.tagName = el.tagName.toLowerCase();
        // CSS selector
        try {
            var css = el.tagName.toLowerCase();
            if (el.id) css = '#' + el.id;
            else if (el.className && typeof el.className === 'string') {
                css = el.tagName.toLowerCase() + '.' + el.className.trim().split(/\s+/).join('.');
            }
            loc.css = css;
        } catch(e) {}
        // XPath
        try {
            var xpath = '';
            var node = el;
            while (node && node.nodeType === 1) {
                var tag = node.tagName.toLowerCase();
                var idx = 0, sib = node;
                while (sib) { if (sib.nodeType === 1 && sib.tagName === node.tagName) idx++; sib = sib.previousSibling; }
                xpath = '/' + tag + (idx > 1 ? '[' + idx + ']' : '') + xpath;
                node = node.parentNode;
            }
            if (xpath) loc.xpath = xpath;
        } catch(e) {}
        return loc;
    }

    function getPlaywrightInfo(el) {
        if (!el) return null;
        try {
            var rect   = el.getBoundingClientRect();
            var styles = window.getComputedStyle(el);
            var attrs  = {};
            for (var i = 0; i < el.attributes.length; i++) {
                attrs[el.attributes[i].name] = el.attributes[i].value;
            }

            function normalizeLabelText(text) {
                text = (text || '').replace(/\s+/g, ' ').trim();
                if (!text) return '';
                var words = text.split(' ');
                if (words.length % 2 === 0) {
                    var half = words.length / 2;
                    var left = words.slice(0, half).join(' ').trim();
                    var right = words.slice(half).join(' ').trim();
                    if (left && left === right) {
                        return left.substring(0, 200);
                    }
                }
                return text.substring(0, 200);
            }

            function labelTextFor(target) {
                if (!target) return '';
                var text = '';
                try {
                    if (target.labels && target.labels.length) {
                        text = Array.from(target.labels)
                            .map(function(lbl) { return (lbl.innerText || lbl.textContent || '').trim(); })
                            .filter(Boolean)
                            .join(' ');
                    }
                    if (!text && attrs['aria-labelledby']) {
                        text = attrs['aria-labelledby']
                            .split(/\s+/)
                            .map(function(id) {
                                var node = document.getElementById(id);
                                return node ? (node.innerText || node.textContent || '').trim() : '';
                            })
                            .filter(Boolean)
                            .join(' ');
                    }
                    if (!text && target.id) {
                        var explicitLabel = document.querySelector('label[for="' + CSS.escape(target.id) + '"]');
                        if (explicitLabel) {
                            text = (explicitLabel.innerText || explicitLabel.textContent || '').trim();
                        }
                    }
                    if (!text) {
                        var parentLabel = target.closest ? target.closest('label') : null;
                        if (parentLabel) {
                            text = (parentLabel.innerText || parentLabel.textContent || '').trim();
                        }
                    }
                } catch(e) {}
                return normalizeLabelText(text);
            }

            function accessibleNameFor(target) {
                if (!target) return '';
                var text = '';
                try {
                    text = attrs['aria-label'] || '';
                    if (!text) text = labelTextFor(target);
                    if (!text && target.title) text = target.title;
                    if (!text && target.placeholder) text = target.placeholder;
                    if (!text && /^(button|a)$/i.test(target.tagName)) {
                        text = (target.innerText || target.textContent || '').trim();
                    }
                    if (!text && /^(submit|button|reset)$/i.test(target.type || '')) {
                        text = target.value || '';
                    }
                } catch(e) {}
                return (text || '').trim().substring(0, 200);
            }

            return {
                tagName:     el.tagName,
                id:          el.id || null,
                className:   (typeof el.className === 'string') ? el.className.trim() : null,
                textContent: (el.textContent || '').trim().substring(0, 200),
                innerText:   (el.innerText   || '').trim().substring(0, 200),
                value:       el.value !== undefined ? el.value : null,
                labelText:   labelTextFor(el),
                accessibleName: accessibleNameFor(el),
                attributes:  attrs,
                state: {
                    visible:  !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
                    disabled: el.disabled  || false,
                    readonly: el.readOnly  || false,
                    checked:  el.checked   || false,
                    selected: el.selected  || false
                },
                position: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                styles: {
                    display:         styles.display,
                    visibility:      styles.visibility,
                    opacity:         styles.opacity,
                    color:           styles.color,
                    backgroundColor: styles.backgroundColor
                }
            };
        } catch(e) { return null; }
    }

    function resolveToggleControl(target) {
        if (!target || !target.closest) return target || null;
        if (target.matches && target.matches('input[type="checkbox"], input[type="radio"]')) {
            return target;
        }
        var control = null;
        var label = target.closest('label');
        if (label) {
            control = label.control || null;
            if (!control && label.htmlFor) {
                control = document.getElementById(label.htmlFor);
            }
            if (!control) {
                control = label.querySelector('input[type="checkbox"], input[type="radio"]');
            }
        }
        if (!control) {
            var container = target.closest('.checkbox, .radio, [role="checkbox"], [role="radio"]');
            if (container) {
                control = container.querySelector('input[type="checkbox"], input[type="radio"]');
            }
        }
        return control || target;
    }

    function buildEvent(action, el, extras) {
        var sourceTarget = el || null;
        el = resolveToggleControl(el);
        var rect = el ? el.getBoundingClientRect() : {left:0, top:0};
        var info = el ? getPlaywrightInfo(el) : null;
        var toggleText = '';
        if (el && /^(checkbox|radio)$/i.test(el.type || '')) {
            toggleText = ((info && (info.labelText || info.accessibleName)) || '').trim();
            if (!toggleText && sourceTarget) {
                toggleText = (sourceTarget.innerText || sourceTarget.textContent || '').trim();
            }
            toggleText = toggleText.substring(0, 80);
        }
        var locators = el ? getLocators(el) : {};
        if (toggleText && !locators.text) {
            locators.text = toggleText;
        }
        if (toggleText && !locators.label) {
            locators.label = toggleText;
        }
        var ev = {
            action: action,
            timestamp: Date.now(),
            url: location.href,
            title: document.title,
            pageTitle: document.title,
            tag: el ? el.tagName.toLowerCase() : '',
            pos_x: Math.round(rect.left + (rect.width || 0) / 2),
            pos_y: Math.round(rect.top + (rect.height || 0) / 2),
            locators: locators,
            id: el ? (el.id || '') : '',
            name: el ? (el.name || '') : '',
            inputType: el ? (el.type || '') : '',
            checked: el && ('checked' in el) ? !!el.checked : null,
            value: el ? (el.value || '') : '',
            text: toggleText || (el ? (el.textContent || '').trim().substring(0, 80) : ''),
            playwright_info: info
        };
        if (extras) { for (var k in extras) ev[k] = extras[k]; }
        return ev;
    }

    function push(ev) {
        window.__pwRecorderEvents.push(ev);
    }

    function isToggleLabelClick(target) {
        if (!target || !target.closest) return false;
        var label = target.closest('label');
        if (!label) return false;
        var control = label.control || null;
        if (!control && label.htmlFor) {
            control = document.getElementById(label.htmlFor);
        }
        if (!control) {
            control = label.querySelector('input[type="checkbox"], input[type="radio"]');
        }
        if (!control) return false;
        var type = (control.getAttribute('type') || '').toLowerCase();
        return type === 'checkbox' || type === 'radio';
    }

    // Click
    document.addEventListener('click', function(e) {
        if (window.__pwRecorderPaused) return;
        push(buildEvent('click', e.target));
    }, true);

    // Double click
    document.addEventListener('dblclick', function(e) {
        if (window.__pwRecorderPaused) return;
        push(buildEvent('dblclick', e.target));
    }, true);

    // Context menu (right-click)
    document.addEventListener('contextmenu', function(e) {
        if (window.__pwRecorderPaused) return;
        push(buildEvent('contextmenu', e.target));
    }, true);

    // Input
    document.addEventListener('input', function(e) {
        if (window.__pwRecorderPaused) return;
        push(buildEvent('input', e.target, {value: e.target.value || ''}));
    }, true);

    // Change (select, checkbox, radio)
    document.addEventListener('change', function(e) {
        if (window.__pwRecorderPaused) return;
        var extras = {value: e.target.value || ''};
        if (e.target.tagName === 'SELECT') {
            var opt = e.target.options[e.target.selectedIndex];
            if (opt) extras.text = opt.text || '';
        }
        push(buildEvent('change', e.target, extras));
    }, true);

    // Keydown (for special keys)
    document.addEventListener('keydown', function(e) {
        if (window.__pwRecorderPaused) return;
        var special = ['Enter','Tab','Escape','Backspace','Delete',
                       'ArrowUp','ArrowDown','ArrowLeft','ArrowRight',
                       'Home','End','PageUp','PageDown'];
        if (special.indexOf(e.key) >= 0 || e.ctrlKey || e.metaKey || e.altKey) {
            push(buildEvent('keydown', e.target, {key: e.key}));
        }
    }, true);

    // Scroll (debounced)
    var _scrollTimer = null;
    var _scrollStart = null;
    window.addEventListener('scroll', function() {
        if (window.__pwRecorderPaused) return;
        if (!_scrollStart) _scrollStart = {x: window.scrollX, y: window.scrollY};
        clearTimeout(_scrollTimer);
        _scrollTimer = setTimeout(function() {
            var dx = window.scrollX - _scrollStart.x;
            var dy = window.scrollY - _scrollStart.y;
            if (Math.abs(dx) > 5 || Math.abs(dy) > 5) {
                push({
                    action: 'scroll',
                    timestamp: Date.now(),
                    url: location.href,
                    title: document.title,
                    pageTitle: document.title,
                    tag: 'window',
                    pos_x: 0, pos_y: 0,
                    delta_x: dx, delta_y: dy,
                    locators: {}, id: '', name: '', inputType: '', value: '', text: ''
                });
            }
            _scrollStart = null;
        }, 300);
    }, true);

    console.log('[PW Recorder] Instrumentation active');
})();
"""


# ---------------------------------------------------------------------------
# Playwright locator enrichment (sync)
# ---------------------------------------------------------------------------

_JS_GET_LOCATOR_INFO = r"""
(el) => {
    const rect = el.getBoundingClientRect();
    const styles = window.getComputedStyle(el);
    const attrs = Array.from(el.attributes).reduce((acc, attr) => {
        acc[attr.name] = attr.value;
        return acc;
    }, {});
    const normalizeLabelText = (text) => {
        text = (text || '').replace(/\s+/g, ' ').trim();
        if (!text) return '';
        const words = text.split(' ');
        if (words.length % 2 === 0) {
            const half = words.length / 2;
            const left = words.slice(0, half).join(' ').trim();
            const right = words.slice(half).join(' ').trim();
            if (left && left === right) return left.substring(0, 200);
        }
        return text.substring(0, 200);
    };
    const labelText = (() => {
        try {
            if (el.labels && el.labels.length) {
                return normalizeLabelText(Array.from(el.labels)
                    .map(lbl => (lbl.innerText || lbl.textContent || '').trim())
                    .filter(Boolean)
                    .join(' ')
                );
            }
            if (attrs['aria-labelledby']) {
                return normalizeLabelText(attrs['aria-labelledby']
                    .split(/\s+/)
                    .map(id => {
                        const node = document.getElementById(id);
                        return node ? (node.innerText || node.textContent || '').trim() : '';
                    })
                    .filter(Boolean)
                    .join(' ')
                );
            }
            if (el.id) {
                const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                if (explicitLabel) {
                    return normalizeLabelText(explicitLabel.innerText || explicitLabel.textContent || '');
                }
            }
            const parentLabel = el.closest ? el.closest('label') : null;
            return parentLabel ? normalizeLabelText(parentLabel.innerText || parentLabel.textContent || '') : '';
        } catch (e) {
            return '';
        }
    })();
    const accessibleName = (() => {
        if (attrs['aria-label']) return attrs['aria-label'];
        if (labelText) return labelText;
        if (el.title) return el.title;
        if (el.placeholder) return el.placeholder;
        if (/^(BUTTON|A)$/.test(el.tagName)) return (el.innerText || el.textContent || '').trim().substring(0, 200);
        if (/^(submit|button|reset)$/i.test(el.type || '')) return (el.value || '').substring(0, 200);
        return '';
    })();
    return {
        tagName: el.tagName,
        id: el.id,
        className: el.className,
        textContent: el.textContent,
        innerText: el.innerText,
        innerHTML: el.innerHTML,
        value: el.value !== undefined ? el.value : null,
        labelText,
        accessibleName,
        attributes: attrs,
        state: {
            visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length),
            disabled: el.disabled || false,
            readonly: el.readOnly || false,
            checked: el.checked || false,
            selected: el.selected || false
        },
        position: {
            x: rect.x,
            y: rect.y,
            width: rect.width,
            height: rect.height
        },
        styles: {
            display: styles.display,
            visibility: styles.visibility,
            opacity: styles.opacity,
            color: styles.color,
            backgroundColor: styles.backgroundColor
        }
    };
}
"""

_PLAYWRIGHT_ATTR_STRATEGY_MAP = {
    "id":            "id",
    "name":          "name",
    "class":         "className",
    "placeholder":   "placeholder",
    "role":          "role",
    "type":          "type",
    "title":         "title",
    "alt":           "alt",
    "href":          "href",
    "aria-label":    "ariaLabel",
    "data-testid":   "dataTestId",
    "data-test-id":  "dataTestId",
}


def get_playwright_locator_info(page, event: dict) -> dict:
    """
    Given a sync Playwright page and a recorded event, locates the element
    and returns rich locator info via JS evaluation.
    Returns an empty dict for non-element events or on failure.
    """
    action = event.get("action", "")
    if action in ("navigate", "navigate_back", "navigate_forward", "scroll"):
        return {}
    locators = event.get("locators") or {}
    if not locators:
        return {}

    # Ordered list of selectors to try, most specific first
    selectors: list[str] = []
    if locators.get("id"):
        selectors.append(f"#{locators['id']}")
    if locators.get("dataTestId"):
        selectors.append(f"[data-testid='{locators['dataTestId']}']")
    if locators.get("ariaLabel"):
        selectors.append(f"[aria-label='{locators['ariaLabel']}']")
    if locators.get("name"):
        selectors.append(f"[name='{locators['name']}']")
    if locators.get("xpath"):
        selectors.append(f"xpath={locators['xpath']}")
    if locators.get("css") and locators["css"] not in selectors:
        selectors.append(locators["css"])

    for selector in selectors:
        try:
            loc = page.locator(selector).first
            if loc.count() > 0:
                return loc.evaluate(_JS_GET_LOCATOR_INFO)
        except Exception:
            continue
    return {}


# ---------------------------------------------------------------------------
# Deduplication (same logic as main.py)
# ---------------------------------------------------------------------------

def _element_key(ev: dict) -> str:
    return (
        ev.get("id")
        or ev.get("name")
        or (ev.get("locators") or {}).get("xpath")
        or ""
    )


def _is_toggle_input(ev: dict) -> bool:
    return (
        ev.get("tag") == "input"
        and str(ev.get("inputType") or ev.get("type") or "").lower() in ("checkbox", "radio")
    )


def _change_signature(ev: dict) -> tuple:
    locators = ev.get("locators") or {}
    return (
        ev.get("action"),
        ev.get("tag"),
        ev.get("id"),
        ev.get("name"),
        locators.get("xpath"),
        locators.get("css"),
        str(ev.get("inputType") or ev.get("type") or "").lower(),
        ev.get("value"),
        ev.get("text"),
        ev.get("checked"),
    )


def _commit_signature(ev: dict) -> tuple:
    locators = ev.get("locators") or {}
    return (
        ev.get("tag"),
        ev.get("id"),
        ev.get("name"),
        locators.get("xpath"),
        locators.get("css"),
        str(ev.get("inputType") or ev.get("type") or "").lower(),
        ev.get("value"),
        ev.get("text"),
        ev.get("checked"),
    )


_RECENT_CHANGE_BATCH_WINDOW = 4


def _remember_recent_change(state: dict, key: str, sig: tuple, batch_no: int) -> None:
    recent = state.setdefault("recent_change_sig", {})
    recent[key] = (sig, batch_no)
    cutoff = batch_no - _RECENT_CHANGE_BATCH_WINDOW
    stale_keys = [k for k, (_sig, seen_batch) in recent.items() if seen_batch < cutoff]
    for stale_key in stale_keys:
        recent.pop(stale_key, None)


def _remember_recent_commit(state: dict, key: str, sig: tuple, batch_no: int) -> None:
    recent = state.setdefault("recent_commit_sig", {})
    recent[key] = (sig, batch_no)
    cutoff = batch_no - _RECENT_CHANGE_BATCH_WINDOW
    stale_keys = [k for k, (_sig, seen_batch) in recent.items() if seen_batch < cutoff]
    for stale_key in stale_keys:
        recent.pop(stale_key, None)


def _is_recent_duplicate_change(state: dict, key: str, sig: tuple, batch_no: int) -> bool:
    recent = state.setdefault("recent_change_sig", {})
    remembered = recent.get(key)
    if not remembered:
        return False
    remembered_sig, remembered_batch = remembered
    return remembered_sig == sig and (batch_no - remembered_batch) <= _RECENT_CHANGE_BATCH_WINDOW


def _is_recent_duplicate_commit(state: dict, key: str, sig: tuple, batch_no: int) -> bool:
    recent = state.setdefault("recent_commit_sig", {})
    remembered = recent.get(key)
    if not remembered:
        return False
    remembered_sig, remembered_batch = remembered
    return remembered_sig == sig and (batch_no - remembered_batch) <= _RECENT_CHANGE_BATCH_WINDOW


def _filter_events(raw_events: list[dict], state: dict) -> list[dict]:
    """Deduplicate events (combine rapid inputs, skip noise)."""
    last_val = state.setdefault("last_val", {})
    pending = state.setdefault("pending", {})
    last_change_sig = state.setdefault("last_change_sig", {})
    batch_no = int(state.get("batch_no", 0)) + 1
    state["batch_no"] = batch_no
    result: list[dict] = []

    def _flush(only_key=None):
        keys = [only_key] if only_key else list(pending.keys())
        for k in keys:
            ev = pending.pop(k, None)
            if ev:
                ev.pop("_pending_batch_no", None)
                if ev.get("action") in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev)):
                    ev["action"] = "change"
                sig = _change_signature(ev)
                commit_sig = _commit_signature(ev)
                if _is_recent_duplicate_change(state, k, sig, batch_no):
                    continue
                last_val[k] = ev.get("value")
                last_change_sig[k] = sig
                _remember_recent_change(state, k, sig, batch_no)
                _remember_recent_commit(state, k, commit_sig, batch_no)
                result.append(ev)

    i = 0
    while i < len(raw_events):
        ev = raw_events[i]
        action = ev.get("action", "")
        key = _element_key(ev)

        if action == "input":
            ev["_pending_batch_no"] = batch_no
            pending[key] = ev
            i += 1
        elif action == "change":
            pending.pop(key, None)
            prev = last_val.get(key)
            sig = _change_signature(ev)
            commit_sig = _commit_signature(ev)
            if not _is_recent_duplicate_change(state, key, sig, batch_no) and not _is_recent_duplicate_commit(state, key, commit_sig, batch_no) and (prev is None or ev.get("value") != prev or last_change_sig.get(key) != sig):
                last_val[key] = ev.get("value")
                last_change_sig[key] = sig
                _remember_recent_change(state, key, sig, batch_no)
                _remember_recent_commit(state, key, commit_sig, batch_no)
                result.append(ev)
            i += 1
        elif action == "keydown":
            _flush(key)
            result.append(ev)
            i += 1
        elif action in ("click", "dblclick"):
            if action in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev)):
                ev["_pending_batch_no"] = batch_no
                pending[key] = ev
                i += 1
                continue
            _flush()
            result.append(ev)
            i += 1
        elif action in ("navigate", "navigate_back", "navigate_forward"):
            _flush()
            last_val.clear()
            last_change_sig.clear()
            pending.clear()
            result.append(ev)
            i += 1
        else:
            result.append(ev)
            i += 1

    # Flush deferred select clicks only after they survive at least one full
    # poll cycle.  This gives the real `change` event a chance to arrive in the
    # next batch instead of producing a synthetic duplicate step immediately.
    # Emit as `change` so replay uses value-selection logic (not raw click).
    select_keys = [
        k for k, ev in pending.items()
        if ev.get("action") in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev))
        and int(ev.get("_pending_batch_no", batch_no)) < batch_no
    ]
    for k in select_keys:
        ev = pending.pop(k)
        ev.pop("_pending_batch_no", None)
        ev["action"] = "change"
        sig = _change_signature(ev)
        commit_sig = _commit_signature(ev)
        if _is_recent_duplicate_change(state, k, sig, batch_no):
            continue
        last_val[k] = ev.get("value")
        last_change_sig[k] = sig
        _remember_recent_change(state, k, sig, batch_no)
        _remember_recent_commit(state, k, commit_sig, batch_no)
        result.append(ev)

    return result


def _flush_pending(state: dict) -> list[dict]:
    pending = state.get("pending", {})
    last_val = state.get("last_val", {})
    last_change_sig = state.get("last_change_sig", {})
    result = []
    batch_no = int(state.get("batch_no", 0)) + 1
    state["batch_no"] = batch_no
    for k, ev in list(pending.items()):
        ev.pop("_pending_batch_no", None)
        if ev.get("action") in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev)):
            ev["action"] = "change"
        sig = _change_signature(ev)
        commit_sig = _commit_signature(ev)
        if _is_recent_duplicate_change(state, k, sig, batch_no):
            continue
        last_val[k] = ev.get("value")
        last_change_sig[k] = sig
        _remember_recent_change(state, k, sig, batch_no)
        _remember_recent_commit(state, k, commit_sig, batch_no)
        result.append(ev)
    pending.clear()
    return result


# ---------------------------------------------------------------------------
# Main recording loop
# ---------------------------------------------------------------------------

def main():
    args = parse_args()
    record_id = uuid.UUID(args.record_id) if args.record_id else uuid.uuid4()

    start_url = args.url or os.getenv("RECORDER_URL") or "https://example.com"
    if args.no_navigate:
        start_url = ""

    # Import Playwright
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright is not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    writer = PgWriter(
        host=args.db_host, port=args.db_port, database=args.db_name,
        user=args.db_user, password=args.db_password,
        recorder=args.recorder, folder_name=args.folder_name,
        is_baseline=args.is_baseline,
    )
    writer.ensure_session_meta(record_id, args.record_name)

    pause_flag = os.path.join(tempfile.gettempdir(), f"recorder_paused_{record_id}.flag")
    try:
        os.remove(pause_flag)
    except OSError:
        pass

    pw = sync_playwright().start()
    browser = None
    step_no = args.start_step
    dedup_state: dict = {}

    def _pop_raw_events(_page):
        try:
            return _page.evaluate("""
                (function() {
                    try {
                        var _saved = sessionStorage.getItem('__pwRecorderPending');
                        if (_saved) {
                            var _parsed = JSON.parse(_saved);
                            sessionStorage.removeItem('__pwRecorderPending');
                            if (Array.isArray(_parsed)) {
                                window.__pwRecorderEvents = _parsed.concat(window.__pwRecorderEvents || []);
                            }
                        }
                    } catch(e) {}
                    var evts = window.__pwRecorderEvents || [];
                    window.__pwRecorderEvents = [];
                    return evts;
                })()
            """)
        except Exception:
            return []

    def _drain_final_browser_events(_page):
        drained = []
        attempts = max(4, max(1, min(6, int(round(max(args.poll_interval, 0.05) / 0.05)))))
        for _ in range(attempts):
            raw_events = _pop_raw_events(_page)
            if raw_events:
                drained.extend(_filter_events(raw_events, dedup_state))
            time.sleep(args.poll_interval)
        return drained

    def _graceful_stop(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, _graceful_stop)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, _graceful_stop)

    try:
        browser = pw.chromium.launch(
            headless=args.headless,
            args=["--start-maximized"],
        )
        context = browser.new_context(no_viewport=True)
        page = context.new_page()

        # Inject recorder JS on every page (including navigations)
        context.add_init_script(RECORDER_JS)

        # Also inject into the current page immediately
        page.add_script_tag(content=RECORDER_JS)

        # Navigate to start URL
        if start_url:
            nav_started_at = time.perf_counter()
            page.goto(start_url, wait_until="domcontentloaded")
            page.wait_for_load_state("load")
            writer.set_next_recorded_delay(time.perf_counter() - nav_started_at)
            # Record the initial navigation as step 1
            step_no += 1
            nav_event = {
                "action": "navigate",
                "timestamp": int(time.time() * 1000),
                "url": start_url,
                "title": page.title(),
                "pageTitle": page.title(),
                "tag": "",
                "pos_x": 0, "pos_y": 0,
                "locators": {}, "id": "", "name": "",
                "inputType": "", "value": "", "text": "",
            }
            writer.insert_event(record_id, step_no, nav_event)
            print(f"[{step_no:>4}] navigate     {start_url}")

        print(f"Session  : {record_id}")
        print(f"Database : {args.db_name}  ({args.db_host}:{args.db_port})")
        print(f"Engine   : Playwright (Chromium)")
        print("Recording started — press Ctrl+C to stop.\n")

        last_url = start_url
        was_paused = False

        while True:
            # Check pause flag
            if os.path.exists(pause_flag):
                if not was_paused:
                    raw_events = _pop_raw_events(page)
                    if raw_events:
                        filtered = _filter_events(raw_events, dedup_state)
                        for event in filtered:
                            pw_info = event.get("playwright_info") or get_playwright_locator_info(page, event)
                            if pw_info:
                                event["playwright_info"] = pw_info
                            step_no += 1
                            writer.insert_event(record_id, step_no, event)
                            action = event.get("action", "?")
                            tag = event.get("tag") or "n/a"
                            print(f"[{step_no:>4}] {action:<12} <{tag}>  {event.get('url', '')}")
                    for event in _flush_pending(dedup_state):
                        try:
                            pw_info = event.get("playwright_info") or get_playwright_locator_info(page, event)
                            if pw_info:
                                event["playwright_info"] = pw_info
                        except Exception:
                            pass
                        step_no += 1
                        writer.insert_event(record_id, step_no, event)
                        print(f"[{step_no:>4}] {event.get('action', '?'):<12} (flushed on pause)")
                    was_paused = True
                else:
                    try:
                        _pop_raw_events(page)
                    except Exception:
                        pass
                time.sleep(args.poll_interval)
                continue
            if was_paused:
                was_paused = False

            # Check if browser is still open
            try:
                current_url = page.url
            except Exception:
                print("\nBrowser closed — stopping recording.")
                break

            # Detect page navigation
            if current_url != last_url and current_url != "about:blank":
                last_url = current_url
                step_no += 1
                nav_event = {
                    "action": "navigate",
                    "timestamp": int(time.time() * 1000),
                    "url": current_url,
                    "title": page.title() if page else "",
                    "pageTitle": page.title() if page else "",
                    "tag": "", "pos_x": 0, "pos_y": 0,
                    "locators": {}, "id": "", "name": "",
                    "inputType": "", "value": "", "text": "",
                }
                writer.insert_event(record_id, step_no, nav_event)
                print(f"[{step_no:>4}] navigate     {current_url}")

            # Poll events from the browser
            raw_events = _pop_raw_events(page)

            if raw_events:
                filtered = _filter_events(raw_events, dedup_state)
                for event in filtered:
                    # playwright_info is now embedded by the browser JS at event time;
                    # fall back to post-hoc lookup only if it's missing (old recorder).
                    pw_info = event.get("playwright_info") or get_playwright_locator_info(page, event)
                    if pw_info:
                        event["playwright_info"] = pw_info
                    step_no += 1
                    writer.insert_event(record_id, step_no, event)
                    action = event.get("action", "?")
                    tag = event.get("tag") or "n/a"
                    print(f"[{step_no:>4}] {action:<12} <{tag}>  {event.get('url', '')}")

            time.sleep(args.poll_interval)

    except KeyboardInterrupt:
        for event in _drain_final_browser_events(page):
            try:
                pw_info = event.get("playwright_info") or get_playwright_locator_info(page, event)
                if pw_info:
                    event["playwright_info"] = pw_info
            except Exception:
                pass
            step_no += 1
            writer.insert_event(record_id, step_no, event)
            print(f"[{step_no:>4}] {event.get('action', '?'):<12} (flushed on stop)")
        # Flush pending inputs
        for event in _flush_pending(dedup_state):
            try:
                pw_info = event.get("playwright_info") or get_playwright_locator_info(page, event)
                if pw_info:
                    event["playwright_info"] = pw_info
            except Exception:
                pass
            step_no += 1
            writer.insert_event(record_id, step_no, event)
            print(f"[{step_no:>4}] {event.get('action', '?'):<12} (flushed)")
        print(f"\nRecording stopped. Total steps: {step_no}")

    except Exception as exc:
        print(f"\nERROR: {exc}")

    finally:
        try:
            if browser:
                browser.close()
        except Exception:
            pass
        try:
            pw.stop()
        except Exception:
            pass
        writer.close()


if __name__ == "__main__":
    main()
