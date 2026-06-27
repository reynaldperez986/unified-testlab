import argparse
from datetime import datetime, timezone
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from typing import Any

import psycopg2
import psycopg2.pool
from psycopg2 import sql
from psycopg2.extras import Json, register_uuid
from selenium import webdriver
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from requests.exceptions import ConnectionError as RequestsConnectionError

# Register UUID <-> psycopg2 adapter once so uuid.UUID objects can be passed
# directly to parameterised queries without any str() conversion.
register_uuid()


def _normalize_field_name(value: Any) -> str | None:
    normalized = re.sub(r"[^0-9A-Za-z]+", "", str(value or "")).strip()
    return normalized or None


def _ensure_unique_field_name(cursor: Any, record_id: uuid.UUID, field_name: str | None) -> str | None:
    if not field_name:
        return None
    cursor.execute(
        """
        SELECT field_name
        FROM data
        WHERE field_name IS NOT NULL
          AND field_name LIKE %s;
        """,
        (f"{field_name}%",),
    )
    used_names = {str(row[0]) for row in cursor.fetchall() if row and row[0]}
    if field_name not in used_names:
        return field_name

    suffix = 2
    while True:
        candidate = f"{field_name}-{suffix}"
        if candidate not in used_names:
            return candidate
        suffix += 1


# Priority	Strategy	Selenium By
# 1	id	CSS_SELECTOR (#id)
# 2	name	CSS_SELECTOR (tag[name=…])
# 3	css	CSS_SELECTOR
# 4	xpath	XPATH
# 5	ariaLabel	CSS_SELECTOR
# 6	dataTestId	CSS_SELECTOR
# 7	className	CLASS_NAME (first class)
# 8	linkText	LINK_TEXT (<a> elements only)
# 9	partialLinkText	PARTIAL_LINK_TEXT (first 40 chars of link text)
# 10	tagName	TAG_NAME (last resort)
# 11	Coordinates	document.elementFromPoint()

