"""web_scraper.py
-----------------
Click-driven Selenium scraper that snapshots the current page into PostgreSQL.

Behavior:
- Opens or attaches to Chrome
- Injects a runtime that listens for page clicks
- On every click, scans visible page elements and collects all supported locators
- Saves each discovered element into ai_databank
- Deduplicates by page URL + element fingerprint
- Stores a screenshot in BYTEA
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import sys
import time
from typing import Any

import psycopg2
from psycopg2.extras import Json
from requests.exceptions import ConnectionError as RequestsConnectionError
from selenium import webdriver
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from webdriver_manager.chrome import ChromeDriverManager
from webdriver_manager.microsoft import EdgeChromiumDriverManager


DB_CONFIG = {
		"dbname": os.getenv("PGDATABASE", "automation_db"),
		"user": os.getenv("PGUSER", "postgres"),
		"password": os.getenv("PGPASSWORD", "password"),
		"host": os.getenv("PGHOST", "localhost"),
		"port": os.getenv("PGPORT", "5432"),
}

_WEBDRIVERS_CHROME_DIR = os.path.join(os.path.dirname(__file__), "webdrivers", "chrome")
_WEBDRIVERS_EDGE_DIR = os.path.join(os.path.dirname(__file__), "webdrivers", "edge")

ORDERED_STRATEGIES = (
		"xpath", "id", "name", "value", "placeholder", "class", "className",
		"tagName", "css", "href", "text", "linkText", "partialLinkText",
		"type", "role", "title", "alt", "ariaLabel", "dataTestId",
)

STRATEGY_BY = {
		"xpath": "xpath",
		"id": "id",
		"name": "name",
		"value": "css selector",
		"placeholder": "css selector",
		"class": "class name",
		"className": "class name",
		"tagName": "tag name",
		"css": "css selector",
		"href": "css selector",
		"text": "xpath",
		"linkText": "link text",
		"partialLinkText": "partial link text",
		"type": "css selector",
		"role": "css selector",
		"title": "css selector",
		"alt": "css selector",
		"ariaLabel": "css selector",
		"dataTestId": "css selector",
}

ATTR_WRAP = {
		"value":       '[value="{value}"]',
		"placeholder": '[placeholder="{value}"]',
		"type":        '[type="{value}"]',
		"role":        '[role="{value}"]',
		"title":       '[title="{value}"]',
		"alt":         '[alt="{value}"]',
		"href":        '[href="{value}"]',
		"text":        '//*[normalize-space(text())="{value}"]',
		# The following strategies use CSS attribute selectors.  We store the
		# raw attribute value in locator_property.locators (normalised via
		# _normalize_strategy_locator) and produce the full CSS expression in
		# prepared_locator so both Selenium (By.CSS_SELECTOR) and Playwright
		# (.locator()) can consume it without additional wrapping.
		"name":        '[name="{value}"]',
		"ariaLabel":   '[aria-label="{value}"]',
		"dataTestId":  '[data-testid="{value}"]',
}

SCRAPER_JS = r"""
(function() {
	if (window.__aiDatabankScraper) {
		return true;
	}

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
		if (!el) { return null; }
		const tag = el.tagName ? el.tagName.toLowerCase() : '';
		if (tag === 'input' || tag === 'textarea') {
			const val = (el.value || '').trim();
			if (val) { return val.slice(0, 300); }
			const placeholder = (el.placeholder || el.getAttribute('placeholder') || '').trim();
			return placeholder ? placeholder.slice(0, 300) : null;
		}
		if (tag === 'select') {
			const opt = el.options && el.options[el.selectedIndex];
			return opt ? (opt.text || '').trim().slice(0, 300) : null;
		}
		const text = (el.innerText || el.textContent || '').trim();
		return text ? text.slice(0, 300) : null;
	}

	function isVisible(el) {
		if (!el || el.nodeType !== Node.ELEMENT_NODE) { return false; }
		const style = window.getComputedStyle(el);
		if (!style) { return false; }
		if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') { return false; }
		const rect = el.getBoundingClientRect();
		return rect.width > 0 && rect.height > 0;
	}

	function isSkippable(el) {
		const tag = el.tagName ? el.tagName.toLowerCase() : '';
		return ['script', 'style', 'noscript', 'template', 'meta', 'link', 'head'].includes(tag);
	}

	function classifyElement(el) {
		const tag = el.tagName ? el.tagName.toLowerCase() : '';
		const role = (el.getAttribute('role') || '').toLowerCase();
		const type = (el.getAttribute('type') || '').toLowerCase();
		if (tag === 'button' || role === 'button' || type === 'button' || type === 'submit' || type === 'reset') { return 'button'; }
		if (tag === 'input') {
			if (type === 'checkbox') { return 'checkbox'; }
			if (type === 'radio') { return 'radio'; }
			if (type === 'hidden') { return 'hidden'; }
			return 'entry field';
		}
		if (tag === 'textarea' || el.isContentEditable) { return 'entry field'; }
		if (tag === 'select') { return 'dropdown'; }
		if (tag === 'a') { return 'link'; }
		if (tag === 'img') { return 'image'; }
		if (tag === 'label') { return 'label'; }
		if (getElementText(el)) { return 'text'; }
		return tag || 'element';
	}

	function normalizeLabel(value) {
		const text = (value || '').replace(/\s+/g, ' ').trim();
		return text || null;
	}

	function getVisibleHeadingText(selector) {
		for (const el of document.querySelectorAll(selector)) {
			if (!isVisible(el)) { continue; }
			const text = normalizeLabel(el.innerText || el.textContent || '');
			if (text) { return text; }
		}
		return null;
	}

	function toHandleLabel(rawValue) {
		const cleaned = normalizeLabel(rawValue);
		if (!cleaned) { return null; }
		return cleaned
			.split(/[-_]+/)
			.filter(Boolean)
			.map(part => part.charAt(0).toUpperCase() + part.slice(1))
			.join(' ');
	}

	function getUrlHandleLabel() {
		try {
			const currentUrl = new URL(window.location.href);
			const parts = currentUrl.pathname.split('/').map(part => part.trim()).filter(Boolean);
			if (parts.length) {
				const label = toHandleLabel(parts[parts.length - 1]);
				if (label) { return label; }
			}
			const host = (currentUrl.hostname || '').replace(/^www\./i, '');
			const hostBase = host.split('.')[0] || '';
			return toHandleLabel(hostBase);
		} catch (err) {
			return null;
		}
	}

	function getPageName() {
		const headerText = getVisibleHeadingText('main header h1, main header h2, header h1, header h2, .page-header h1, .page-header h2, .page-title, [role="main"] h1');
		if (headerText) { return headerText; }
		const h1Text = getVisibleHeadingText('h1');
		if (h1Text) { return h1Text; }
		const handleText = getUrlHandleLabel();
		if (handleText) { return handleText; }
		return normalizeLabel(document.title || '') || '';
	}

	/* Escape a raw attribute value for embedding inside a CSS [attr="..."] selector. */
	function cssVal(v) { return (v || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"'); }

	function buildLocators(el) {
		const tag = el.tagName ? el.tagName.toLowerCase() : null;
		const text = getElementText(el);
		const linkText = tag === 'a' && text ? text : null;
		const idValue = el.id ? el.id.trim() : '';
		const nameValue = (el.getAttribute('name') || '').trim();
		const ariaLabelValue = (el.getAttribute('aria-label') || '').trim();
		const dataTestIdValue = (el.getAttribute('data-testid') || '').trim();
		return {
			id: idValue || null,
			// Store name/ariaLabel/dataTestId as full CSS attribute selectors so that
			// both Selenium (By.CSS_SELECTOR) and Playwright (.locator()) can use them
			// without extra wrapping — matching exactly what the recorder stores.
			name: nameValue ? '[name="' + cssVal(nameValue) + '"]' : null,
			value: el.getAttribute('value') || null,
			placeholder: el.getAttribute('placeholder') || null,
			class: el.classList && el.classList.length ? el.classList[0] : null,
			className: el.classList && el.classList.length ? el.classList[0] : null,
			tagName: tag,
			css: getCssSelector(el),
			xpath: getXPath(el),
			href: el.getAttribute('href') || null,
			text: text,
			linkText: linkText,
			partialLinkText: linkText ? linkText.slice(0, 40) : null,
			type: el.getAttribute('type') || null,
			role: el.getAttribute('role') || null,
			title: el.getAttribute('title') || null,
			alt: el.getAttribute('alt') || null,
			ariaLabel: ariaLabelValue ? '[aria-label="' + cssVal(ariaLabelValue) + '"]' : null,
			dataTestId: dataTestIdValue ? '[data-testid="' + cssVal(dataTestIdValue) + '"]' : null
		};
	}

	function buildDescriptor(el) {
			const rect = el.getBoundingClientRect();
		return {
			element_type: classifyElement(el),
			tag_name: el.tagName ? el.tagName.toLowerCase() : null,
			text: getElementText(el),
				locators: buildLocators(el),
				bounds: {
					left: Math.max(0, rect.left),
					top: Math.max(0, rect.top),
					width: Math.max(0, rect.width),
					height: Math.max(0, rect.height),
				},
				viewport: {
					width: window.innerWidth || document.documentElement.clientWidth || 0,
					height: window.innerHeight || document.documentElement.clientHeight || 0,
					devicePixelRatio: window.devicePixelRatio || 1,
				}
		};
	}

	function scanPage() {
		const seen = new Set();
		const items = [];
		for (const el of document.querySelectorAll('body *')) {
			if (isSkippable(el) || !isVisible(el)) {
				continue;
			}
			const item = buildDescriptor(el);
			const sig = item.locators.xpath || item.locators.css || `${item.tag_name}:${item.text || ''}`;
			if (!sig || seen.has(sig)) {
				continue;
			}
			seen.add(sig);
			items.push(item);
		}
		return {
			page_url: window.location.href,
			page_name: getPageName(),
			captured_at: new Date().toISOString(),
			items: items
		};
	}

	window.__aiDatabankScraper = {
		queue: [],
		scanPage: scanPage,
		popSnapshots: function() {
			const out = this.queue.slice();
			this.queue = [];
			return out;
		}
	};

	function queueSnapshot() {
		try {
			window.__aiDatabankScraper.queue.push(scanPage());
		} catch (err) {
			console.error('ai_databank scan error', err);
		}
	}

	document.addEventListener('click', function() {
		window.setTimeout(queueSnapshot, 150);
	}, true);
	window.addEventListener('load', function() {
		window.setTimeout(queueSnapshot, 250);
	});

	queueSnapshot();
	return true;
})();
"""


def _normalize_strategy_locator(strategy: str, raw: Any) -> str:
		"""Normalise a raw locator value to the bare attribute value.

		The scraper JS now stores name/ariaLabel/dataTestId as full CSS attribute
		selectors (e.g. '[name="foo"]') so that they match what the recorder
		stores.  This function strips the wrapper to yield the bare value which
		is then stored in locator_property.ordered_locators[].locator; ATTR_WRAP
		re-produces the prepared CSS expression in prepared_locator.
		"""
		locator = str(raw or "").strip()
		if not locator:
				return ""
		if strategy == "id" and locator.startswith("#"):
				return locator[1:].strip()
		if strategy == "name":
				match = re.search(r'\[name="([^"]+)"\]', locator)
				if match:
						return match.group(1).strip()
		if strategy == "ariaLabel":
				match = re.search(r'\[aria-label="([^"]+)"\]', locator)
				if match:
						return match.group(1).strip()
		if strategy == "dataTestId":
				match = re.search(r'\[data-testid="([^"]+)"\]', locator)
				if match:
						return match.group(1).strip()
		return locator


def ordered_locators(locator_block: dict[str, Any]) -> list[dict[str, Any]]:
		results: list[dict[str, Any]] = []
		seen: set[tuple[str, str]] = set()

		for rank, strategy in enumerate(ORDERED_STRATEGIES, start=1):
				raw = locator_block.get(strategy)
				if raw is None:
						continue
				locator = _normalize_strategy_locator(strategy, raw)
				if not locator or (strategy, locator) in seen:
						continue
				seen.add((strategy, locator))
				prepared = ATTR_WRAP[strategy].format(value=locator) if strategy in ATTR_WRAP else locator
				results.append({
						"strategy": strategy,
						"locator": locator,
						"prepared_locator": prepared,
						"rank": rank,
						"by": STRATEGY_BY.get(strategy, "css selector"),
						"wrapped": strategy in ATTR_WRAP,
				})
		return results


def build_fingerprint(page_url: str, element_type: str, item: dict[str, Any], ordered: list[dict[str, Any]]) -> str:
		primary = ordered[0]["prepared_locator"] if ordered else ""
		raw = "|".join([
				page_url or "",
				element_type or "",
				item.get("tag_name") or "",
				(item.get("text") or "")[:300],
				primary,
		])
		return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()


def ensure_table(conn) -> None:
		with conn.cursor() as cur:
				cur.execute(
						"""
						CREATE TABLE IF NOT EXISTS ai_databank (
								id BIGSERIAL PRIMARY KEY,
								page_url TEXT NOT NULL,
								page_name TEXT NOT NULL DEFAULT '',
								element_type VARCHAR(80) NOT NULL DEFAULT 'element',
								element_fingerprint TEXT NOT NULL,
								locator_property JSONB NOT NULL DEFAULT '{}'::jsonb,
								screenshot_png BYTEA,
								created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
								updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
						)
						"""
				)
				cur.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS element_fingerprint TEXT")
				cur.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS screenshot_png BYTEA")
				cur.execute("ALTER TABLE ai_databank ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()")
				cur.execute("CREATE INDEX IF NOT EXISTS ai_databank_page_url_idx ON ai_databank (page_url)")
				cur.execute("CREATE INDEX IF NOT EXISTS ai_databank_created_at_idx ON ai_databank (created_at DESC)")
				cur.execute(
					"""
					CREATE UNIQUE INDEX IF NOT EXISTS ai_databank_page_fingerprint_uniq
					ON ai_databank (page_url, element_fingerprint)
					WHERE element_fingerprint IS NOT NULL AND element_fingerprint <> ''
					"""
				)
		conn.commit()


def _local_chromedriver_candidates(configured_filename: str = "") -> list[str]:
		candidates: list[str] = []
		if configured_filename:
				configured_path = os.path.join(_WEBDRIVERS_CHROME_DIR, configured_filename)
				if os.path.isfile(configured_path):
						candidates.append(configured_path)

		try:
				if os.path.isdir(_WEBDRIVERS_CHROME_DIR):
						for name in sorted(os.listdir(_WEBDRIVERS_CHROME_DIR), reverse=True):
								full_path = os.path.join(_WEBDRIVERS_CHROME_DIR, name)
								if os.path.isfile(full_path) and full_path not in candidates:
										candidates.append(full_path)
		except Exception:
				pass
		return candidates


def _local_edgedriver_candidates(configured_filename: str = "") -> list[str]:
		candidates: list[str] = []
		if configured_filename:
				configured_path = os.path.join(_WEBDRIVERS_EDGE_DIR, configured_filename)
				if os.path.isfile(configured_path):
						candidates.append(configured_path)

		try:
				if os.path.isdir(_WEBDRIVERS_EDGE_DIR):
						for name in sorted(os.listdir(_WEBDRIVERS_EDGE_DIR), reverse=True):
								full_path = os.path.join(_WEBDRIVERS_EDGE_DIR, name)
								if os.path.isfile(full_path) and full_path not in candidates:
										candidates.append(full_path)
		except Exception:
				pass
		return candidates


def _start_chrome_driver(options: Options, configured_filename: str = "") -> webdriver.Chrome:
		for driver_path in _local_chromedriver_candidates(configured_filename):
				try:
						return webdriver.Chrome(service=Service(driver_path), options=options)
				except WebDriverException:
						pass

		try:
				return webdriver.Chrome(options=options)
		except WebDriverException:
				pass

		try:
				return webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=options)
		except (RequestsConnectionError, Exception):
				pass

		try:
				return webdriver.Chrome(service=Service("chromedriver"), options=options)
		except WebDriverException as exc:
				raise WebDriverException(
						"Could not start ChromeDriver via local webdrivers/chrome, Selenium Manager, webdriver_manager, or PATH. "
						"Configure Chrome -> ChromeDriver Executable or place a matching driver in webdrivers/chrome/."
				) from exc


def _start_edge_driver(options: EdgeOptions, configured_filename: str = "") -> webdriver.Edge:
		for driver_path in _local_edgedriver_candidates(configured_filename):
				try:
						return webdriver.Edge(service=EdgeService(driver_path), options=options)
				except WebDriverException:
						pass

		try:
				return webdriver.Edge(options=options)
		except WebDriverException:
				pass

		try:
				return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=options)
		except (RequestsConnectionError, Exception):
				pass

		try:
				return webdriver.Edge(service=EdgeService("msedgedriver"), options=options)
		except WebDriverException as exc:
				raise WebDriverException(
						"Could not start EdgeDriver via local webdrivers/edge, Selenium Manager, webdriver_manager, or PATH. "
						"Configure Edge -> EdgeDriver Executable or place a matching driver in webdrivers/edge/."
				) from exc


def build_driver(attach_port: int = 0, configured_filename: str = "", browser_name: str = "chrome"):
		browser = (browser_name or "chrome").strip().lower()
		if browser == "msedge":
				options = EdgeOptions()
				options.add_argument("--start-maximized")
				options.add_argument("--disable-blink-features=AutomationControlled")
				if attach_port:
						options.add_experimental_option("debuggerAddress", f"127.0.0.1:{attach_port}")
				return _start_edge_driver(options, configured_filename=configured_filename)

		options = Options()
		options.add_argument("--start-maximized")
		options.add_argument("--disable-blink-features=AutomationControlled")
		if attach_port:
				options.add_experimental_option("debuggerAddress", f"127.0.0.1:{attach_port}")
		return _start_chrome_driver(options, configured_filename=configured_filename)


class AIDatabankScraper:
		def __init__(self, driver: webdriver.Chrome, conn, poll_interval: float = 1.0):
				self.driver = driver
				self.conn = conn
				self.poll_interval = poll_interval
				self.saved_rows = 0

		def ensure_runtime(self) -> None:
				self.driver.execute_script(SCRAPER_JS)

		def open_url(self, url: str) -> None:
				if url:
						self.driver.get(url)

		def pull_snapshots(self) -> list[dict[str, Any]]:
				try:
						return self.driver.execute_script(
								"return window.__aiDatabankScraper ? window.__aiDatabankScraper.popSnapshots() : [];"
						) or []
				except JavascriptException:
						self.ensure_runtime()
						return []

		def save_snapshot(
			self,
			snapshot: dict[str, Any],
			on_progress=None,   # callable(saved: int, total: int) — called per upsert
			stop_event=None,    # threading.Event — set to abort
			pause_event=None,   # threading.Event — clear to pause, set to resume
			item_delay_seconds: float = 0.0,
		) -> int:
				page_url = snapshot.get("page_url") or self.driver.current_url
				page_name = snapshot.get("page_name") or self.driver.title or ""
				items = snapshot.get("items") or []
				if not items:
						return 0

				# Take ONE screenshot for the whole page scan.
				# Do NOT embed the same binary in every element row — on large pages
				# (500+ elements) that would mean sending hundreds of MB to the DB,
				# causing the scraper to hang.  Store it only on the first row.
				try:
						screenshot_png = self.driver.get_screenshot_as_png()
				except Exception:
						screenshot_png = None
				screenshot_bin = psycopg2.Binary(screenshot_png) if screenshot_png else None

				# ── Phase 1: build item data (CPU only, no DB) ────────────────────────────
				current_fingerprints: list[str] = []
				item_data: list[tuple] = []
				for item in items:
						locator_block = item.get("locators") or {}
						ordered = ordered_locators(locator_block)
						element_type = item.get("element_type") or "element"
						fingerprint = build_fingerprint(page_url, element_type, item, ordered)
						locator_property = {
								"tag_name": item.get("tag_name"),
								"text": item.get("text"),
								"bounds": item.get("bounds") or {},
								"viewport": item.get("viewport") or {},
								"locators": locator_block,
								"ordered_locators": ordered,
								"primary": ordered[0] if ordered else None,
								"fallback_order": list(ORDERED_STRATEGIES),
								"mapping": STRATEGY_BY,
						}
						if fingerprint:
								current_fingerprints.append(fingerprint)
						item_data.append((page_url, page_name, element_type, fingerprint, locator_property))

				total = len(item_data)

				# ── Phase 2: delete stale rows (short transaction) ────────────────────
				if current_fingerprints:
						with self.conn.cursor() as cur:
								cur.execute(
										"""
										DELETE FROM ai_databank
										WHERE (page_url = %s OR page_name = %s)
										  AND (element_fingerprint IS NULL
										       OR element_fingerprint = ''
										       OR element_fingerprint <> ALL(%s))
										""",
										[page_url, page_name, current_fingerprints],
								)
						self.conn.commit()

				# ── Phase 3: upsert elements in small committed batches ─────────────────
				# Committing every BATCH_SIZE rows keeps individual transactions
				# small and lets stop/pause fire promptly between commits.
				BATCH_SIZE = 50
				saved = 0
				pending = 0

				with self.conn.cursor() as cur:
						for idx_, (page_url_, page_name_, element_type_, fingerprint_, locator_property_) in enumerate(item_data):

								# ── Stop check ────────────────────────────────────────────────────────
								if stop_event and stop_event.is_set():
										break

								# ── Pause check — commit first, then wait outside transaction
								if pause_event and not pause_event.is_set():
										# Flush pending work so we don't hold an open transaction
										if pending > 0:
												self.conn.commit()
												pending = 0
										# Block until resumed (or stopped), checking every 200 ms
										while not pause_event.wait(timeout=0.2):
												if stop_event and stop_event.is_set():
														break
										if stop_event and stop_event.is_set():
												break

								# Only attach the screenshot to the very first element; all others
								# get NULL (the ON CONFLICT DO UPDATE preserves the existing screenshot).
								row_screenshot = screenshot_bin if idx_ == 0 else None

								cur.execute(
										"""
										INSERT INTO ai_databank (
												page_url, page_name, element_type, element_fingerprint,
												locator_property, screenshot_png, updated_at
										)
										VALUES (%s, %s, %s, %s, %s, %s, NOW())
										ON CONFLICT (page_url, element_fingerprint)
										WHERE element_fingerprint IS NOT NULL AND element_fingerprint <> ''
										DO UPDATE SET
												page_name      = EXCLUDED.page_name,
												element_type   = EXCLUDED.element_type,
												locator_property = EXCLUDED.locator_property,
												screenshot_png = COALESCE(EXCLUDED.screenshot_png, ai_databank.screenshot_png),
												updated_at     = NOW()
										""",
										[
												page_url_,
												page_name_,
												element_type_,
												fingerprint_,
												Json(locator_property_),
												row_screenshot,
										],
								)
								pending += 1
								saved += 1

								if on_progress:
										on_progress(saved, total)

								# Commit in batches — keeps transactions small and DB responsive
								if pending >= BATCH_SIZE:
										self.conn.commit()
										pending = 0

								# ── Optional per-item delay (with stop/pause checks) ────
								if item_delay_seconds > 0:
										deadline = time.time() + item_delay_seconds
										while time.time() < deadline:
												if stop_event and stop_event.is_set():
														break
												if pause_event and not pause_event.wait(timeout=0.05):
														continue
												time.sleep(min(0.05, max(0.0, deadline - time.time())))
										if stop_event and stop_event.is_set():
												break

				# Commit any remaining rows
				if pending > 0:
						self.conn.commit()

				self.saved_rows += saved
				return saved

		def run(self) -> None:
				print("AI Databank scraper is running.")
				print("Click anywhere in the browser. Each click refreshes unique page objects in ai_databank.")
				print("Press Ctrl+C to stop.\n")
				while True:
						try:
								self.ensure_runtime()
								for snapshot in self.pull_snapshots():
										count = self.save_snapshot(snapshot)
										if count:
												print(f"[{time.strftime('%H:%M:%S')}] Upserted {count} objects from {snapshot.get('page_name') or self.driver.title}")
								time.sleep(self.poll_interval)
						except KeyboardInterrupt:
								raise
						except WebDriverException as exc:
								print(f"WebDriver error: {exc}", file=sys.stderr)
								time.sleep(self.poll_interval)


def scrape_once(
		url: str = "",
		attach_port: int = 0,
		db_config: dict[str, Any] | None = None,
		webdriver_filename: str = "",
		browser_name: str = "chrome",
		settle_seconds: float = 0.75,
		item_delay_seconds: float = 0.0,
		on_progress=None,   # callable(phase: str, count: int, total: int)
		stop_event=None,    # threading.Event — set to abort
		pause_event=None,   # threading.Event — clear to pause, set to resume
) -> dict[str, Any]:
		conn = psycopg2.connect(**(db_config or DB_CONFIG))
		driver = build_driver(attach_port, configured_filename=webdriver_filename, browser_name=browser_name)
		scraper = AIDatabankScraper(driver, conn, poll_interval=0.25)

		try:
				if on_progress:
						on_progress("scanning", 0, 0)
				# Attach mode scrapes whatever page is already open.
				# Launch mode opens a fresh browser and navigates to the requested URL first.
				if not attach_port and url:
						scraper.open_url(url)
				scraper.ensure_runtime()
				if settle_seconds > 0:
						time.sleep(settle_seconds)
				snapshot = driver.execute_script(
						"return window.__aiDatabankScraper ? window.__aiDatabankScraper.scanPage() : null;"
				)
				if not snapshot:
						raise RuntimeError("Unable to scan the current page.")
				ensure_table(conn)
				total_items = len(snapshot.get("items") or [])
				if on_progress:
						on_progress("saving", 0, total_items)

				def _prog(count, total):
						if on_progress:
								on_progress("saving", count, total)

				saved_rows = scraper.save_snapshot(
						snapshot,
						on_progress=_prog,
						stop_event=stop_event,
						pause_event=pause_event,
						item_delay_seconds=item_delay_seconds,
				)
				return {
						"saved_rows": saved_rows,
						"page_url": snapshot.get("page_url") or driver.current_url,
						"page_name": snapshot.get("page_name") or driver.title or "",
				}
		finally:
				# When attaching to an existing Chrome session via debuggerAddress,
				# do NOT quit() — that would close the user's browser. Just disconnect.
				if not attach_port:
						try:
								driver.quit()
						except Exception:
								pass
				conn.close()


def parse_args() -> argparse.Namespace:
		parser = argparse.ArgumentParser(description="Click-driven Selenium scraper for ai_databank")
		parser.add_argument("--url", default="https://example.com", help="URL to open when launching Chrome.")
		parser.add_argument("--attach-port", type=int, default=0, help="Attach to an existing Chrome instance using its remote debugging port.")
		parser.add_argument("--poll-interval", type=float, default=1.0, help="Seconds between browser polling cycles.")
		return parser.parse_args()


def main() -> int:
		args = parse_args()
		conn = psycopg2.connect(**DB_CONFIG)
		ensure_table(conn)
		driver = build_driver(args.attach_port)
		scraper = AIDatabankScraper(driver, conn, poll_interval=max(args.poll_interval, 0.25))
		try:
				if not args.attach_port:
						scraper.open_url(args.url)
				scraper.ensure_runtime()
				scraper.run()
		except KeyboardInterrupt:
				print("\nStopping scraper...")
		finally:
				try:
						driver.quit()
				except Exception:
						pass
				conn.close()
				print(f"Saved rows total: {scraper.saved_rows}")
		return 0


if __name__ == "__main__":
		raise SystemExit(main())