RECORDER_JS = r"""
(function () {
    if (window.__webActionRecorderInstalled) {
        return;
    }

    window.__webActionRecorderInstalled = true;
    window.__webActionRecorder = window.__webActionRecorder || { events: [] };

    // Restore paused state across page navigations
    try {
        if (sessionStorage.getItem('__webActionRecorder_paused') === 'true') {
            window.__webActionRecorder.paused = true;
        }
    } catch(e) {}

    // Restore any events that were saved before a page navigation (e.g. form submit click)
    try {
        const _saved = sessionStorage.getItem('__webActionRecorder_pending');
        if (_saved) {
            const _parsed = JSON.parse(_saved);
            sessionStorage.removeItem('__webActionRecorder_pending');
            if (Array.isArray(_parsed)) {
                window.__webActionRecorder.events = _parsed.concat(window.__webActionRecorder.events);
            }
        }
    } catch(e) {}

    // Before any navigation, flush current events to sessionStorage so they survive
    window.addEventListener('beforeunload', function () {
        const _pending = window.__webActionRecorder && window.__webActionRecorder.events;
        if (_pending && _pending.length > 0) {
            try {
                const _existing = sessionStorage.getItem('__webActionRecorder_pending');
                const _existingArr = _existing ? JSON.parse(_existing) : [];
                sessionStorage.setItem('__webActionRecorder_pending', JSON.stringify(_existingArr.concat(_pending)));
                window.__webActionRecorder.events = [];  // drain — prevents pop_events from double-capturing the same events
            } catch(e) {}
        }
        // Persist paused state so it survives page navigation
        try {
            if (window.__webActionRecorder && window.__webActionRecorder.paused) {
                sessionStorage.setItem('__webActionRecorder_paused', 'true');
            } else {
                sessionStorage.removeItem('__webActionRecorder_paused');
            }
        } catch(e) {}
    });

    function getXPath(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) { return null; }
        if (element.id) { return `//*[@id="${element.id}"]`; }
        const nameAttr = element.getAttribute('name');
        if (nameAttr) { return `//${element.tagName.toLowerCase()}[@name="${nameAttr}"]`; }
        const segments = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
            let index = 1;
            let sibling = current.previousElementSibling;
            while (sibling) {
                if (sibling.nodeName === current.nodeName) { index++; }
                sibling = sibling.previousElementSibling;
            }
            segments.unshift(`${current.nodeName.toLowerCase()}[${index}]`);
            current = current.parentElement;
        }
        return `/${segments.join('/')}`;
    }

    function getCssSelector(element) {
        if (!element || element.nodeType !== Node.ELEMENT_NODE) { return null; }
        if (element.id) { return `#${CSS.escape(element.id)}`; }
        const nameAttr = element.getAttribute('name');
        if (nameAttr) { return `${element.tagName.toLowerCase()}[name="${CSS.escape(nameAttr)}"]`; }
        const path = [];
        let current = element;
        while (current && current.nodeType === Node.ELEMENT_NODE) {
            let selector = current.nodeName.toLowerCase();
            if (current.classList && current.classList.length > 0) {
                selector += `.${CSS.escape(current.classList[0])}`;
            }
            const parent = current.parentElement;
            if (parent) {
                let index = 1;
                let sibling = current.previousElementSibling;
                while (sibling) {
                    if (sibling.nodeName === current.nodeName) { index++; }
                    sibling = sibling.previousElementSibling;
                }
                selector += `:nth-of-type(${index})`;
            }
            path.unshift(selector);
            current = parent;
        }
        return path.join(' > ');
    }

    function getElementText(el) {
        if (!el) return null;
        const tag = el.tagName ? el.tagName.toLowerCase() : '';
        // Input/select/textarea: use value or placeholder — they have no innerText
        if (tag === 'input' || tag === 'textarea') {
            const type = (el.getAttribute('type') || '').toLowerCase();
            if (type === 'checkbox' || type === 'radio') {
                let labelText = '';
                try {
                    if (el.labels && el.labels.length) {
                        labelText = Array.from(el.labels)
                            .map(lbl => (lbl.innerText || lbl.textContent || '').trim())
                            .filter(Boolean)
                            .join(' ');
                    }
                    if (!labelText && el.id) {
                        const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (explicitLabel) {
                            labelText = (explicitLabel.innerText || explicitLabel.textContent || '').trim();
                        }
                    }
                    if (!labelText) {
                        const parentLabel = el.closest ? el.closest('label') : null;
                        if (parentLabel) {
                            labelText = (parentLabel.innerText || parentLabel.textContent || '').trim();
                        }
                    }
                } catch (e) {}
                if (labelText) {
                    labelText = labelText.replace(/\s+/g, ' ').trim();
                    const words = labelText.split(' ');
                    if (words.length % 2 === 0) {
                        const half = words.length / 2;
                        const left = words.slice(0, half).join(' ').trim();
                        const right = words.slice(half).join(' ').trim();
                        if (left && left === right) {
                            labelText = left;
                        }
                    }
                    return labelText.slice(0, 300);
                }
            }
            const v = (el.value || '').trim();
            if (v) return v.slice(0, 300);
            const p = (el.placeholder || el.getAttribute('placeholder') || '').trim();
            return p ? p.slice(0, 300) : null;
        }
        if (tag === 'select') {
            const opt = el.options && el.options[el.selectedIndex];
            return opt ? (opt.text || '').trim().slice(0, 300) : null;
        }
        // For any other element prefer innerText (respects CSS visibility),
        // fall back to textContent (includes hidden text).
        let text = (el.innerText || el.textContent || '').trim();
        if (text) return text.slice(0, 300);
        // If the element itself has no text (e.g. an icon <i> inside a <button>),
        // walk up to the first ancestor that has visible text.
        let ancestor = el.parentElement;
        while (ancestor && ancestor !== document.body) {
            text = (ancestor.innerText || ancestor.textContent || '').trim();
            if (text) return text.slice(0, 300);
            ancestor = ancestor.parentElement;
        }
        return null;
    }

    function resolveToggleControl(target) {
        if (!target || !target.closest) { return target || null; }
        if (target.matches && target.matches('input[type="checkbox"], input[type="radio"]')) {
            return target;
        }
        let control = null;
        const label = target.closest('label');
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
            const container = target.closest('.checkbox, .radio, [role="checkbox"], [role="radio"]');
            if (container) {
                control = container.querySelector('input[type="checkbox"], input[type="radio"]');
            }
        }
        return control || target;
    }

    function getSeleniumInfo(target) {
        if (!target) { return null; }
        try {
            const rect = target.getBoundingClientRect();
            const attrs = {};
            for (let i = 0; i < target.attributes.length; i++) {
                attrs[target.attributes[i].name] = target.attributes[i].value;
            }

            function normalizeLabelText(text) {
                text = (text || '').replace(/\s+/g, ' ').trim();
                if (!text) { return ''; }
                const words = text.split(' ');
                if (words.length % 2 === 0) {
                    const half = words.length / 2;
                    const left = words.slice(0, half).join(' ').trim();
                    const right = words.slice(half).join(' ').trim();
                    if (left && left === right) {
                        return left.slice(0, 200);
                    }
                }
                return text.slice(0, 200);
            }

            function labelTextFor(el) {
                if (!el) { return ''; }
                let text = '';
                try {
                    if (el.labels && el.labels.length) {
                        text = Array.from(el.labels)
                            .map(lbl => (lbl.innerText || lbl.textContent || '').trim())
                            .filter(Boolean)
                            .join(' ');
                    }
                    if (!text && attrs['aria-labelledby']) {
                        text = attrs['aria-labelledby']
                            .split(/\s+/)
                            .map(id => {
                                const node = document.getElementById(id);
                                return node ? (node.innerText || node.textContent || '').trim() : '';
                            })
                            .filter(Boolean)
                            .join(' ');
                    }
                    if (!text && el.id) {
                        const explicitLabel = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
                        if (explicitLabel) {
                            text = (explicitLabel.innerText || explicitLabel.textContent || '').trim();
                        }
                    }
                    if (!text) {
                        const parentLabel = el.closest ? el.closest('label') : null;
                        if (parentLabel) {
                            text = (parentLabel.innerText || parentLabel.textContent || '').trim();
                        }
                    }
                } catch(e) {}
                return normalizeLabelText(text);
            }

            function accessibleNameFor(el) {
                if (!el) { return ''; }
                let text = '';
                try {
                    text = attrs['aria-label'] || '';
                    if (!text) { text = labelTextFor(el); }
                    if (!text && el.title) { text = el.title; }
                    if (!text && el.placeholder) { text = el.placeholder; }
                    if (!text && /^(button|a)$/i.test(el.tagName || '')) {
                        text = (el.innerText || el.textContent || '').trim();
                    }
                    if (!text && /^(submit|button|reset)$/i.test(el.type || '')) {
                        text = el.value || '';
                    }
                } catch(e) {}
                return (text || '').trim().slice(0, 200);
            }

            return {
                tagName: target.tagName || null,
                id: target.id || null,
                className: typeof target.className === 'string' ? target.className.trim() : null,
                textContent: (target.textContent || '').trim().slice(0, 200),
                innerText: (target.innerText || '').trim().slice(0, 200),
                value: target.value !== undefined ? target.value : null,
                labelText: labelTextFor(target),
                accessibleName: accessibleNameFor(target),
                attributes: attrs,
                state: {
                    visible: !!(target.offsetWidth || target.offsetHeight || target.getClientRects().length),
                    disabled: !!target.disabled,
                    readonly: !!target.readOnly,
                    checked: !!target.checked,
                    selected: !!target.selected,
                },
                position: {
                    x: rect ? Math.round(rect.left + window.scrollX) : null,
                    y: rect ? Math.round(rect.top + window.scrollY) : null,
                    width: rect ? Math.round(rect.width) : null,
                    height: rect ? Math.round(rect.height) : null,
                },
            };
        } catch(e) {
            return null;
        }
    }

    function buildEvent(action, event) {
        const sourceTarget = event && event.target ? event.target : null;
        const target = resolveToggleControl(sourceTarget);
        const rect = target ? target.getBoundingClientRect() : null;
        const info = target ? getSeleniumInfo(target) : null;
        let toggleText = '';
        if (target && /^(checkbox|radio)$/i.test(target.getAttribute('type') || '')) {
            toggleText = ((info && (info.labelText || info.accessibleName)) || '').trim();
            if (!toggleText && sourceTarget) {
                toggleText = (sourceTarget.innerText || sourceTarget.textContent || '').trim();
            }
            toggleText = toggleText.slice(0, 200);
        }
        const locators = {
            id: target && target.id ? `#${CSS.escape(target.id)}` : null,
            name: target && target.getAttribute('name')
                ? `${target.tagName.toLowerCase()}[name="${target.getAttribute('name')}"]` : null,
            css: target ? getCssSelector(target) : null,
            xpath: target ? getXPath(target) : null,
            ariaLabel: target && target.getAttribute('aria-label')
                ? `${target.tagName.toLowerCase()}[aria-label="${target.getAttribute('aria-label')}"]` : null,
            dataTestId: target && target.getAttribute('data-testid')
                ? `[data-testid="${target.getAttribute('data-testid')}"]` : null,
            placeholder: target && target.getAttribute('placeholder')
                ? target.getAttribute('placeholder') : null,
            role: target && target.getAttribute('role')
                ? target.getAttribute('role') : null,
            title: target && target.getAttribute('title')
                ? target.getAttribute('title') : null,
            alt: target && target.getAttribute('alt')
                ? target.getAttribute('alt') : null,
            href: target && target.getAttribute('href')
                ? target.getAttribute('href') : null,
            type: target && target.getAttribute('type')
                ? target.getAttribute('type') : null,
            value: target && target.getAttribute('value')
                ? target.getAttribute('value') : null,
            className: target && target.classList && target.classList.length > 0
                ? target.classList[0] : null,
            linkText: target && target.tagName.toLowerCase() === 'a' && target.innerText.trim()
                ? target.innerText.trim().slice(0, 200) : null,
            partialLinkText: target && target.tagName.toLowerCase() === 'a' && target.innerText.trim()
                ? target.innerText.trim().slice(0, 40) : null,
            tagName: target ? target.tagName.toLowerCase() : null
        };
        if (toggleText && !locators.text) {
            locators.text = toggleText;
        }
        if (toggleText && !locators.label) {
            locators.label = toggleText;
        }
        return {
            action,
            timestamp: new Date().toISOString(),
            url: window.location.href,
            title: document.title,
            pageTitle: (function() {
                var h1 = document.querySelector('h1');
                if (h1 && h1.innerText && h1.innerText.trim()) return h1.innerText.trim().slice(0, 500);
                var h2 = document.querySelector('h2');
                if (h2 && h2.innerText && h2.innerText.trim()) return h2.innerText.trim().slice(0, 500);
                return (document.title || '').trim().slice(0, 500) || null;
            })(),
            tag: target ? target.tagName.toLowerCase() : null,
            pos_x: rect ? Math.round(rect.left + window.scrollX) : null,
            pos_y: rect ? Math.round(rect.top  + window.scrollY) : null,
            id: target ? (target.id || target.getAttribute('name') || null) : null,
            name: target ? target.getAttribute('name') || null : null,
            inputType: target ? target.getAttribute('type') || null : null,
            checked: target && ('checked' in target) ? !!target.checked : null,
            value: target && 'value' in target
                    ? (target.tagName && target.tagName.toLowerCase() === 'select'
                        ? (target.options && target.selectedIndex >= 0
                            ? (target.options[target.selectedIndex].text || '').trim()
                            : target.value)
                        : target.value)
                    : null,
            key: event && event.key ? event.key : null,
            text: toggleText || getElementText(target),
            locators: locators,
            selenium_info: info
        };
    }

    function pushEvent(action, event) {
        if (window.__webActionRecorder && window.__webActionRecorder.paused) { return; }
        try {
            window.__webActionRecorder.events.push(buildEvent(action, event));
        } catch (error) {
            window.__webActionRecorder.events.push({
                action,
                timestamp: new Date().toISOString(),
                url: window.location.href,
                error: String(error)
            });
        }
    }

    function buildNavigationEvent(action, direction) {
        return {
            action,
            direction: direction,
            timestamp: new Date().toISOString(),
            url: window.location.href,
            title: document.title,
            pageTitle: (function() {
                var h1 = document.querySelector('h1');
                if (h1 && h1.innerText && h1.innerText.trim()) return h1.innerText.trim().slice(0, 500);
                var h2 = document.querySelector('h2');
                if (h2 && h2.innerText && h2.innerText.trim()) return h2.innerText.trim().slice(0, 500);
                return (document.title || '').trim().slice(0, 500) || null;
            })(),
            tag: null,
            pos_x: null,
            pos_y: null,
            id: null,
            name: null,
            inputType: null,
            value: null,
            key: null,
            text: null,
            locators: {}
        };
    }

    function isToggleLabelClick(target) {
        if (!target || !target.closest) { return false; }
        const label = target.closest('label');
        if (!label) { return false; }
        let control = label.control || null;
        if (!control && label.htmlFor) {
            control = document.getElementById(label.htmlFor);
        }
        if (!control) {
            control = label.querySelector('input[type="checkbox"], input[type="radio"]');
        }
        if (!control) { return false; }
        const type = (control.getAttribute('type') || '').toLowerCase();
        return type === 'checkbox' || type === 'radio';
    }

    // Track navigation history using URL stack to detect back/forward
    window.__navigationStack = [window.location.href];
    window.__navigationStackIndex = 0;

    // Track when user navigates via link click
    document.addEventListener('click', function(e) {
        const target = e.target.closest('a[href]');
        if (target && target.href) {
            // Check if this is different from current page
            const newUrl = target.href;
            if (newUrl !== window.location.href) {
                // This will trigger navigation
                // We'll detect it with beforeunload and adjust stack
                window.__pendingNavigation = 'link';
            }
        }
    }, false);

    window.addEventListener('popstate', function (e) {
        // Suppress popstate fired within 2 s of a form submit — this is a SPA
        // History-API side-effect caused by the server redirect after POST, not
        // a real user pressing the Back button.
        if (window.__recorderLastSubmitAt && (Date.now() - window.__recorderLastSubmitAt) < 2000) {
            window.__recorderLastSubmitAt = null;
            return;
        }
        if (window.__webActionRecorder && window.__webActionRecorder.paused) { return; }
        let direction = 'unknown';
        const currentUrl = window.location.href;
        const previousUrl = window.__navigationStack[window.__navigationStackIndex];

        // Try to determine direction from stack history
        if (window.__navigationStack.includes(currentUrl)) {
            const newIndex = window.__navigationStack.indexOf(currentUrl);
            if (newIndex < window.__navigationStackIndex) {
                direction = 'back';
                window.__navigationStackIndex = newIndex;
            } else if (newIndex > window.__navigationStackIndex) {
                direction = 'forward';
                window.__navigationStackIndex = newIndex;
            }
        } else {
            // URL not in stack - could be forward to new page
            // Check if we were going forward (common case)
            if (window.__navigationStackIndex < window.__navigationStack.length - 1) {
                direction = 'forward';
                window.__navigationStackIndex++;
            } else {
                // Default to guessing based on history length if available
                direction = 'back'; // most common case
            }
        }

        try {
            window.__webActionRecorder.events.push(buildNavigationEvent('navigate_' + direction, direction));
        } catch (error) {
            window.__webActionRecorder.events.push({
                action: 'navigate_error',
                timestamp: new Date().toISOString(),
                url: window.location.href,
                error: String(error)
            });
        }
    });

    document.addEventListener('click', (e) => {
        pushEvent('click', e);
        const target = e.target && e.target.closest ? e.target.closest('button, input[type="submit"], input[type="image"]') : null;
        if (target) {
            window.__recorderLastSubmitAt = Date.now();
        }
    }, true);
    document.addEventListener('dblclick',    (e) => pushEvent('dblclick', e),    true);
    document.addEventListener('contextmenu', (e) => pushEvent('contextmenu', e), true);
    document.addEventListener('submit', (e) => {
        window.__recorderLastSubmitAt = Date.now();  // suppress spurious popstate after form navigation
    }, true);
    document.addEventListener('change',      (e) => pushEvent('change', e),      true);
    document.addEventListener('input',       (e) => pushEvent('input', e),       true);
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === 'Tab') { pushEvent('keydown', e); }
    }, true);

    // Debounced wheel listener — accumulates deltas and emits one scroll event per gesture
    let __scrollTimer = null;
    let __scrollDeltaX = 0;
    let __scrollDeltaY = 0;
    let __scrollLastEvent = null;
    document.addEventListener('wheel', (e) => {
        __scrollDeltaX += e.deltaX;
        __scrollDeltaY += e.deltaY;
        __scrollLastEvent = e;
        if (__scrollTimer) { clearTimeout(__scrollTimer); }
        __scrollTimer = setTimeout(() => {
            if (window.__webActionRecorder && window.__webActionRecorder.paused) {
                __scrollDeltaX = 0; __scrollDeltaY = 0; __scrollLastEvent = null; __scrollTimer = null;
                return;
            }
            try {
                const evt = buildEvent('scroll', __scrollLastEvent);
                evt.delta_x = Math.round(__scrollDeltaX);
                evt.delta_y = Math.round(__scrollDeltaY);
                window.__webActionRecorder.events.push(evt);
            } catch (err) {
                window.__webActionRecorder.events.push({
                    action: 'scroll',
                    timestamp: new Date().toISOString(),
                    url: window.location.href,
                    delta_x: Math.round(__scrollDeltaX),
                    delta_y: Math.round(__scrollDeltaY),
                    locators: {}
                });
            }
            __scrollDeltaX = 0;
            __scrollDeltaY = 0;
            __scrollLastEvent = null;
            __scrollTimer = null;
        }, 300);
    }, { passive: true, capture: true });
})();
"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class DbConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


# ---------------------------------------------------------------------------
# Database repository
# ---------------------------------------------------------------------------

class PsqlRepository:
    def __init__(self, config: DbConfig, recorder: str = "", folder_name: str = "", is_baseline: bool = False) -> None:
        self.recorder = recorder
        self.folder_name: str = (folder_name or "").strip()
        self.is_baseline: bool = is_baseline
        self.file_order: int = 1   # set once by main() before recording starts
        self._last_event_timestamp_ms: int | None = None
        self._next_recorded_delay_s: float | None = None
        self._pool: psycopg2.pool.ThreadedConnectionPool | None = None
        self._connect_pool(config)
        self._create_tables()

    def _connect_pool(self, config: DbConfig) -> None:
        """Create a ThreadedConnectionPool; retries until the pool is ready."""
        _kw = dict(
            host=config.host,
            port=config.port,
            dbname=config.database,
            user=config.user,
            password=config.password,
        )
        try:
            while True:
                self._pool = psycopg2.pool.ThreadedConnectionPool(1, 10, **_kw)
                if self._pool:
                    print("[pool] Connection pool created successfully")
                    break
                print("[pool] Establishing connection pool…")
        except (Exception, psycopg2.DatabaseError) as error:
            if self._pool:
                self._pool.closeall()
            print("[pool] Error while connecting to PostgreSQL:", error)
            raise

    def _disconnect_pool(self) -> None:
        if self._pool:
            self._pool.closeall()
            print("[pool] Connection pool closed.")

    def _get_conn(self):
        """Borrow a connection from the pool (autocommit=False by default)."""
        return self._pool.getconn()

    def _put_conn(self, conn, *, discard: bool = False) -> None:
        """Return (or discard) a connection to/from the pool."""
        try:
            self._pool.putconn(conn, close=discard)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Generic pool-backed query helpers (SELECT / UPDATE / DELETE / any DML)
    # ------------------------------------------------------------------

    def fetch_one(self, query: str, params=None) -> tuple | None:
        """Execute a SELECT and return the first row as a tuple, or None."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                result = cur.fetchone()
        except Exception:
            self._put_conn(conn, discard=True)
            raise
        else:
            self._put_conn(conn)
            return result

    def fetch_all(self, query: str, params=None) -> list[tuple]:
        """Execute a SELECT and return all rows as a list of tuples."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                result = cur.fetchall()
        except Exception:
            self._put_conn(conn, discard=True)
            raise
        else:
            self._put_conn(conn)
            return result

    def execute(self, query: str, params=None) -> int:
        """Execute a DML statement (UPDATE / DELETE / INSERT) and return the rowcount."""
        conn = self._get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rowcount = cur.rowcount
            conn.commit()
        except Exception:
            conn.rollback()
            self._put_conn(conn, discard=True)
            raise
        else:
            self._put_conn(conn)
            return rowcount

    def _create_tables(self) -> None:
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS data (
                    id          BIGSERIAL PRIMARY KEY,
                    record_id   UUID        NOT NULL,
                    step_no     INTEGER     NOT NULL,
                    field_name  TEXT,
                    value       TEXT,
                    folder_name TEXT,
                    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS locators (
                    id           BIGSERIAL PRIMARY KEY,
                    record_id    UUID        NOT NULL,
                    step_no      INTEGER     NOT NULL,
                    strategy     TEXT        NOT NULL,
                    locator      TEXT        NOT NULL,
                    is_primary   BOOLEAN     NOT NULL DEFAULT FALSE,
                    locator_rank INTEGER,
                    pos_x        FLOAT,
                    pos_y        FLOAT,
                    folder_name  TEXT,
                    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS locators_stat (
                    id           BIGSERIAL PRIMARY KEY,
                    run_id       UUID,
                    record_id    UUID        NOT NULL,
                    step_no      INTEGER     NOT NULL,
                    strategy     TEXT        NOT NULL,
                    locator      TEXT        NOT NULL,
                    is_primary   BOOLEAN     NOT NULL DEFAULT FALSE,
                    locator_rank INTEGER,
                    pos_x        FLOAT,
                    pos_y        FLOAT,
                    action       TEXT,
                    page_url     TEXT,
                    runner       TEXT,
                    author       TEXT,
                    public            BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_baseline       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS end_folders (
                    id                BIGSERIAL   PRIMARY KEY,
                    end_folder_id     UUID        NOT NULL UNIQUE,
                    end_folder        TEXT        NOT NULL,
                    end_folder_parent UUID        NOT NULL,
                    end_folder_order  INTEGER     NOT NULL DEFAULT 1,
                    end_file_order    INTEGER     NOT NULL DEFAULT 1,
                    file_type         TEXT        NOT NULL DEFAULT 'end-folder'
                                          CHECK (file_type IN ('end-folder', 'session')),
                    author            TEXT,
                    public            BOOLEAN     NOT NULL DEFAULT FALSE,
                    is_baseline       BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated      TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS steps (
                    id             BIGSERIAL PRIMARY KEY,
                    record_id     UUID        NOT NULL,
                    step_no        INTEGER     NOT NULL,
                    steps_description TEXT,
                    page_title  TEXT,
                    action         TEXT        NOT NULL,
                    page_url       TEXT        NOT NULL,
                    element_tag    TEXT,
                    locator_id     BIGINT      REFERENCES locators(id),
                    data_id        BIGINT      REFERENCES data(id),
                    raw_event      JSONB       NOT NULL,
                    recorder       TEXT,
                    runner         TEXT,
                    folder_name    TEXT,
                    locators_raw   JSONB,
                    field_name     TEXT,
                    field_value    TEXT,
                    pos_x          FLOAT,
                    pos_y          FLOAT,
                    strategy       TEXT,
                    locator        TEXT,
                    is_primary     BOOLEAN,
                    locator_rank   INTEGER,
                    folder_order     INTEGER     NOT NULL DEFAULT 1,
                    file_order     INTEGER     NOT NULL DEFAULT 1,
                    headless_state   BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    author           TEXT,
                    last_updated_by  TEXT,
                    parent_record_id UUID,
                    sub_record_id    UUID,
                    end_record       UUID,
                    file_type        TEXT        NOT NULL DEFAULT 'step'
                                         CHECK (file_type IN ('step', 'folder')),
                    is_baseline        BOOLEAN     NOT NULL DEFAULT FALSE,
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID,
                    "validation"     TEXT
                );
            """)
                cursor.execute("""
                ALTER TABLE steps ADD COLUMN IF NOT EXISTS "validation" TEXT;
                """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS run_table (
                    id               BIGSERIAL   PRIMARY KEY,
                    run_id           UUID        NOT NULL,
                    record_id        UUID        NOT NULL,
                    step_no          INTEGER     NOT NULL,
                    steps_description TEXT,
                    page_title  TEXT,
                    action           TEXT        NOT NULL,
                    page_url         TEXT        NOT NULL,
                    element_tag      TEXT,
                    locator_id       BIGINT      REFERENCES locators(id),
                    data_id          BIGINT      REFERENCES data(id),
                    raw_event        JSONB       NOT NULL,
                    status           TEXT        NOT NULL DEFAULT 'not_executed'
                        CHECK (status IN ('pass', 'fail', 'not_executed')),
                    message          TEXT,
                    author           TEXT,
                    runner           TEXT,
                    run_date         TIMESTAMPTZ,
                    folder_name      TEXT,
                    folder_order     INTEGER     NOT NULL DEFAULT 1,
                    file_order       INTEGER     NOT NULL DEFAULT 1,
                    is_baseline      BOOLEAN     NOT NULL DEFAULT FALSE,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    last_updated_by  TEXT,
                    parent_record_id UUID,
                    sub_record_id    UUID,
                    end_record       UUID,
                    file_type        TEXT        NOT NULL DEFAULT 'step'
                                         CHECK (file_type IN ('step', 'folder')),
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID,
                    "validation"     TEXT,
                    screenshot       BYTEA
                );
            """)
                cursor.execute("""
                ALTER TABLE run_table ADD COLUMN IF NOT EXISTS "validation" TEXT;
                ALTER TABLE run_table ADD COLUMN IF NOT EXISTS screenshot BYTEA;
                """)
                cursor.execute("""
                CREATE TABLE IF NOT EXISTS session_meta (
                    id               BIGSERIAL   PRIMARY KEY,
                    parent_folder_id UUID,
                    sub_folder_id    UUID,
                    end_folder_id    UUID,
                    record_id        UUID        NOT NULL UNIQUE,
                    record_name      TEXT        NOT NULL DEFAULT '',
                    recorder         TEXT,
                    folder_name      TEXT,
                    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
                );
            """)

            conn.commit()
        except Exception:
            conn.rollback()
            self._put_conn(conn, discard=True)
            raise
        else:
            self._put_conn(conn)

    @staticmethod
    def _build_step_description(event: dict[str, Any]) -> str:
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
            if key:
                return f"Key pressed: '{key}'"
            return "Key pressed"

        if action == "submit":
            return f"Form submitted" + (f" on <{tag}>" if tag else "")

        if action == "navigate":
            url = event.get("url") or ""
            return f"Navigate to {url}" if url else "Page navigation"

        if action == "scroll":
            return "Page scrolled"

        return f"{action.capitalize()} event" + (f" on <{tag}>" if tag else "")

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

    def insert_event(self, record_id: uuid.UUID, step_no: int, event: dict[str, Any]) -> None:
        event = self._annotate_recorded_delay(event)
        locator_id: int | None = None
        data_id: int | None = None

        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                # 1. Persist entered data (input/change/keydown only)
                data_payload = self._extract_data(event)
                if data_payload is not None:
                    data_payload = (
                        _ensure_unique_field_name(cursor, record_id, data_payload[0]),
                        data_payload[1],
                    )
                    cursor.execute(
                        """
                        INSERT INTO data (record_id, step_no, field_name, value, folder_name, engine)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id;
                        """,
                        (record_id, step_no, data_payload[0], data_payload[1], self.folder_name, "selenium"),
                    )
                    data_id = int(cursor.fetchone()[0])

                # 2. Persist all locator strategies
                _pos_x = event.get("pos_x")
                _pos_y = event.get("pos_y")
                _rec_strategy: str | None = None
                _rec_locator: str | None = None
                _rec_is_primary: bool | None = None
                _rec_rank: int | None = None
                extracted_locators = self._extract_locators(event)
                for strategy, locator, rank in extracted_locators:
                    is_primary = (rank == 1)
                    cursor.execute(
                        """
                        INSERT INTO locators (record_id, step_no, strategy, locator, is_primary, locator_rank, pos_x, pos_y, folder_name, engine)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        RETURNING id;
                        """,
                        (record_id, step_no, strategy, locator, is_primary, rank, _pos_x, _pos_y, self.folder_name, "selenium"),
                    )
                    row_id = int(cursor.fetchone()[0])
                    if is_primary and locator_id is None:
                        locator_id = row_id
                        _rec_strategy = strategy
                        _rec_locator = locator
                        _rec_is_primary = is_primary
                        _rec_rank = rank

                # 3. Persist the step into the recordings table with inline locator/data info
                _recorder = self.recorder or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"
                _field_name  = data_payload[0] if data_payload else None
                _field_value = data_payload[1] if data_payload else None

                # Use only the primary (rank-1) locator for the recordings row.
                # All strategies are already fully stored in the locators table;
                # inserting one row per strategy here was causing N-per-step duplication.
                _pri = extracted_locators[0] if extracted_locators else (None, None, None)
                _rec_strategy_val, _rec_locator_val, _rec_rank_val = _pri
                _rec_is_primary_val = (_rec_rank_val == 1) if _rec_rank_val is not None else None

                # Use the file_order value computed once at session startup.
                _file_order = self.file_order

                # Build human-readable step description
                _steps_description = self._build_step_description(event)

                # Always save to steps (both baseline and regular recordings)
                _tgt = sql.Identifier("steps")
                cursor.execute(
                    sql.SQL("""
                    INSERT INTO {}
                        (record_id, step_no, action, page_url, element_tag,
                         locator_id, data_id, raw_event, recorder, folder_name,
                         locators_raw, field_name, field_value, pos_x, pos_y,
                         strategy, locator, is_primary, locator_rank, file_order, is_baseline, author,
                         steps_description, page_title, engine)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s);
                    """).format(_tgt),
                    (
                        record_id,
                        step_no,
                        str(event.get("action", "unknown")),
                        str(event.get("url", "")),
                        event.get("tag"),
                        locator_id,
                        data_id,
                        Json(event),
                        _recorder,
                        self.folder_name,
                        Json(event.get("locators") or {}),
                        _field_name,
                        _field_value,
                        _pos_x,
                        _pos_y,
                        _rec_strategy_val,
                        _rec_locator_val,
                        _rec_is_primary_val,
                        _rec_rank_val,
                        _file_order,
                        self.is_baseline,
                        _recorder,
                        _steps_description,
                        str(event.get("pageTitle") or event.get("title") or "")[:500] or None,
                        "selenium",
                    ),
                )

            conn.commit()
        except Exception:
            conn.rollback()
            self._put_conn(conn, discard=True)
            raise
        else:
            self._put_conn(conn)

    @staticmethod
    def _extract_data(event: dict[str, Any]) -> tuple[str | None, str] | None:
        """Return (field_name, value) for data-entry events, else None.

        ``input`` and ``change``: save typed value.
        ``click`` / ``dblclick``: save the element's visible text (e.g. "Masters").
        ``keydown`` (Tab / Enter): skipped — value already captured by preceding input step.
        """
        action = event.get("action")
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
        )
        normalized_field_name = _normalize_field_name(field_name)

        if action in {"input", "change"}:
            value = event.get("value")
            if value is None:
                return None
            return (normalized_field_name or _normalize_field_name(value), str(value))

        if action in {"click", "dblclick"}:
            text = event.get("text")
            if not text:
                return None
            return (normalized_field_name or _normalize_field_name(text), str(text))

        return None

    @staticmethod
    def _extract_locators(event: dict[str, Any]) -> list[tuple[str, str, int]]:
        """Return ordered (strategy, locator, rank) tuples.

        Rank 1 = highest priority (xpath), rank 20 = lowest (dataTestId).
        Strategies match the priority order used in replay.py.
        Duplicate locator strings are skipped.
        """
        locator_block = event.get("locators") or {}
        input_type = str(event.get("inputType") or event.get("type") or "").strip().lower()
        if input_type in ("checkbox", "radio"):
            ordered_strategies = (
                "xpath", "id", "type", "text", "label", "name", "value", "className",
                "tagName", "css", "placeholder", "role", "title", "alt", "ariaLabel",
                "dataTestId", "href", "linkText", "partialLinkText",
            )
        else:
            ordered_strategies = (
                "xpath", "id", "name", "value", "placeholder", "class", "className",
                "tagName", "css", "href", "text", "label", "linkText", "partialLinkText",
                "type", "role", "title", "alt", "ariaLabel", "dataTestId",
            )
        results: list[tuple[str, str, int]] = []
        seen: set[str] = set()

        for rank, strategy in enumerate(ordered_strategies, start=1):
            raw = locator_block.get(strategy)
            if not raw:
                continue
            normalized = str(raw).strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            results.append((strategy, normalized, rank))

        return results

    def compute_file_order(self) -> int:
        """Count distinct (record_id, folder_name) pairs already in the target
        table and return the next file_order value. Called once per session at startup."""
        _tgt = sql.Identifier("steps")
        conn = self._get_conn()
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "SELECT COUNT(DISTINCT (record_id::text || '|' || COALESCE(folder_name,''))) "
                        "FROM {}"
                    ).format(_tgt)
                )
                count = cursor.fetchone()[0] or 0
            return int(count) + 1
        finally:
            self._put_conn(conn)

    def close(self) -> None:
        try:
            self._disconnect_pool()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Database bootstrap
# ---------------------------------------------------------------------------

def ensure_database_exists(config: DbConfig) -> None:
    """Create the target database if it does not already exist."""
    for maintenance_db in ("postgres", "template1"):
        conn = None
        try:
            conn = psycopg2.connect(
                host=config.host,
                port=config.port,
                dbname=maintenance_db,
                user=config.user,
                password=config.password,
            )
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s;", (config.database,))
                if cur.fetchone() is not None:
                    return
                cur.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(config.database)))
            print(f"Database '{config.database}' created.")
            return
        except Exception:
            if maintenance_db == "template1":
                raise
        finally:
            if conn is not None:
                conn.close()


# ---------------------------------------------------------------------------
# Selenium recorder
# ---------------------------------------------------------------------------

_NAV_TRIGGER_ACTIONS = frozenset({
    "click", "dblclick", "submit",
    "navigate_back", "navigate_forward", "navigate_unknown",
})


def _wait_page_ready(driver, timeout: float = 15.0) -> None:
    """Block until document.readyState is 'complete', or timeout expires.

    Called after every page interaction so the next poll cycle always starts
    on a fully-loaded page.  Failures are swallowed gracefully so a dead
    driver or JS error never crashes the recording loop.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if driver.execute_script("return document.readyState") == "complete":
                return
        except Exception:
            return  # driver gone or JS error — give up gracefully
        time.sleep(0.2)

_WEBDRIVERS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webdrivers")


class SeleniumActionRecorder:
    def __init__(self, start_url: str, headless: bool,
                 db_config: "DbConfig | None" = None,
                 db_pool: "psycopg2.pool.ThreadedConnectionPool | None" = None,
                 attach_port: int = 0,
                 remote_debug_port: int = 0) -> None:
        self._db_config = db_config
        self._db_pool   = db_pool
        self.initial_page_ready_delay_s: float | None = None

        # ── Build ChromeOptions from app_config ──────────────────────────────
        if attach_port:
            # Fast connectivity check — fail in ~2 s instead of hanging for minutes
            # if Chrome is not running with --remote-debugging-port=<attach_port>.
            import socket as _socket
            _s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
            _s.settimeout(2)
            try:
                _s.connect(("127.0.0.1", attach_port))
            except (OSError, _socket.timeout):
                _s.close()
                raise RuntimeError(
                    f"Chrome is not listening on port {attach_port}. "
                    f"Start a new recording first (this will launch Chrome with "
                    f"--remote-debugging-port={attach_port}), then use Add Step."
                )
            finally:
                try:
                    _s.close()
                except Exception:
                    pass

            # CDP attach only — assume target page is already open in Chrome.
            # No fallback: if Chrome is not listening on the port, raise immediately.
            _attach_opts = webdriver.ChromeOptions()
            _attach_opts.debugger_address = f"127.0.0.1:{attach_port}"
            self.driver = self._create_chrome_driver(_attach_opts)
            self._cdp_port = attach_port  # for non-intrusive CDP polling
            print(f"[attach] Connected to existing Chrome on port {attach_port}.")
            _wait = self._get_config("chrome.implicit_wait")
            self.driver.implicitly_wait(int(_wait) if _wait.isdigit() else 10)
            self._last_url: str = ""
            self._inject()
            # A new recording session always starts un-paused, regardless of any
            # stale paused state left by a previous recording or a Replay & Continue
            # freeze call.  Clearing this here (synchronously via Selenium
            # execute_script) is more reliable than the CDP WebSocket path because
            # it is guaranteed to complete before the first pop_events() call.
            # Also broadcast via CDP WebSocket to ALL tabs in case the user's active
            # tab differs from the one Selenium is currently attached to.
            _clear_pause_js = (
                "if(window.__webActionRecorder){"
                "  window.__webActionRecorder.paused=false;"
                "  window.__webActionRecorder.events=[];"
                "}"
                "try{sessionStorage.removeItem('__webActionRecorder_paused');}catch(e){}"
            )
            try:
                self.driver.execute_script(_clear_pause_js)
            except Exception:
                pass
            try:
                import urllib.request as _ureq, json as _jcdp, websocket as _wscdp
                with _ureq.urlopen(f"http://127.0.0.1:{attach_port}/json", timeout=3) as _r:
                    _all_tabs = _jcdp.loads(_r.read())
                for _tab in _all_tabs:
                    if _tab.get("type") == "page" and _tab.get("webSocketDebuggerUrl"):
                        try:
                            _c = _wscdp.create_connection(_tab["webSocketDebuggerUrl"], timeout=3)
                            _c.send(_jcdp.dumps({"id": 1, "method": "Runtime.evaluate",
                                                 "params": {"expression": _clear_pause_js}}))
                            _c.recv()
                            _c.close()
                        except Exception:
                            pass
            except Exception:
                pass
        else:
            # Normal: launch a new Chrome window (used by start_recording).
            options = webdriver.ChromeOptions()
            if headless:
                options.add_argument("--headless=new")
            _extra = self._get_config("chrome.extra_arguments")
            for _arg in (ln.strip() for ln in _extra.splitlines()):
                if _arg:
                    options.add_argument(_arg)
            if remote_debug_port:
                options.add_argument(f"--remote-debugging-port={remote_debug_port}")
            self._cdp_port = remote_debug_port or 0  # for non-intrusive CDP polling
            import json as _json
            for _line in self._get_config("chrome.experimental_options", "").splitlines():
                _line = _line.strip()
                if not _line or "=" not in _line:
                    continue
                _opt_key, _, _opt_val = _line.partition("=")
                _opt_key = _opt_key.strip()
                try:
                    _opt_parsed = _json.loads(_opt_val.strip())
                except Exception:
                    _opt_parsed = _opt_val.strip()
                if _opt_key:
                    options.add_experimental_option(_opt_key, _opt_parsed)
            self.driver = self._create_chrome_driver(options)
            _wait = self._get_config("chrome.implicit_wait")
            self.driver.implicitly_wait(int(_wait) if _wait.isdigit() else 10)
            self._last_url: str = ""
            if start_url:
                _nav_started_at = time.perf_counter()
                self.driver.get(start_url)
                _wait_page_ready(self.driver)
                self.initial_page_ready_delay_s = max(0.0, time.perf_counter() - _nav_started_at)
            self._inject()

    # ── Config helper ──────────────────────────────────────────────────────

    def _get_config(self, key: str, default: str = "") -> str:
        """Read a value from the Django app_config table.
        Uses the shared connection pool when available; falls back to a direct connection."""
        # --- Pool path (preferred) ---
        if self._db_pool is not None:
            conn = None
            try:
                conn = self._db_pool.getconn()
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM app_config WHERE key = %s", [key])
                    row = cur.fetchone()
                    return row[0] if row else default
            except Exception:
                return default
            finally:
                if conn is not None:
                    try:
                        self._db_pool.putconn(conn)
                    except Exception:
                        pass
        # --- Direct connection fallback ---
        if self._db_config is None:
            return default
        try:
            conn = psycopg2.connect(
                host=self._db_config.host,
                port=self._db_config.port,
                dbname=self._db_config.database,
                user=self._db_config.user,
                password=self._db_config.password,
            )
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT value FROM app_config WHERE key = %s", [key])
                    row = cur.fetchone()
                    return row[0] if row else default
            finally:
                conn.close()
        except Exception:
            return default

    # ── ChromeDriver factory ──────────────────────────────────────────────

    def _create_chrome_driver(self, options: webdriver.ChromeOptions) -> webdriver.Chrome:
        """Start ChromeDriver: pinned config → Selenium Manager → webdriver_manager → PATH."""
        # Tier 0 — pinned driver configured via /configuration/ (webdrivers/chrome/ folder)
        _wd_filename = self._get_config("chrome.webdriver_path", "").strip()
        if _wd_filename:
            _wd_path = os.path.join(_WEBDRIVERS_DIR, "chrome", _wd_filename)
            if os.path.isfile(_wd_path):
                try:
                    return webdriver.Chrome(service=Service(_wd_path), options=options)
                except WebDriverException as _e:
                    print(f"[warning] Pinned chromedriver failed, falling back to auto-detection.")

        # Tier 1 — Selenium Manager (bundled, handles current Chrome automatically)
        try:
            return webdriver.Chrome(options=options)
        except WebDriverException:
            pass

        # Tier 2 — webdriver_manager (downloads matching driver)
        try:
            return webdriver.Chrome(
                service=Service(ChromeDriverManager().install()), options=options)
        except (RequestsConnectionError, Exception):
            pass

        # Tier 3 — chromedriver on PATH
        try:
            return webdriver.Chrome(service=Service("chromedriver"), options=options)
        except WebDriverException as exc:
            raise WebDriverException(
                "Could not start ChromeDriver via Selenium manager, webdriver_manager, "
                "or PATH. Please install chromedriver manually or configure one in "
                "/configuration/ under Chrome -> ChromeDriver Executable."
            ) from exc

    def _inject(self) -> None:
        """Re-inject recorder JS when the page has navigated or been reloaded.

        Handles both URL-change navigations AND same-URL reloads (e.g. JSF
        autosubmit POST-backs) by checking whether the recorder flag is still
        present in the page JS context.

        Swallows exceptions silently so system/blank pages (e.g. chrome://new-tab-page/)
        do not crash the recording loop — injection is retried on the next poll
        cycle once the user navigates to a real page.
        """
        try:
            current_url = self.driver.current_url
        except Exception:
            return
        needs_inject = current_url != self._last_url
        if not needs_inject:
            # Same URL — check if the page was reloaded (JS context lost)
            try:
                needs_inject = not self.driver.execute_script(
                    "return !!window.__webActionRecorderInstalled"
                )
            except Exception:
                needs_inject = True
        if needs_inject:
            try:
                self.driver.execute_script(RECORDER_JS)
                self._last_url = current_url  # only update on success
            except Exception:
                pass  # retry next poll cycle

    def pop_events(self) -> list[dict[str, Any]]:
        """Drain and return buffered events from all attached browser tabs.

        Record More / attach mode can connect ChromeDriver to a different tab
        than the one the user continues interacting with. Poll every page tab,
        inject the recorder if needed, and aggregate events across all tabs.

        When a CDP port is available, uses direct WebSocket Runtime.evaluate
        which does NOT steal focus from the page — this prevents native
        <select> dropdowns from being closed by the polling cycle.
        Falls back to Selenium execute_script only when CDP is unavailable.
        """
        # ── CDP WebSocket path (preferred — no focus steal) ──────────────────
        cdp_port = getattr(self, "_cdp_port", 0)
        if cdp_port:
            try:
                return self._pop_events_cdp(cdp_port)
            except Exception:
                pass  # fall through to Selenium path

        # ── Selenium path (fallback) ────────────────────────────────────────
        try:
            original_handle = self.driver.current_window_handle
        except Exception:
            original_handle = None

        try:
            handles = list(self.driver.window_handles)
        except Exception:
            handles = [original_handle] if original_handle else []

        events: list[dict[str, Any]] = []
        seen_handles: set[str] = set()

        for handle in handles:
            if not handle or handle in seen_handles:
                continue
            seen_handles.add(handle)
            try:
                # Only switch tabs when there are multiple handles AND
                # the target is not the current one.  switch_to.window()
                # triggers a focus change via ChromeDriver which closes
                # native <select> dropdowns in the inspected page.
                if handle != original_handle:
                    self.driver.switch_to.window(handle)
                self._inject()
                tab_events = self.driver.execute_script("""
                    if (!window.__webActionRecorder) { return []; }
                    if (window.__webActionRecorder.paused) {
                        window.__webActionRecorder.events = [];
                        return [];
                    }
                    // Also recover any sessionStorage-saved events not yet restored by inject
                    try {
                        const _saved = sessionStorage.getItem('__webActionRecorder_pending');
                        if (_saved) {
                            const _parsed = JSON.parse(_saved);
                            sessionStorage.removeItem('__webActionRecorder_pending');
                            if (Array.isArray(_parsed)) {
                                window.__webActionRecorder.events = _parsed.concat(window.__webActionRecorder.events);
                            }
                        }
                    } catch(e) {}
                    const copy = window.__webActionRecorder.events.slice();
                    window.__webActionRecorder.events = [];
                    return copy;
                """)
                if isinstance(tab_events, list):
                    events.extend(tab_events)
            except JavascriptException:
                continue
            except Exception:
                continue

        if original_handle:
            try:
                self.driver.switch_to.window(original_handle)
            except Exception:
                pass

        return events

    # ── CDP WebSocket event polling (no focus steal) ─────────────────────────

    _CDP_DRAIN_JS = (
        "(() => {"
        "  if (!window.__webActionRecorder) return JSON.stringify([]);"
        "  if (window.__webActionRecorder.paused) {"
        "    window.__webActionRecorder.events = [];"
        "    return JSON.stringify([]);"
        "  }"
        "  try {"
        "    const _saved = sessionStorage.getItem('__webActionRecorder_pending');"
        "    if (_saved) {"
        "      const _parsed = JSON.parse(_saved);"
        "      sessionStorage.removeItem('__webActionRecorder_pending');"
        "      if (Array.isArray(_parsed)) {"
        "        window.__webActionRecorder.events = _parsed.concat(window.__webActionRecorder.events);"
        "      }"
        "    }"
        "  } catch(e) {}"
        "  const copy = window.__webActionRecorder.events.slice();"
        "  window.__webActionRecorder.events = [];"
        "  return JSON.stringify(copy);"
        "})()"
    )

    _CDP_INJECT_CHECK_JS = "typeof window.__webActionRecorderInstalled !== 'undefined'"

    def _pop_events_cdp(self, port: int) -> list[dict[str, Any]]:
        """Collect events from all browser tabs via CDP WebSocket.

        This method communicates with Chrome's DevTools protocol directly,
        bypassing ChromeDriver's execute_script which can steal page focus.
        """
        import urllib.request as _ureq
        import json as _jcdp

        try:
            import websocket as _ws
        except ImportError:
            raise RuntimeError("websocket-client not installed")

        with _ureq.urlopen(f"http://127.0.0.1:{port}/json", timeout=3) as _r:
            all_targets = _jcdp.loads(_r.read())

        events: list[dict[str, Any]] = []
        _msg_id = 1

        for target in all_targets:
            if target.get("type") != "page":
                continue
            ws_url = target.get("webSocketDebuggerUrl")
            if not ws_url:
                continue
            try:
                conn = _ws.create_connection(
                    ws_url, timeout=3,
                    suppress_origin=True,  # avoid origin rejection in newer Chrome
                )
                try:
                    # Check if recorder is injected
                    conn.send(_jcdp.dumps({
                        "id": _msg_id,
                        "method": "Runtime.evaluate",
                        "params": {"expression": self._CDP_INJECT_CHECK_JS}
                    }))
                    _msg_id += 1
                    resp = _jcdp.loads(conn.recv())
                    result = resp.get("result", {}).get("result", {})
                    if result.get("value") is not True:
                        # Inject recorder JS into this tab
                        conn.send(_jcdp.dumps({
                            "id": _msg_id,
                            "method": "Runtime.evaluate",
                            "params": {"expression": RECORDER_JS}
                        }))
                        _msg_id += 1
                        conn.recv()  # wait for completion

                    # Drain events
                    conn.send(_jcdp.dumps({
                        "id": _msg_id,
                        "method": "Runtime.evaluate",
                        "params": {"expression": self._CDP_DRAIN_JS}
                    }))
                    _msg_id += 1
                    resp = _jcdp.loads(conn.recv())
                    val = resp.get("result", {}).get("result", {}).get("value", "[]")
                    tab_events = _jcdp.loads(val) if isinstance(val, str) else []
                    if isinstance(tab_events, list):
                        events.extend(tab_events)
                finally:
                    conn.close()
            except Exception:
                continue

        return events

    def close(self) -> None:
        # Do NOT quit the driver — Chrome must stay open on its
        # remote-debugging port so "Add Step" can attach to it later.
        # We only close the ChromeDriver *service connection* (so the
        # chromedriver.exe helper process exits), not Chrome itself.
        try:
            self.driver.service.stop()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record Selenium web actions and save actions/data/locators into PostgreSQL."
    )
    parser.add_argument("--url", default=None,
                        help="Initial URL opened in Chrome (env: RECORDER_URL).")
    parser.add_argument("--headless", action="store_true",
                        help="Run Chrome in headless mode.")
    parser.add_argument("--poll-interval", type=float, default=0.5,
                        help="Event polling interval in seconds (default: 0.5).")
    parser.add_argument("--db-host",     default=os.getenv("PGHOST",     "localhost"))
    parser.add_argument("--db-port",     type=int,
                                         default=int(os.getenv("PGPORT", "5432")))
    parser.add_argument("--db-name",     default=os.getenv("PGDATABASE", "automation_db"))
    parser.add_argument("--db-user",     default=os.getenv("PGUSER",     "postgres"))
    parser.add_argument("--db-password", default=os.getenv("PGPASSWORD", "password"))
    parser.add_argument("--record-name", default="",
                        help="Human-readable name for this recording session.")
    parser.add_argument("--recorder", default="",
                        help="Username of the person who started the recording.")
    parser.add_argument("--folder-name", default="",
                        help="Folder to save this recording into.")
    parser.add_argument("--is-baseline", action="store_true",
                        help="Save recording as baseline (into recordings table).")
    parser.add_argument("--record-id", default="",
                        help="UUID to use for this recording session (generated by web view).")
    parser.add_argument("--start-step", type=int, default=0,
                        help="Initial step_no offset — use to append steps to an existing session.")
    parser.add_argument("--attach-port", type=int, default=0,
                        help="Attach to an already-running Chrome via remote debugging port instead of launching a new browser.")
    parser.add_argument("--remote-debug-port", type=int, default=0,
                        help="Start Chrome with --remote-debugging-port=N so it can be reattached later.")
    parser.add_argument("--no-navigate", action="store_true",
                        help="Do not navigate to any URL on startup (Add Step / continue mode).")
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Event deduplication
# ---------------------------------------------------------------------------

def _element_key(ev: dict) -> str:
    """Stable, per-session identity key for deduplication."""
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


def _filter_recorded_events(events: list[dict], state: dict) -> list[dict]:
    """Remove recording noise without losing any meaningful action.

    Uses ``state`` (a mutable dict that survives across poll batches) with:
      ``state['last_val']``  — {element_key: last_emitted_value}  (change dedup)
      ``state['pending']``   — {element_key: full_event}           (input deferral)

    Rules:
    1. ``input`` events are never emitted immediately — stored in ``pending``.
       The pending event is flushed (emitted) only when a "commit" event arrives:
       Tab/Enter keydown, click, dblclick, submit, change, or any navigation.
       This collapses ALL per-keystroke inputs into one step regardless of how
       many poll batches they were spread across.
    2. A ``change`` event whose value matches the last emitted value for the same
       element is dropped — it duplicates the flushed input.
    3. ``navigate_back`` / ``navigate_unknown`` are dropped when preceded by a
       ``submit`` in the same batch (SPA History-API artefact).
    """
    last_val: dict = state.setdefault("last_val", {})
    pending:  dict = state.setdefault("pending", {})
    last_change_sig = state.setdefault("last_change_sig", {})
    batch_no = int(state.get("batch_no", 0)) + 1
    state["batch_no"] = batch_no

    result: list[dict] = []

    def _flush(key: str | None = None) -> None:
        """Emit pending input(s) and update last_val."""
        if key is not None:
            if key in pending:
                ev = pending.pop(key)
                ev.pop("_pending_batch_no", None)
                if ev.get("action") in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev)):
                    ev["action"] = "change"
                sig = _change_signature(ev)
                commit_sig = _commit_signature(ev)
                if not _is_recent_duplicate_change(state, key, sig, batch_no):
                    last_val[key] = ev.get("value")
                    last_change_sig[key] = sig
                    _remember_recent_change(state, key, sig, batch_no)
                    _remember_recent_commit(state, key, commit_sig, batch_no)
                    result.append(ev)
        else:
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

    i = 0
    while i < len(events):
        ev = events[i]
        action = ev.get("action")
        key = _element_key(ev)

        if action == "input":
            # Defer — keep only the latest event per element
            ev["_pending_batch_no"] = batch_no
            pending[key] = ev
            i += 1

        elif action == "change":
            # change  supersedes any pending input for this element
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
            # Tab / Enter commits the field — flush pending input first
            _flush(key)
            result.append(ev)
            i += 1

        elif action in ("click", "dblclick"):
            # Clicks on <select> elements are noise — the user is opening the
            # dropdown or selecting an option; the meaningful event is `change`.
            # Defer them like `input` events so the subsequent `change` can
            # discard them (pending.pop) rather than emitting a bare click.
            if action in ("click", "dblclick") and (ev.get("tag") == "select" or _is_toggle_input(ev)):
                ev["_pending_batch_no"] = batch_no
                pending[key] = ev          # will be dropped when `change` arrives
                i += 1
                continue
            # Any other intentional user action flushes all pending inputs first
            _flush()
            result.append(ev)
            i += 1

        elif action in ("navigate_back", "navigate_unknown"):
            _flush()
            _prev = result[-1].get("action") if result else None
            if _prev != "submit":
                last_val.clear()
                last_change_sig.clear()
                pending.clear()
                result.append(ev)
            i += 1

        else:
            if action and action.startswith("navigate_"):
                _flush()
                last_val.clear()
                last_change_sig.clear()
                pending.clear()
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


def _flush_pending_inputs(state: dict) -> list[dict]:
    """Flush any inputs still pending at end-of-session (e.g. user closed browser
    without clicking anything after the last keystroke)."""
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
# Entry point
# ---------------------------------------------------------------------------

def main(url: str = "https://example.com") -> None:
    args = parse_args()
    record_id = uuid.UUID(args.record_id) if args.record_id else uuid.uuid4()

    # Resolve the start URL:
    #  - --url / RECORDER_URL always wins when explicitly provided.
    #  - --no-navigate, --attach-port, or --start-step > 0: no navigation wanted.
    #  - Normal start_recording from Django: --url is always passed, so the
    #    default below is only reached in pure CLI usage.
    if args.url:
        start_url = args.url
    elif os.getenv("RECORDER_URL"):
        start_url = os.getenv("RECORDER_URL")
    elif args.no_navigate or args.attach_port or args.start_step > 0:
        start_url = ""   # continue / attach — do not navigate
    else:
        start_url = url  # pure CLI fallback ("https://example.com")

    db_config = DbConfig(
        host=args.db_host,
        port=args.db_port,
        database=args.db_name,
        user=args.db_user,
        password=args.db_password,
    )

    # Keep None so the finally block is safe if initialisation fails mid-way.
    repository: PsqlRepository | None = None
    recorder: SeleniumActionRecorder | None = None

    try:
        # Determine the recorder (Django logged-in user passed via --recorder, else OS fallback)
        _session_recorder = (
            args.recorder
            or os.environ.get("USERNAME")
            or os.environ.get("USER")
            or "unknown"
        )

        ensure_database_exists(db_config)
        repository = PsqlRepository(db_config, recorder=_session_recorder,
                                    folder_name=args.folder_name,
                                    is_baseline=args.is_baseline)

        # Compute file_order once — number of existing distinct (record_id, folder_name)
        # pairs + 1.  All events in this session share the same value.
        repository.file_order = repository.compute_file_order()

        # Persist the human-readable name and recorder for this session
        _meta_conn = repository._get_conn()
        try:
            with _meta_conn.cursor() as _cur:
                _cur.execute(
                    "INSERT INTO session_meta (record_id, record_name, recorder, folder_name, engine, is_baseline) VALUES (%s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (record_id) DO UPDATE SET is_baseline = EXCLUDED.is_baseline;",
                    (record_id, args.record_name or "", _session_recorder, repository.folder_name, "selenium", args.is_baseline),
                )
            _meta_conn.commit()
        except Exception:
            _meta_conn.rollback()
            repository._put_conn(_meta_conn, discard=True)
            raise
        else:
            repository._put_conn(_meta_conn)

        recorder = SeleniumActionRecorder(start_url=start_url, headless=args.headless,
                                           db_config=db_config, db_pool=repository._pool,
                                           attach_port=args.attach_port,
                                           remote_debug_port=args.remote_debug_port)
        if recorder.initial_page_ready_delay_s is not None:
            repository.set_next_recorded_delay(recorder.initial_page_ready_delay_s)
        step_no = args.start_step

        print(f"Session  : {record_id}")
        print(f"Database : {args.db_name}  ({args.db_host}:{args.db_port})")
        print("Recording started — press Ctrl+C to stop.\n")

        _dedup_state: dict = {}  # keys: 'last_val', 'pending' — survive across poll batches
        # Pause flag is keyed on record_id (UUID) so views.py and main.py always
        # agree on exactly the same path — no PID/tempdir mismatch possible.
        _pause_flag = os.path.join(tempfile.gettempdir(), f"recorder_paused_{record_id}.flag")
        # Remove any stale flag from a previous run with the same record_id.
        try:
            os.remove(_pause_flag)
        except OSError:
            pass

        def _drain_final_browser_events() -> list[dict[str, Any]]:
            drained: list[dict[str, Any]] = []
            attempts = max(1, min(6, int(round(max(args.poll_interval, 0.05) / 0.05))))
            attempts = max(attempts, 4)
            for _ in range(attempts):
                try:
                    raw_events = recorder.pop_events()
                except Exception:
                    raw_events = []
                if raw_events:
                    drained.extend(_filter_recorded_events(raw_events, _dedup_state))
                time.sleep(max(args.poll_interval, 0.05))
            return drained

        _was_paused = False
        while True:
            # Python-level pause gate — set by pause_recording_ajax via flag file.
            # This is the authoritative guard: even if RECORDER_JS somehow delivers
            # events while paused, they are never inserted into the database.
            if os.path.exists(_pause_flag):
                if not _was_paused:
                    for event in _filter_recorded_events(recorder.pop_events(), _dedup_state):
                        step_no += 1
                        repository.insert_event(record_id=record_id, step_no=step_no, event=event)
                        print(
                            f"[{step_no:>4}] {event.get('action', '?'):<12} "
                            f"<{event.get('tag') or 'n/a'}>  {event.get('url', '')}"
                        )
                    for event in _flush_pending_inputs(_dedup_state):
                        step_no += 1
                        repository.insert_event(record_id=record_id, step_no=step_no, event=event)
                        print(
                            f"[{step_no:>4}] {event.get('action', '?'):<12} "
                            f"<{event.get('tag') or 'n/a'}>  (flushed on pause)"
                        )
                    _was_paused = True
                else:
                    try:
                        recorder.pop_events()
                    except Exception:
                        pass
                time.sleep(max(args.poll_interval, 0.05))
                continue
            if _was_paused:
                _was_paused = False
            raw_events = recorder.pop_events()
            for event in _filter_recorded_events(raw_events, _dedup_state):
                step_no += 1
                repository.insert_event(record_id=record_id, step_no=step_no, event=event)
                print(
                    f"[{step_no:>4}] {event.get('action', '?'):<12} "
                    f"<{event.get('tag') or 'n/a'}>  {event.get('url', '')}"
                )
            time.sleep(max(args.poll_interval, 0.05))

    except KeyboardInterrupt:
        # One last drain lets submit-button clicks survive the final navigation
        # even when the recorder is stopped immediately after the user action.
        for event in _drain_final_browser_events():
            step_no += 1
            repository.insert_event(record_id=record_id, step_no=step_no, event=event)
            print(
                f"[{step_no:>4}] {event.get('action', '?'):<12} "
                f"<{event.get('tag') or 'n/a'}>  (flushed on stop)"
            )
        # Flush any input still pending (user stopped recording mid-field)
        for event in _flush_pending_inputs(_dedup_state):
            step_no += 1
            repository.insert_event(record_id=record_id, step_no=step_no, event=event)
            print(f"[{step_no:>4}] {event.get('action', '?'):<12} <{event.get('tag') or 'n/a'}>  (flushed on stop)")
        print("\nRecording stopped.")
    except WebDriverException as exc:
        _msg = getattr(exc, "msg", None) or str(exc)
        try:
            print(_msg.encode("ascii", "replace").decode("ascii"))
        except Exception:
            print(repr(exc))
        sys.stdout.flush()
    except Exception as exc:
        print(f"Unexpected error: {exc}")
        sys.stdout.flush()
        raise
    finally:
        sys.stdout.flush()
        # Clean up the pause flag file so it doesn't linger after process exit.
        try:
            _flag_path = os.path.join(tempfile.gettempdir(), f"recorder_paused_{record_id}.flag")
            if os.path.exists(_flag_path):
                os.remove(_flag_path)
        except OSError:
            pass
        if recorder is not None:
            recorder.close()
        if repository is not None:
            repository.close()


if __name__ == "__main__":
    main()
