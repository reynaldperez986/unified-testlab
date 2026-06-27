"""
Playwright-based replay engine.

Drop-in alternative to the Selenium replay in replay.py.
Shares the same function signature (replay_session) and writes results to the
same run_table (RunResult model).
"""

import os
import time
import threading
import logging
import logging.handlers
from typing import Any, Callable

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PwTimeout, expect
    _PW_AVAILABLE = True
except ImportError:
    _PW_AVAILABLE = False
    sync_playwright = None  # type: ignore[assignment]
    PwTimeout = TimeoutError  # type: ignore[misc,assignment]
    expect = None  # type: ignore[assignment]

from django.db import connection as _db_connection

_BASE_DIR = os.path.dirname(os.path.dirname(__file__))
_SCREENSHOTS_DIR = os.path.join(_BASE_DIR, "logs", "screenshots")
_LOG_DIR = os.path.join(_BASE_DIR, "logs")

os.makedirs(_LOG_DIR, exist_ok=True)
_pw_logger = logging.getLogger("playwright_replay")
_pw_logger.setLevel(logging.DEBUG)
if not _pw_logger.handlers:
    _fh = logging.handlers.RotatingFileHandler(
        os.path.join(_LOG_DIR, "playwright_replay.log"),
        maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
    )
    _fh.setFormatter(logging.Formatter("%(asctime)s  %(levelname)s  %(message)s"))
    _pw_logger.addHandler(_fh)


# ---------------------------------------------------------------------------
# Config helper — reads from app_config table
# ---------------------------------------------------------------------------

def _get_config(key: str, default: str = "") -> str:
    """Read a single value from app_config; returns *default* on any error."""
    try:
        with _db_connection.cursor() as cur:
            cur.execute("SELECT value FROM app_config WHERE key = %s", [key])
            row = cur.fetchone()
            return row[0] if row else default
    except Exception:
        return default


def _get_config_group(prefix: str) -> dict[str, str]:
    """Read all app_config values that start with *prefix*."""
    try:
        with _db_connection.cursor() as cur:
            cur.execute(
                "SELECT key, value FROM app_config WHERE key LIKE %s",
                [f"{prefix}%"],
            )
            return {key: value for key, value in cur.fetchall()}
    except Exception:
        return {}


def _load_replay_config() -> dict:
    """Load replay timeout settings from app_config."""
    cfg = {}
    _keys = {
        "replay.page_timeout":    ("page_timeout",    30),
        "replay.overlay_timeout":  ("overlay_timeout", 60),
        "replay.step_timeout":    ("step_timeout",    10),
        "replay.poll_interval":   ("poll_interval",    0.5),
        "replay.step_retries":    ("step_retries",     2),
        "replay.retry_delay":     ("retry_delay",      5),
        "replay.step_delay":      ("step_delay",       0),
        "replay.step_settle":     ("step_settle",      0.3),
        "replay.window_timeout":  ("window_timeout",  15),
        "replay.nav_retries":     ("nav_retries",      2),
        "replay.nav_retry_wait":  ("nav_retry_wait",   3),
        "replay.max_step_delay":  ("max_step_delay",   10.0),
    }
    for config_key, (name, default) in _keys.items():
        raw = _get_config(config_key, "")
        if raw:
            try:
                cfg[name] = float(raw) if "." in raw else int(raw)
            except (ValueError, TypeError):
                cfg[name] = default
        else:
            cfg[name] = default
    return cfg


def _safe_page_goto(
    page,
    url: str,
    dom_timeout_ms: int,
    page_load_timeout_ms: int,
    retries: int,
    retry_wait_s: float,
) -> None:
    """Navigate with retry for transient page-load timing issues."""
    last_exc: Exception | None = None
    for attempt in range(retries + 1):
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=dom_timeout_ms)
            page.wait_for_load_state("domcontentloaded", timeout=dom_timeout_ms)
            if page_load_timeout_ms > 0:
                page.wait_for_load_state("load", timeout=page_load_timeout_ms)
            _wait_for_page_ready(page, max(page_load_timeout_ms, dom_timeout_ms))
            return
        except Exception as exc:
            last_exc = exc
            if attempt < retries:
                time.sleep(retry_wait_s)
                continue
    if last_exc:
        raise last_exc


def _wait_for_expected_page(page, expected_url: str, timeout_ms: int, poll_interval_s: float) -> bool:
    """Give the app time to complete an in-flight navigation before forcing a goto."""
    expected = _url_base_path(expected_url)
    if not expected:
        return False

    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    while time.monotonic() < deadline:
        try:
            current = _url_base_path(page.url)
            if current == expected:
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=max(int(poll_interval_s * 1000), 50))
                except Exception:
                    pass
                return True
        except Exception:
            pass
        time.sleep(max(poll_interval_s, 0.05))

    return False


def _should_skip_stale_postback_step(raw_event: dict, step, next_step, current_url: str) -> bool:
    """Return True when the browser already advanced past a stale autosubmit step.

    Some JSF controls emit duplicate change events while the final selection triggers
    an autosubmit navigation. If replay is already on the next recorded page, forcing
    a goto back to the stale page can land on a blank JSF endpoint.
    """
    if next_step is None:
        return False

    current = _url_base_path(current_url)
    expected = _url_base_path(getattr(step, "page_url", "") or "")
    next_expected = _url_base_path(getattr(next_step, "page_url", "") or "")
    if not current or not expected or not next_expected:
        return False
    if current != next_expected or expected == next_expected:
        return False

    action = str(raw_event.get("action") or getattr(step, "action", "") or "").strip().lower()
    if action not in ("change", "input", "click"):
        return False

    tag = str(raw_event.get("tag") or getattr(step, "element_tag", "") or "").strip().lower()
    pw_info = raw_event.get("playwright_info") if isinstance(raw_event.get("playwright_info"), dict) else {}
    sel_info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = {}
    for info in (pw_info, sel_info):
        if isinstance(info.get("attributes"), dict):
            attrs.update(info["attributes"])

    autosubmit = str(attrs.get("data-autosubmit") or "").strip().lower() in {"true", "1", "yes", "on"}
    return autosubmit or tag == "select"


def _load_playwright_config() -> dict:
    """Load Playwright-specific launch/context/page settings from app_config."""
    _raw_cfg = _get_config_group("playwright.")

    def _str(key, default=""):
        raw = _raw_cfg.get(key)
        return default if raw is None else raw

    def _int(key, default=0, allow_blank=False):
        raw = _raw_cfg.get(key)
        if raw is None:
            return default
        if raw == "":
            return 0 if allow_blank else default
        try:
            return int(raw)
        except (ValueError, TypeError):
            return default

    def _bool(key, default=False):
        raw = (_raw_cfg.get(key) or "").lower()
        if key not in _raw_cfg:
            return default
        if raw in ("true", "1", "yes"):
            return True
        if raw in ("false", "0", "no"):
            return False
        if raw == "":
            return default
        return default

    return {
        # Browser launch
        "slow_mo":          _int("playwright.slow_mo", 0),
        "devtools":         _bool("playwright.devtools", False),
        "extra_args":       _str("playwright.extra_args", "--start-maximized"),
        # Browser context
        "viewport_width":   _int("playwright.viewport_width", 1280, allow_blank=True),
        "viewport_height":  _int("playwright.viewport_height", 720, allow_blank=True),
        "user_agent":       _str("playwright.user_agent"),
        "locale":           _str("playwright.locale", "en-US"),
        "timezone_id":      _str("playwright.timezone_id"),
        "geo_latitude":     _str("playwright.geolocation_latitude"),
        "geo_longitude":    _str("playwright.geolocation_longitude"),
        "permissions":      _str("playwright.permissions"),
        "record_video":     _bool("playwright.record_video", False),
        "record_video_dir": _str("playwright.record_video_dir", "logs/videos"),
        "accept_downloads": _bool("playwright.accept_downloads", True),
        # Page defaults
        "default_timeout":            _int("playwright.default_timeout", 30000),
        "default_navigation_timeout": _int("playwright.default_navigation_timeout", 60000),
        # Proxy
        "proxy_server":     _str("playwright.proxy_server"),
        "proxy_username":   _str("playwright.proxy_username"),
        "proxy_password":   _str("playwright.proxy_password"),
        # Persistent context
        "user_data_dir":    _str("playwright.user_data_dir"),
    }


def _parse_playwright_extra_args(extra_args_text: str) -> tuple[list[str], bool | None]:
    """Split raw extra args into Chromium CLI args plus optional launch overrides."""
    chromium_args: list[str] = []
    headless_override: bool | None = None

    for raw_line in (extra_args_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lower_line = line.lower()
        if lower_line in ("headless=false", "headless = false"):
            headless_override = False
            continue
        if lower_line in ("headless=true", "headless = true"):
            headless_override = True
            continue
        chromium_args.append(line)

    return chromium_args, headless_override


# ---------------------------------------------------------------------------
# Strategy-to-Playwright locator mapping
# ---------------------------------------------------------------------------

def _looks_like_full_selector(value: str) -> bool:
    """Return True if *value* is already a complete CSS selector or XPath expression
    rather than a bare attribute value (e.g. 'input[name="foo"]' vs 'foo')."""
    v = value.strip()
    if v.startswith(("//", "./", "(//")):   # XPath
        return True
    # CSS with structural characters: tag[attr], [attr=val], .class, #id, descendant >
    import re as _re
    return bool(_re.search(r'\[|>|~|\+|^#|^\.\w', v))


def _build_locator(page, strategy: str, value: str):
    """Return a Playwright Locator for a given strategy + value."""
    # If the stored value is already a full CSS or XPath expression, use it directly.
    # This happens when the recorder stored e.g. 'input[name="username"]' under the
    # 'name' strategy instead of just the bare attribute value 'username'.
    if _looks_like_full_selector(value) and strategy not in ("xpath", "css", "text", "linkText", "partialLinkText", "role", "tagName"):
        if value.strip().startswith(("//", "./", "(//")):
            return page.locator(f"xpath={value.strip()}")
        return page.locator(value.strip())

    if strategy == "xpath":
        return page.locator(f"xpath={value}")
    elif strategy == "id":
        val = value.lstrip("#")
        return page.locator(f"#{val}")
    elif strategy == "css":
        return page.locator(value)
    elif strategy == "name":
        return page.locator(f"[name='{value}']")
    elif strategy == "dataTestId":
        return page.get_by_test_id(value)
    elif strategy == "role":
        return page.get_by_role(value)
    elif strategy == "ariaLabel":
        return page.get_by_label(value)
    elif strategy == "label":
        label_for = str(value or "").strip()
        if label_for.lower().startswith("for="):
            label_for = label_for[4:].strip()
            if label_for:
                return page.locator(f'label[for="{label_for}"]')
        return page.get_by_label(value, exact=True)
    elif strategy == "placeholder":
        return page.get_by_placeholder(value)
    elif strategy == "text":
        return page.get_by_text(value, exact=True)
    elif strategy == "linkText":
        return page.get_by_role("link", name=value)
    elif strategy == "partialLinkText":
        return page.get_by_text(value)
    elif strategy == "title":
        return page.locator(f"[title='{value}']")
    elif strategy == "alt":
        return page.get_by_alt_text(value)
    elif strategy == "value":
        return page.locator(f"[value='{value}']")
    elif strategy in ("class", "className"):
        cls = value.split()[0] if value else value
        return page.locator(f".{cls}")
    elif strategy == "tagName":
        return page.locator(value.lower())
    elif strategy == "href":
        return page.locator(f"[href='{value}']")
    elif strategy == "type":
        return page.locator(f"[type='{value}']")
    else:
        return page.locator(f"css={value}")


# ---------------------------------------------------------------------------
# Element finder with fallback (mirrors Selenium _find_element logic)
# ---------------------------------------------------------------------------

_ORDERED_STRATS = (
    "xpath", "id", "name", "value", "placeholder", "class", "className",
    "tagName", "css", "href", "text", "label", "linkText", "partialLinkText",
    "type", "role", "title", "alt", "ariaLabel", "dataTestId",
)

_BLOCKING_OVERLAY_SELECTOR = ",".join([
    ".ui-blockui",
    ".blockUI",
    ".ui-widget-overlay",
    ".ui-dialog-mask",
    ".loading-mask",
    ".loading-overlay",
    ".spinner-overlay",
    ".progress-overlay",
    ".rf-pp-shade",
    ".rf-pp-shdw",
    "[aria-busy='true']",
    "[data-loading='true']",
    "[data-testid='loading']",
    "[id*='BlockUI']",
    "[id*='blockUI']",
    "[id*='Progress']",
    "[id*='progress']",
    "[id*='WaitDialog']",
    "[id*='waitDialog']",
    "[id*='StatusDialog']",
    "[id*='statusDialog']",
    # JSF TIPlus in-progress page overlay
    "#inProgressPage",
    "[id='inProgressPage']",
    "[id*='inProgress']",
    "[class*='blockui']",
    "[class*='block-ui']",
    "[class*='loading-overlay']",
    "[class*='spinner-overlay']",
    "[class*='progress-overlay']",
])

_DIALOG_CONTAINER_SELECTORS = (
    "[role='dialog']",
    ".ui-dialog",
    ".modal",
    ".modal-dialog",
    "[class*='dialog']",
    "[id*='MessageBox']",
    "[id*='messagebox']",
)


def _semantic_locators(page, raw_event: dict) -> list[tuple[str, Any]]:
    """Build semantic fallback locators from the recorded event payload."""
    candidates: list[tuple[str, Any]] = []
    seen: set[str] = set()

    def _add(label: str, locator) -> None:
        if label in seen:
            return
        seen.add(label)
        candidates.append((label, locator))

    tag = str(raw_event.get("tag") or "").strip().lower()
    text = str(raw_event.get("text") or "").strip()
    value = str(raw_event.get("value") or "").strip()
    input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").strip().lower()
    element_id = str(raw_event.get("id") or "").strip()
    name = str(raw_event.get("name") or "").strip()
    title = str(raw_event.get("title") or raw_event.get("pageTitle") or "").strip()
    pw_info = raw_event.get("playwright_info") if isinstance(raw_event.get("playwright_info"), dict) else {}
    attrs = pw_info.get("attributes") if isinstance(pw_info.get("attributes"), dict) else {}
    locators = raw_event.get("locators") if isinstance(raw_event.get("locators"), dict) else {}
    accessible_name = str(pw_info.get("accessibleName") or pw_info.get("labelText") or locators.get("label") or attrs.get("aria-label") or "").strip()
    form_id = element_id.split(":", 1)[0].strip() if ":" in element_id else ""
    message_box_prefix = element_id.rsplit("_", 1)[0].strip() if "_" in element_id else ""

    if tag == "button" and text:
        _add(f"role:button:{text}", page.get_by_role("button", name=text, exact=True))
    if tag in ("input", "textarea") and input_type not in ("checkbox", "radio", "submit", "button", "reset", "hidden"):
        if accessible_name:
            _add(f"role:textbox:{accessible_name}", page.get_by_role("textbox", name=accessible_name, exact=True))
            _add(f"label:{accessible_name}", page.get_by_label(accessible_name, exact=True))
    if tag == "select" and accessible_name:
        _add(f"role:combobox:{accessible_name}", page.get_by_role("combobox", name=accessible_name, exact=True))
        _add(f"label:combobox:{accessible_name}", page.get_by_label(accessible_name, exact=True))
    if input_type == "checkbox" and accessible_name:
        _add(f"role:checkbox:{accessible_name}", page.get_by_role("checkbox", name=accessible_name, exact=True))
    if input_type == "radio" and accessible_name:
        _add(f"role:radio:{accessible_name}", page.get_by_role("radio", name=accessible_name, exact=True))
    if text:
        _add(f"text:{text}", page.get_by_text(text, exact=True))
        role_hint = str(attrs.get("role") or "").strip().lower()
        if role_hint in {"cell", "gridcell"}:
            _add(f"role:cell:{text}", page.get_by_role("cell", name=text, exact=True))
    if input_type in ("submit", "button", "reset") and value:
        _add(
            f"input:{input_type}:{value}",
            page.locator(f'input[type="{input_type}"][value="{value}"]'),
        )
    if element_id:
        _add(f"raw-id:{element_id}", page.locator(f'#{element_id.replace(":", "\\:")}'))
        _add(f"attr-id:{element_id}", page.locator(f'[id="{element_id}"]'))
    if name and tag:
        _add(f"tag-name:{tag}:{name}", page.locator(f'{tag}[name="{name}"]'))
    if name:
        _add(f"attr-name:{name}", page.locator(f'[name="{name}"]'))
    if form_id and text:
        form = page.locator(f'form[id="{form_id}"]')
        _add(f"form-role:{form_id}:{text}", form.get_by_role("button", name=text, exact=True))
        if tag == "button":
            _add(f"form-button-text:{form_id}:{text}", form.locator(f'button:has-text("{text}")'))
    if form_id and element_id:
        form = page.locator(f'form[id="{form_id}"]')
        _add(f"form-button-id:{form_id}:{element_id}", form.locator(f'button[id="{element_id}"]'))
    if form_id and name:
        form = page.locator(f'form[id="{form_id}"]')
        _add(f"form-name:{form_id}:{name}", form.locator(f'[name="{name}"]'))
    if message_box_prefix:
        dialog = page.locator(f'[id^="{message_box_prefix}"]')
        if text:
            _add(f"msgbox-role:{message_box_prefix}:{text}", dialog.get_by_role("button", name=text, exact=True))
            _add(f"msgbox-button-text:{message_box_prefix}:{text}", dialog.locator(f'button:has-text("{text}")'))
        if element_id:
            _add(f"msgbox-button-id:{message_box_prefix}:{element_id}", dialog.locator(f'button[id="{element_id}"]'))
        if name:
            _add(f"msgbox-name:{message_box_prefix}:{name}", dialog.locator(f'[name="{name}"]'))
    if title and text and tag == "button":
        for container_selector in _DIALOG_CONTAINER_SELECTORS:
            container = page.locator(container_selector).filter(has=page.get_by_text(title))
            _add(
                f"dialog-role:{container_selector}:{title}:{text}",
                container.get_by_role("button", name=text, exact=True),
            )
            _add(
                f"dialog-text:{container_selector}:{title}:{text}",
                container.locator(f'button:has-text("{text}")'),
            )
    if title and value and input_type in ("submit", "button", "reset"):
        for container_selector in _DIALOG_CONTAINER_SELECTORS:
            container = page.locator(container_selector).filter(has=page.get_by_text(title))
            _add(
                f"dialog-input:{container_selector}:{title}:{value}",
                container.locator(f'input[type="{input_type}"][value="{value}"]'),
            )

    return candidates


def _toggle_label_candidates(raw_event: dict) -> list[str]:
    """Build likely visible label texts for checkbox/radio controls."""
    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        value = " ".join(str(value or "").split()).strip()
        if not value or value in seen:
            return
        seen.add(value)
        candidates.append(value)

        words = value.split()
        if len(words) % 2 == 0:
            half = len(words) // 2
            left = " ".join(words[:half]).strip()
            right = " ".join(words[half:]).strip()
            if left and left == right and left not in seen:
                seen.add(left)
                candidates.append(left)

    pw_info = raw_event.get("playwright_info") if isinstance(raw_event.get("playwright_info"), dict) else {}
    sel_info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = {}
    for info in (pw_info, sel_info):
        if isinstance(info.get("attributes"), dict):
            attrs.update(info["attributes"])

    _add(pw_info.get("accessibleName") or "")
    _add(pw_info.get("labelText") or "")
    _add(sel_info.get("accessibleName") or "")
    _add(sel_info.get("labelText") or "")
    _add((raw_event.get("locators") or {}).get("label") or "")
    _add(attrs.get("aria-label") or "")
    return candidates


def _toggle_target_id(raw_event: dict) -> str:
    locators = raw_event.get("locators") if isinstance(raw_event.get("locators"), dict) else {}
    label_locator = str(locators.get("label") or "").strip()
    if label_locator.lower().startswith("for="):
        return label_locator[4:].strip()
    pw_info = raw_event.get("playwright_info") if isinstance(raw_event.get("playwright_info"), dict) else {}
    sel_info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = {}
    for info in (pw_info, sel_info):
        if isinstance(info.get("attributes"), dict):
            attrs.update(info["attributes"])
    return str(raw_event.get("id") or pw_info.get("id") or sel_info.get("id") or attrs.get("id") or "").strip()


def _toggle_autosubmit_enabled(raw_event: dict) -> bool:
    pw_info = raw_event.get("playwright_info") if isinstance(raw_event.get("playwright_info"), dict) else {}
    sel_info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = {}
    for info in (pw_info, sel_info):
        if isinstance(info.get("attributes"), dict):
            attrs.update(info["attributes"])
    return str(attrs.get("data-autosubmit") or "").strip().lower() in {"true", "1", "yes", "on"}


def _select_autosubmit_enabled(raw_event: dict) -> bool:
    return _toggle_autosubmit_enabled(raw_event)


def _prefer_label_toggle_click(raw_event: dict) -> bool:
    if str(raw_event.get("_used_strategy") or "").strip().lower() == "label":
        return True
    for info_name in ("playwright_info", "selenium_info"):
        info = raw_event.get(info_name) if isinstance(raw_event.get(info_name), dict) else {}
        state = info.get("state") if isinstance(info.get("state"), dict) else {}
        if state.get("visible") is False:
            return True
    return False


def _wait_for_locator_state(loc, state: str, timeout_ms: int, poll_interval_s: float) -> None:
    """Wait for a locator using the configured poll interval."""
    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    poll_ms = max(int(poll_interval_s * 1000), 50)
    last_exc: Exception | None = None
    while True:
        remaining_ms = int((deadline - time.monotonic()) * 1000)
        if remaining_ms <= 0:
            break
        try:
            loc.wait_for(state=state, timeout=min(poll_ms, remaining_ms))
            return
        except (PwTimeout, Exception) as exc:
            last_exc = exc
    if last_exc:
        raise last_exc
    raise PwTimeout(f"Timed out waiting for locator state={state!r}")


def _find_element(page, raw_event: dict, timeout: int = 10000, poll_interval_s: float = 0.5):
    """
    Try locators in DB rank order, mirroring Selenium replay behaviour:
      Pass 1  – primary (rank 1)      with full timeout
      Pass 2  – fallbacks (rank 2+)   with half timeout  (~5 s)
      Pass 3  – semantic fallbacks    with half timeout
      Pass 4  – presence-only checks  with short timeout (~3 s)
    Returns (locator, strategy_used, rank_used).
    """
    locators:    dict       = raw_event.get("locators") or {}
    db_locators: list[dict] = raw_event.get("_db_locators") or []
    record_id = raw_event.get("_record_id", "?")
    step_no   = raw_event.get("_step_no",   "?")

    # Build ordered list: DB-ranked first (is_primary leads), then _ORDERED_STRATS as safety net
    if db_locators:
        ordered = [
            (e["strategy"], e["locator"], e["rank"], e.get("is_primary", False))
            for e in db_locators
            if e.get("strategy") and e.get("locator")
        ]
    else:
        ordered = [
            (strategy, locators[strategy], rank, rank == 1)
            for rank, strategy in enumerate(_ORDERED_STRATS, start=1)
            if locators.get(strategy)
        ]

    fallback_timeout = max(timeout // 2, 3000)   # ~5 s when step_timeout = 10 s
    short_timeout    = max(timeout // 3, 2000)   # ~3 s for presence-only

    # ── Pass 1: primary (is_primary=True or first entry) — full step timeout ─
    if ordered:
        p_strat, p_val, p_rank, p_primary = ordered[0]
        loc = _build_locator(page, p_strat, p_val)
        try:
            _wait_for_locator_state(loc, "visible", timeout, poll_interval_s)
            raw_event["_used_strategy"] = p_strat
            raw_event["_used_locator"]  = p_val
            raw_event["_is_primary"]    = p_primary
            raw_event["_used_rank"]     = p_rank
            _store_rect(page, loc, raw_event)
            return loc, p_strat, p_rank
        except (PwTimeout, Exception):
            _pw_logger.warning(
                "primary-failed  strategy=%r  locator=%r  is_primary=%s  record_id=%s  step=%s",
                p_strat, p_val, p_primary, record_id, step_no,
            )
            print(
                f"[playwright_replay] Primary locator failed "
                f"(strategy={p_strat!r} is_primary={p_primary}); trying fallbacks.",
                flush=True,
            )

    # ── Pass 2: fallback locators with half timeout ────────────────────────────
    for strategy, loc_val, rank, _is_p in ordered[1:]:
        loc = _build_locator(page, strategy, loc_val)
        try:
            _wait_for_locator_state(loc, "visible", fallback_timeout, poll_interval_s)
            raw_event["_used_strategy"] = strategy
            raw_event["_used_locator"]  = loc_val
            raw_event["_is_primary"]    = _is_p
            raw_event["_used_rank"]     = rank
            _store_rect(page, loc, raw_event)
            print(
                f"[playwright_replay] Fallback succeeded; rank={rank} strategy={strategy!r}",
                flush=True,
            )
            _pw_logger.info(
                "fallback-success  rank=%s  strategy=%r  record_id=%s  step=%s",
                rank, strategy, record_id, step_no,
            )
            return loc, strategy, rank
        except (PwTimeout, Exception):
            _pw_logger.debug(
                "fallback-miss  rank=%s  strategy=%r  locator=%r  record_id=%s  step=%s",
                rank, strategy, loc_val, record_id, step_no,
            )
            continue

    # ── Pass 3: semantic fallbacks (text / role / label heuristics) ──────────
    for label, loc in _semantic_locators(page, raw_event):
        try:
            _wait_for_locator_state(loc, "visible", fallback_timeout, poll_interval_s)
            raw_event["_used_strategy"] = label
            raw_event["_used_locator"]  = label
            raw_event["_is_primary"]    = False
            raw_event["_used_rank"]     = 0
            _store_rect(page, loc, raw_event)
            print(
                f"[playwright_replay] Semantic fallback succeeded; strategy={label!r}",
                flush=True,
            )
            return loc, label, 0
        except (PwTimeout, Exception):
            _pw_logger.debug(
                "semantic-miss  label=%r  record_id=%s  step=%s",
                label, record_id, step_no,
            )
            continue

    # ── Pass 4: presence-only (attached) with short timeout ──────────────────
    for strategy, loc_val, rank, _is_p in ordered:
        loc = _build_locator(page, strategy, loc_val)
        try:
            _wait_for_locator_state(loc, "attached", short_timeout, poll_interval_s)
            loc.scroll_into_view_if_needed(timeout=short_timeout)
            raw_event["_used_strategy"] = strategy
            raw_event["_used_locator"]  = loc_val
            raw_event["_is_primary"]    = _is_p
            raw_event["_used_rank"]     = rank
            _store_rect(page, loc, raw_event)
            print(
                f"[playwright_replay] Presence-only fallback; rank={rank} strategy={strategy!r}",
                flush=True,
            )
            return loc, strategy, rank
        except (PwTimeout, Exception):
            _pw_logger.debug(
                "presence-miss  rank=%s  strategy=%r  record_id=%s  step=%s",
                rank, strategy, record_id, step_no,
            )
            continue

    raise Exception(
        f"Could not locate element by any strategy. "
        f"record_id={record_id}  step={step_no}  locators={locators}"
    )


def _wait_for_same_page_next_step_target(
    page,
    current_page_url: str,
    current_raw_event: dict,
    next_step,
    step_timeout_ms: int,
    poll_interval_s: float,
) -> None:
    """Best-effort stabilization for same-page AJAX/UI updates."""
    if next_step is None or not getattr(next_step, "raw_event", None):
        return
    expected = _url_base_path(current_page_url or "")
    if _url_base_path(getattr(next_step, "page_url", "") or "") != expected:
        return
    if _url_base_path(page.url or "") != expected:
        return
    try:
        _next_raw = dict(next_step.raw_event or {})
        _next_raw["_record_id"] = current_raw_event.get("_record_id", "?")
        _next_raw["_step_no"] = getattr(next_step, "step_no", "?")
        _find_element(
            page,
            _next_raw,
            timeout=min(4000, step_timeout_ms),
            poll_interval_s=poll_interval_s,
        )
    except Exception:
        pass


def _store_rect(page, loc, raw_event: dict):
    """Store the element bounding rect in raw_event for screenshot highlight."""
    try:
        box = loc.bounding_box()
        if box:
            raw_event["_element_rect"] = {
                "x": box["x"], "y": box["y"],
                "w": box["width"], "h": box["height"],
            }
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Screenshot helper
# ---------------------------------------------------------------------------

def _capture_screenshot(page, raw_event: dict) -> bytes | None:
    """Capture page screenshot with red highlight on the target element."""
    _el_rect = raw_event.get("_element_rect")
    try:
        if _el_rect:
            page.evaluate("""(rect) => {
                var d = document.createElement('div');
                d.id = '__ss_hl__';
                // position:fixed uses viewport coordinates, which is exactly
                // what Playwright's bounding_box() returns.  position:absolute
                // is relative to the document origin and shifts with scroll.
                d.style.cssText =
                    'position:fixed;border:3px solid red;'
                    + 'background:rgba(255,0,0,0.15);'
                    + 'z-index:2147483647;pointer-events:none;box-sizing:border-box;'
                    + 'left:' + rect.x + 'px;'
                    + 'top:' + rect.y + 'px;'
                    + 'width:' + rect.w + 'px;'
                    + 'height:' + rect.h + 'px;';
                document.documentElement.appendChild(d);
            }""", _el_rect)
        ss_bytes = page.screenshot(full_page=False)
        if _el_rect:
            page.evaluate("var e=document.getElementById('__ss_hl__');if(e)e.remove();")
        return ss_bytes
    except Exception:
        return None


def _selector_for_waits(raw_event: dict) -> str | None:
    """Best-effort selector string for page.wait_for_selector calls."""
    strategy = str(raw_event.get("_used_strategy") or "").strip()
    value = raw_event.get("_used_locator")
    if not strategy or not value:
        return None

    selector = str(value).strip()
    if not selector:
        return None

    if strategy == "xpath":
        return f"xpath={selector}"
    if strategy in {"css", "id", "class", "className", "tagName", "href", "type", "raw-id"}:
        return selector
    if strategy == "name":
        if _looks_like_full_selector(selector):
            return selector
        return f"[name='{selector}']"
    if strategy == "title":
        return f"[title='{selector}']"
    if strategy == "value":
        return f"[value='{selector}']"

    return None


def _prepare_element_for_action(page, loc, raw_event: dict, timeout_ms: int) -> None:
    """Run explicit attached/visible checks before any element-driven action."""
    selector = _selector_for_waits(raw_event)
    if selector:
        page.wait_for_selector(selector, state="attached", timeout=timeout_ms)
        page.wait_for_selector(selector, state="visible", timeout=timeout_ms)

    _wait_for_visible(page, loc, timeout_ms)

# ---------------------------------------------------------------------------
# Page readiness / element visibility helpers
# ---------------------------------------------------------------------------

_PAGE_READY_TIMEOUT_MS     = 60_000  # ms — document.readyState == 'complete'
_ELEMENT_VISIBLE_TIMEOUT_MS = 60_000  # ms — element visible after located


def _wait_for_page_ready(page, timeout_ms: int = _PAGE_READY_TIMEOUT_MS) -> None:
    """Block until document.readyState == 'complete'.  Logs a warning (does not raise) on timeout."""
    try:
        page.wait_for_function(
            "() => document.readyState === 'complete'",
            timeout=timeout_ms,
        )
    except Exception:
        _pw_logger.warning(
            "page-not-ready: document.readyState did not reach 'complete' within %sms", timeout_ms
        )


def _wait_for_visible(page, loc, timeout_ms: int = _ELEMENT_VISIBLE_TIMEOUT_MS) -> None:
    """Scroll locator into the viewport then wait until it is visible.  Logs a warning on timeout."""
    try:
        loc.scroll_into_view_if_needed(timeout=timeout_ms)
        loc.wait_for(state="visible", timeout=timeout_ms)
    except Exception:
        _pw_logger.warning(
            "element-not-visible: element did not become visible within %sms", timeout_ms
        )


def _wait_for_blocking_overlays_to_clear(page, timeout_ms: int, poll_interval_s: float) -> None:
    """Wait until common blocking overlays are no longer visible.

    Uses a two-phase approach:
    1. Playwright-native wait_for_selector(state="hidden") for #inProgressPage
       (XPath: //*[@id="inProgressPage"]) — fast, event-driven, most reliable.
    2. JS-based polling loop for all other overlay selectors.
    """
    # Phase 1 — explicit Playwright-native wait for the JSF in-progress overlay.
    # wait_for_selector with state="hidden" resolves as soon as the element is
    # either not in the DOM or not visible, so it works whether the element
    # exists or not (no-op when absent).
    _in_progress_timeout = max(int(timeout_ms), 500)
    for _in_progress_sel in ("#inProgressPage", "//*[@id='inProgressPage']"):
        try:
            page.wait_for_selector(
                _in_progress_sel,
                state="hidden",
                timeout=_in_progress_timeout,
            )
            break  # one succeeded — no need to try the XPath form as well
        except Exception:
            pass  # element absent or timed out — continue to JS-poll phase

    deadline = time.monotonic() + max(timeout_ms, 0) / 1000.0
    last_visible_count = 0
    while True:
        try:
            visible_count = page.locator(_BLOCKING_OVERLAY_SELECTOR).evaluate_all(
                """els => els.filter(el => {
                    const style = window.getComputedStyle(el);
                    if (!style) return false;
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    if (Number(style.opacity || '1') === 0) return false;
                    const rect = el.getBoundingClientRect();
                    return rect.width >= 4 && rect.height >= 4;
                }).length"""
            )
        except Exception:
            visible_count = 0

        if not visible_count:
            return

        last_visible_count = visible_count
        if time.monotonic() >= deadline:
            _pw_logger.warning(
                "overlay-wait-timeout  visible_overlays=%s  selector=%r",
                last_visible_count,
                _BLOCKING_OVERLAY_SELECTOR,
            )
            return

        time.sleep(max(poll_interval_s, 0.05))


# ---------------------------------------------------------------------------
# Action performer
# ---------------------------------------------------------------------------

def _url_base_path(url: str) -> str:
    """Return the URL stripped of query-string and path parameters.

    JSF apps append ;jsessionid=... to URLs as path parameters.  Comparing
    only the base path avoids false mismatch when the recorded URL lacks the
    session token but the live browser URL has one.
    """
    if not url:
        return ""
    # Strip fragment
    url = url.split("#")[0]
    # Strip query string
    url = url.split("?")[0]
    # Strip path parameters (;jsessionid=..., ;JSESSIONID=...)
    url = url.split(";")[0]
    return url.rstrip("/")


_TOGGLE_CONTROL_JS = """
el => {
    const label = el.closest ? el.closest('label') : null;
    let control = null;
    if (el.matches && el.matches('input[type="checkbox"], input[type="radio"]')) {
        control = el;
    }
    if (!control && label) {
        control = label.control || null;
        if (!control && label.htmlFor) {
            control = document.getElementById(label.htmlFor);
        }
        if (!control) {
            control = label.querySelector('input[type="checkbox"], input[type="radio"]');
        }
    }
    if (!control) {
        const container = el.closest ? el.closest('.checkbox, .radio, [role="checkbox"], [role="radio"]') : null;
        if (container) {
            control = container.querySelector('input[type="checkbox"], input[type="radio"]');
        }
    }
    return control || el;
}
"""


def _toggle_checked_state(loc) -> bool:
    return bool(loc.evaluate(f"el => !!(({_TOGGLE_CONTROL_JS})(el).checked)"))


def _dom_toggle_click(loc) -> None:
    loc.evaluate(f"el => (({_TOGGLE_CONTROL_JS})(el)).click()")


def _text_toggle_click(page, raw_event: dict, timeout_ms: int) -> None:
    target_id = _toggle_target_id(raw_event)
    if target_id:
        try:
            page.locator(f'label[for="{target_id}"]').first.click(timeout=timeout_ms)
            return
        except Exception:
            pass
    for label in _toggle_label_candidates(raw_event):
        try:
            page.get_by_text(label, exact=True).first.click(timeout=timeout_ms)
            return
        except Exception:
            continue
    raise RuntimeError("No visible checkbox label text was clickable")


def _set_toggle_state(page, loc, raw_event: dict, expected_checked: bool | None, timeout_ms: int) -> bool:
    before = _toggle_checked_state(loc)
    target = before if expected_checked is None else bool(expected_checked)
    if before == target:
        return before

    autosubmit = _toggle_autosubmit_enabled(raw_event)
    prefer_label = _prefer_label_toggle_click(raw_event)

    click_errors: list[Exception] = []
    click_attempts = [
        lambda: loc.click(timeout=timeout_ms),
        lambda: _dom_toggle_click(loc),
        lambda: _text_toggle_click(page, raw_event, timeout_ms),
    ]
    if prefer_label:
        click_attempts = [click_attempts[-1], *click_attempts[:-1]]

    for _click in click_attempts:
        try:
            _click()
        except Exception as exc:
            click_errors.append(exc)
        if autosubmit:
            try:
                page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
            except Exception:
                pass
            try:
                _wait_for_blocking_overlays_to_clear(page, timeout_ms, 0.25)
            except Exception:
                pass
        if _toggle_checked_state(loc) == target:
            return target

    try:
        final_state = bool(loc.evaluate(
            """
            (el, expected) => {
                const target = ((node) => {
                    const label = node.closest ? node.closest('label') : null;
                    let control = null;
                    if (node.matches && node.matches('input[type="checkbox"], input[type="radio"]')) {
                        control = node;
                    }
                    if (!control && label) {
                        control = label.control || null;
                        if (!control && label.htmlFor) {
                            control = document.getElementById(label.htmlFor);
                        }
                        if (!control) {
                            control = label.querySelector('input[type="checkbox"], input[type="radio"]');
                        }
                    }
                    if (!control) {
                        const container = node.closest ? node.closest('.checkbox, .radio, [role="checkbox"], [role="radio"]') : null;
                        if (container) {
                            control = container.querySelector('input[type="checkbox"], input[type="radio"]');
                        }
                    }
                    return control || node;
                })(el);
                target.checked = !!expected;
                target.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
                target.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
                return !!target.checked;
            }
            """,
            target,
        ))
        if final_state == target:
            return target
    except Exception as exc:
        click_errors.append(exc)

    if click_errors:
        raise RuntimeError(str(click_errors[-1]))
    raise RuntimeError("Toggle state did not change")


def _select_current_option(loc) -> tuple[str, str]:
    value, text = loc.evaluate(
        """
        el => {
            const option = el.selectedOptions && el.selectedOptions.length ? el.selectedOptions[0] : null;
            return [String(el.value || ''), option ? String((option.textContent || '').trim()) : ''];
        }
        """
    )
    return str(value or ""), str(text or "")


def _set_select_value(page, loc, raw_event: dict, timeout_ms: int) -> str:
    text = str(raw_event.get("text") or "").strip()
    value = str(raw_event.get("value") or "").strip()

    if text:
        try:
            loc.select_option(label=text, timeout=timeout_ms)
            return text
        except Exception:
            pass
    if value:
        try:
            loc.select_option(value=value, timeout=timeout_ms)
            return value
        except Exception:
            pass

    matched = loc.evaluate(
        """
        (el, payload) => {
            const wantedText = String(payload.text || '').trim();
            const wantedValue = String(payload.value || '').trim();
            const options = Array.from(el.options || []);
            const match = options.find(opt => {
                const optText = String((opt.textContent || '').trim());
                const optValue = String(opt.value || '');
                return (wantedText && optText === wantedText) || (wantedValue && optValue === wantedValue);
            });
            if (!match) return '';
            el.value = match.value;
            match.selected = true;
            el.dispatchEvent(new Event('input', { bubbles: true, cancelable: true }));
            el.dispatchEvent(new Event('change', { bubbles: true, cancelable: true }));
            return String(match.value || '');
        }
        """,
        {"text": text, "value": value},
    )
    if matched:
        current_value, current_text = _select_current_option(loc)
        if (text and current_text == text) or (value and current_value == value):
            return current_text or current_value

    loc.focus()
    try:
        page.keyboard.press("Home")
    except Exception:
        pass
    if text:
        page.keyboard.type(text, delay=25)
    elif value:
        page.keyboard.type(value, delay=25)
    try:
        page.keyboard.press("Enter")
    except Exception:
        pass

    current_value, current_text = _select_current_option(loc)
    if text and current_text == text:
        return current_text
    if value and current_value == value:
        return current_value
    raise RuntimeError(f"Could not select option text={text!r} value={value!r}")


def _recorded_step_delay(raw_event: dict | None, max_delay: float = 2.0) -> float:
    if not isinstance(raw_event, dict):
        return 0.0
    try:
        val = max(0.0, float(raw_event.get("_recorded_step_delay_s") or 0.0))
        return min(val, max_delay) if max_delay > 0 else val
    except Exception:
        return 0.0


def _check_validation_rule(rule: str, actual_text: str) -> tuple[bool, str]:
    """Parse a validation rule string and check it against actual_text.

    Rule format: "Category | Operator | Value1 [| Value2]"
    Returns (passed: bool, detail: str).
    """
    parts = [p.strip() for p in rule.split(" | ")]
    if len(parts) < 2:
        passed = (rule == actual_text)
        return passed, f"expected '{rule}' but got '{actual_text[:120]}'"

    category = parts[0]
    operator = parts[1]
    value1 = parts[2] if len(parts) > 2 else ""
    value2 = parts[3] if len(parts) > 3 else ""

    actual = actual_text.strip()

    if category in ("General / Text", ""):
        if operator == "Equals":
            passed = (actual == value1)
        elif operator == "Does not equal":
            passed = (actual != value1)
        elif operator == "Contains":
            passed = (value1 in actual)
        elif operator == "Does not contain":
            passed = (value1 not in actual)
        elif operator == "Starts with":
            passed = actual.startswith(value1)
        elif operator == "Ends with":
            passed = actual.endswith(value1)
        elif operator == "Is empty":
            passed = (actual == "")
        elif operator == "Is not empty":
            passed = (actual != "")
        else:
            passed = (actual == value1)
    elif category == "Number":
        try:
            n_actual = float(actual.replace(",", ""))
            n_val1 = float(value1.replace(",", "")) if value1 else 0.0
            n_val2 = float(value2.replace(",", "")) if value2 else 0.0
        except (ValueError, TypeError):
            return False, f"cannot parse number: actual='{actual[:60]}' value='{value1}'"
        if operator == "Equals":
            passed = (n_actual == n_val1)
        elif operator == "Does not equal":
            passed = (n_actual != n_val1)
        elif operator == "Greater than":
            passed = (n_actual > n_val1)
        elif operator == "Greater than or equal to":
            passed = (n_actual >= n_val1)
        elif operator == "Less than":
            passed = (n_actual < n_val1)
        elif operator == "Less than or equal to":
            passed = (n_actual <= n_val1)
        elif operator == "Between":
            passed = (n_val1 <= n_actual <= n_val2)
        elif operator == "Not between":
            passed = not (n_val1 <= n_actual <= n_val2)
        else:
            passed = (n_actual == n_val1)
    elif category == "Boolean":
        low = actual.lower()
        if operator == "Is true":
            passed = low in ("true", "1", "yes", "on", "checked")
        elif operator == "Is false":
            passed = low in ("false", "0", "no", "off", "", "unchecked")
        else:
            passed = False
    else:
        passed = (actual == value1)

    if passed:
        return True, ""
    return False, f"expected '{rule}' but got '{actual_text[:120]}'"


def _perform_action(
    page,
    step,
    raw_event: dict,
    step_timeout_ms: int = 60000,
    nav_timeout_ms: int = 30000,
    page_load_timeout_ms: int = 30000,
    overlay_timeout_ms: int = 60000,
    poll_interval_s: float = 0.5,
    nav_retries: int = 5,
    nav_retry_wait_s: float = 5,
    next_step=None,
) -> str:
    """Execute a single step action using Playwright. Returns a status message."""
    action = raw_event.get("action", step.action)
    page_url = step.page_url

    # Always wait for any blocking overlay (e.g. JSF #inProgressPage) to
    # clear before attempting anything — including URL checks and navigation.
    _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)

    # Navigate if we're on a different page
    _nav_mismatch_msg: str | None = None
    try:
        current = _url_base_path(page.url)
        expected = _url_base_path(page_url)
        if current != expected and action not in ("navigate", "open", "goto"):
            if action in ("submit", "navigate_back", "navigate_forward", "navigate_unknown"):
                return "Form submitted"
            if _should_skip_stale_postback_step(raw_event, step, next_step, page.url):
                return "Skipped stale autosubmit step after navigation"
            waited_for_expected = _wait_for_expected_page(
                page,
                page_url,
                max(step_timeout_ms, page_load_timeout_ms),
                poll_interval_s,
            )
            if waited_for_expected:
                current = _url_base_path(page.url)
                expected = _url_base_path(page_url)
            if current == expected:
                pass
            else:
                _safe_page_goto(page, page_url, nav_timeout_ms, page_load_timeout_ms, nav_retries, nav_retry_wait_s)
                # After navigating to the expected page, verify the browser actually
                # landed there.  A redirect (e.g. back to the login page when
                # credentials are invalid) means the expected page is unreachable —
                # record the mismatch and raise AFTER the except block so it
                # propagates to the step retry loop instead of being swallowed.
                _post_nav = _url_base_path(page.url)
                if _post_nav != _url_base_path(page_url):
                    _nav_mismatch_msg = (
                        f"Page URL mismatch after navigation: expected {_url_base_path(page_url)!r} "
                        f"but browser is at {_post_nav!r} — possible auth failure or redirect."
                    )
    except Exception:
        pass

    # Raise the URL-mismatch error OUTSIDE the swallowing try/except so it
    # propagates to the per-step retry loop and is recorded as a step failure.
    if _nav_mismatch_msg:
        raise Exception(_nav_mismatch_msg)

    if action in ("navigate", "open", "goto"):
        if page_url:
            _safe_page_goto(page, page_url, nav_timeout_ms, page_load_timeout_ms, nav_retries, nav_retry_wait_s)
            return f"Opened URL: {page_url}"
        return "Skipped navigation (missing page_url)"

    if action == "click":
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        # Capture screenshot before click (may navigate away)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)

        # --- <select> element: use value selection instead of raw click ---
        # A raw .click() on a JSF/autosubmit select can trigger an unintended
        # form submission.  Treat it like a change event.
        _tag = (step.element_tag or raw_event.get("tag") or "").lower()
        if _tag == "select":
            selected = _set_select_value(page, loc, raw_event, step_timeout_ms)
            if _select_autosubmit_enabled(raw_event):
                _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
            _wait_for_same_page_next_step_target(
                page, page_url, raw_event, next_step, step_timeout_ms, poll_interval_s
            )
            return f"Selected: {selected}"

        _pre_click_url = page.url
        _input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").lower()
        _is_submit_btn = _input_type in ("submit", "button", "reset") or str(raw_event.get("tag") or "").lower() == "button"

        def _dom_click() -> None:
            _dom_toggle_click(loc)

        def _click_with_dom_fallback() -> None:
            try:
                loc.click(timeout=step_timeout_ms)
            except Exception:
                _dom_click()

        def _ensure_toggle_state() -> None:
            if _input_type not in ("checkbox", "radio"):
                return
            expected_checked = raw_event.get("checked")
            final_checked = _set_toggle_state(page, loc, raw_event, bool(expected_checked) if expected_checked is not None else None, step_timeout_ms)
            if expected_checked is not None and final_checked != bool(expected_checked):
                raise RuntimeError(
                    f"Expected {_input_type} checked={bool(expected_checked)} but got {final_checked}"
                )

        # If the very next recorded step is an explicit "submit" on this same
        # page, the form submission is driven by that step — using
        # expect_navigation here would intercept the wrong navigation in JSF
        # apps (e.g. prematurely catching a redirect to the global hub instead
        # of letting the submit step POST with the correct action parameter).
        _next_is_form_submit = (
            next_step is not None
            and next_step.action == "submit"
            and _url_base_path(next_step.page_url or "") == _url_base_path(page_url or "")
        )
        if _is_submit_btn and not _next_is_form_submit:
            # Wrap the click inside expect_navigation so Playwright registers the
            # listener BEFORE the click fires.  wait_for_load_state() called
            # *after* click returns immediately if the page is already in that
            # state — expect_navigation waits for the *next* navigation commit.
            try:
                with page.expect_navigation(wait_until="domcontentloaded", timeout=page_load_timeout_ms):
                    _click_with_dom_fallback()
                _ensure_toggle_state()
                # Navigation committed — wait for all resources too
                try:
                    page.wait_for_load_state("load", timeout=page_load_timeout_ms)
                except Exception:
                    pass
                # JSF/RichFaces apps show a full-page blocking overlay (e.g.
                # #inProgressPage) during server-side processing after the page
                # loads.  Wait for it to clear before proceeding.
                _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
            except Exception:
                # No navigation triggered (e.g. button opened a dialog only).
                # If URL somehow changed anyway, settle the load state.
                try:
                    if page.url != _pre_click_url:
                        page.wait_for_load_state("load", timeout=page_load_timeout_ms)
                        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
                except Exception:
                    pass
        elif _is_submit_btn and _next_is_form_submit:
            # Plain click — the following submit step will drive the POST and
            # navigation.  Do NOT use expect_navigation here.
            _click_with_dom_fallback()
            _ensure_toggle_state()
            try:
                if page.url != _pre_click_url:
                    page.wait_for_load_state("domcontentloaded", timeout=page_load_timeout_ms)
                    _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
            except Exception:
                pass
            _wait_for_same_page_next_step_target(
                page, page_url, raw_event, next_step, step_timeout_ms, poll_interval_s
            )
        else:
            if _input_type in ("checkbox", "radio"):
                _ensure_toggle_state()
            else:
                _click_with_dom_fallback()
            # Probe for a JSF/AJAX blocking overlay that may appear shortly
            # after the click (there is a race window between the click and
            # the overlay appearing on the page, so check for "visible"
            # first with a short timeout before doing the full clear-wait).
            try:
                page.wait_for_selector(
                    "#inProgressPage", state="visible", timeout=60000, poll_interval=poll_interval_s
                )
                # Overlay appeared — wait for it to fully clear.
                _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
            except Exception:
                # No #inProgressPage within 500 ms — check for plain navigation.
                try:
                    if page.url != _pre_click_url:
                        page.wait_for_load_state("domcontentloaded", timeout=page_load_timeout_ms)
                        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
                except Exception:
                    pass
            _wait_for_same_page_next_step_target(
                page, page_url, raw_event, next_step, step_timeout_ms, poll_interval_s
            )
        return "Left\u2011mouse\u2011click is pressed."

    elif action == "dblclick":
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)
        loc.dblclick(timeout=step_timeout_ms)
        return "Double-click is pressed."

    elif action == "contextmenu":
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)
        loc.click(button="right", timeout=step_timeout_ms)
        return "Right-click is pressed."

    elif action in ("input", "change"):
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)
        value = raw_event.get("value") or ""

        # Handle <select>
        tag = step.element_tag or ""
        if tag.lower() == "select":
            selected = _set_select_value(page, loc, raw_event, step_timeout_ms)
            if _select_autosubmit_enabled(raw_event):
                _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
                _wait_for_same_page_next_step_target(
                    page, page_url, raw_event, next_step, step_timeout_ms, poll_interval_s
                )
            return f"Selected: {selected or value}"

        # Handle checkbox/radio
        input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").lower()
        if input_type in ("checkbox", "radio"):
            checked = raw_event.get("checked")
            final_checked = _set_toggle_state(page, loc, raw_event, bool(checked) if checked is not None else None, step_timeout_ms)
            if checked is not None and final_checked != bool(checked):
                raise RuntimeError(
                    f"Expected {input_type} checked={bool(checked)} but got {final_checked}"
                )
            if _toggle_autosubmit_enabled(raw_event):
                _wait_for_blocking_overlays_to_clear(page, overlay_timeout_ms, poll_interval_s)
                _wait_for_same_page_next_step_target(
                    page, page_url, raw_event, next_step, step_timeout_ms, poll_interval_s
                )
            return f"{'Checked' if final_checked else 'Unchecked'} {input_type}"

        # Regular text input
        loc.fill(value, timeout=step_timeout_ms)
        return f"Typed: {value[:50]}"

    elif action == "keydown":
        key = raw_event.get("key", "")
        if key == "Tab":
            return "Tab key is pressed"
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)
        loc.press(key, timeout=step_timeout_ms)
        return f"keydown: {key}"

    elif action == "submit":
        # If navigation is already in progress (e.g. because the preceding click
        # on a submit button already triggered a form submission / JSF postback),
        # do NOT call form.submit() again — that would cancel the in-flight request.
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(step_timeout_ms, 5000))
        except Exception:
            pass
        _current_url = page.url.rstrip("/")
        _expected_url = (page_url or "").rstrip("/")
        if _current_url != _expected_url:
            # The page already navigated away — the form was already submitted by
            # the preceding click. Skip the redundant submit.
            return "Form submitted"
        loc, strat, rank = _find_element(page, raw_event, timeout=step_timeout_ms, poll_interval_s=poll_interval_s)
        _prepare_element_for_action(page, loc, raw_event, step_timeout_ms)
        raw_event["_pre_ss_bytes"] = _capture_screenshot(page, raw_event)
        # Use requestSubmit() (fires submit event through JS handlers including
        # JSF/PrimeFaces) instead of raw form.submit() which bypasses them.
        loc.evaluate("""el => {
            var f = el.closest('form');
            if (f) {
                if (f.requestSubmit) { f.requestSubmit(); }
                else { f.submit(); }
            }
        }""")
        try:
            page.wait_for_load_state("domcontentloaded", timeout=step_timeout_ms)
        except Exception:
            pass
        return "Form is submitted"

    elif action == "scroll":
        delta_x = int(raw_event.get("delta_x") or 0)
        delta_y = int(raw_event.get("delta_y") or 0)
        page.mouse.wheel(delta_x, delta_y)
        direction = "up" if delta_y < 0 else "down"
        return f"Scrolled {direction} (\u0394x={delta_x}, \u0394y={delta_y})"

    elif action == "navigate_back":
        page.go_back(wait_until="domcontentloaded", timeout=nav_timeout_ms)
        return "Browser back button"

    elif action == "navigate_forward":
        page.go_forward(wait_until="domcontentloaded", timeout=nav_timeout_ms)
        return "Browser forward button"

    elif action == "navigate_unknown":
        page.go_back(wait_until="domcontentloaded", timeout=nav_timeout_ms)
        return "Browser navigation (back)"

    else:
        return f"Skipped (unsupported action: {action})"


# ---------------------------------------------------------------------------
# Public API — same signature as replay.replay_session
# ---------------------------------------------------------------------------

def replay_session(
    record_id: str,
    headless: bool = False,
    pause_event: threading.Event | None = None,
    stop_event: threading.Event | None = None,
    on_step: Callable[[dict], None] | None = None,
    steps: list | None = None,
    run_id: str | None = None,
    runner: str = "",
    folder_name: str = "",
    keep_open: bool = False,
    rdp_port: int | None = None,
) -> list[dict]:
    """
    Replay all steps for *record_id* using Playwright (Chromium).

    Same interface as recorder.replay.replay_session — can be used as a
    drop-in replacement.
    """
    # Playwright uses an async event loop internally; allow Django ORM calls within it.
    os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"

    if not _PW_AVAILABLE:
        raise ImportError(
            "playwright is not installed. Run: pip install playwright && playwright install chromium"
        )

    import uuid as _uuid
    from .models import Step, Recording, RunResult, DataEntry, LocatorStat, Locator

    if run_id is None:
        run_id = str(_uuid.uuid4())

    _runner = runner or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    # Load steps if not passed in
    if steps is None:
        _raw = list(Step.objects.filter(record_id=record_id).order_by("step_no", "id"))
        if not _raw:
            _raw = list(Recording.objects.filter(record_id=record_id).order_by("step_no", "id"))
        _seen: set[int] = set()
        steps = []
        for _s in _raw:
            if _s.step_no not in _seen:
                _seen.add(_s.step_no)
                steps.append(_s)
    if not steps:
        return [{"step_no": 0, "action": "", "page_url": "",
                 "status": "No steps found for this session.", "ok": False}]

    # Load configurable replay timeouts from app_config
    _cfg = _load_replay_config()
    _page_timeout   = int(_cfg["page_timeout"] * 1000)
    _step_timeout   = int(_cfg["step_timeout"] * 1000)  # convert to ms for Playwright
    _poll_interval  = float(_cfg["poll_interval"])
    _step_retries   = int(_cfg["step_retries"])
    _retry_delay    = _cfg["retry_delay"]
    _step_delay     = float(_cfg["step_delay"]) / 1000.0
    _step_settle    = _cfg["step_settle"]
    _nav_timeout    = int(_cfg["window_timeout"] * 1000)
    _nav_retries = int(_cfg["nav_retries"])
    _nav_retry_wait = _cfg["nav_retry_wait"]
    _overlay_timeout = int(_cfg["overlay_timeout"] * 1000)
    _pw_logger.info(
        "replay-config  page_timeout=%sms  step_timeout=%sms  overlay_timeout=%sms  "
        "poll_interval=%ss  step_retries=%s  retry_delay=%ss  step_delay=%ss  "
        "step_settle=%ss  nav_timeout=%sms  nav_retries=%s  nav_retry_wait=%ss",
        _page_timeout, _step_timeout, _overlay_timeout, _poll_interval,
        _step_retries, _retry_delay, _step_delay, _step_settle, _nav_timeout,
        _nav_retries, _nav_retry_wait,
    )

    # Load data overrides from DB
    try:
        _data_rows = list(DataEntry.objects.filter(record_id=record_id))
        _data_map: dict[int, str] = {
            de.step_no: (de.value or "")
            for de in _data_rows
        }
        _data_id_map: dict[int, int] = {
            de.step_no: de.id
            for de in _data_rows
            if getattr(de, "id", None) is not None
        }
    except Exception:
        _data_map = {}
        _data_id_map = {}

    # Determine folder metadata
    from .models import SessionMeta
    try:
        _sm = SessionMeta.objects.get(record_id=record_id)
        _parent_folder_id = getattr(_sm, "parent_folder_id", None)
        _sub_folder_id = getattr(_sm, "sub_folder_id", None)
        _end_folder_id = getattr(_sm, "end_folder_id", None)
    except Exception:
        _parent_folder_id = _sub_folder_id = _end_folder_id = None

    results: list[dict] = []
    _executed_step_nos: set[int] = set()

    # Load Playwright launch / context / page settings from app_config
    _pw_cfg = _load_playwright_config()
    _pw_logger.info("playwright-config  %s", _pw_cfg)

    # -- Build launch kwargs ------------------------------------------------
    _launch_args, _headless_override = _parse_playwright_extra_args(_pw_cfg["extra_args"] or "")
    _effective_headless = headless if _headless_override is None else _headless_override
    _wants_start_maximized = any(arg.strip().lower() == "--start-maximized" for arg in _launch_args)
    # --start-maximized is a GUI-only flag; it can prevent browser launch in headless
    # mode on some Playwright/Chromium versions.  Strip it when running headlessly.
    if _effective_headless and _wants_start_maximized:
        _launch_args = [a for a in _launch_args if a.strip().lower() != "--start-maximized"]
        _pw_logger.info("launch-args  stripped --start-maximized (headless mode active)")
        _wants_start_maximized = False
    _launch_kwargs: dict[str, Any] = {
        "headless": _effective_headless,
        "args": _launch_args,
    }
    if _pw_cfg["slow_mo"]:
        _launch_kwargs["slow_mo"] = _pw_cfg["slow_mo"]
    if _pw_cfg["devtools"]:
        _launch_kwargs["devtools"] = True

    # Proxy
    if _pw_cfg["proxy_server"]:
        _proxy: dict[str, str] = {"server": _pw_cfg["proxy_server"]}
        if _pw_cfg["proxy_username"]:
            _proxy["username"] = _pw_cfg["proxy_username"]
        if _pw_cfg["proxy_password"]:
            _proxy["password"] = _pw_cfg["proxy_password"]
        _launch_kwargs["proxy"] = _proxy

    # -- Build context kwargs -----------------------------------------------
    _ctx_kwargs: dict[str, Any] = {}

    # Viewport: use fixed viewport only when requested. A headful launch with
    # --start-maximized needs no_viewport=True; otherwise the fixed viewport
    # size keeps the browser window from opening maximized.
    if _wants_start_maximized and not _effective_headless:
        _ctx_kwargs["no_viewport"] = True
    elif _pw_cfg["viewport_width"] and _pw_cfg["viewport_height"]:
        _ctx_kwargs["viewport"] = {
            "width": _pw_cfg["viewport_width"],
            "height": _pw_cfg["viewport_height"],
        }
    else:
        _ctx_kwargs["no_viewport"] = True

    if _pw_cfg["user_agent"]:
        _ctx_kwargs["user_agent"] = _pw_cfg["user_agent"]
    if _pw_cfg["locale"]:
        _ctx_kwargs["locale"] = _pw_cfg["locale"]
    if _pw_cfg["timezone_id"]:
        _ctx_kwargs["timezone_id"] = _pw_cfg["timezone_id"]
    if _pw_cfg["geo_latitude"] and _pw_cfg["geo_longitude"]:
        try:
            _ctx_kwargs["geolocation"] = {
                "latitude": float(_pw_cfg["geo_latitude"]),
                "longitude": float(_pw_cfg["geo_longitude"]),
            }
        except (ValueError, TypeError):
            pass
    if _pw_cfg["permissions"]:
        _ctx_kwargs["permissions"] = [
            p.strip() for p in _pw_cfg["permissions"].split(",") if p.strip()
        ]
    if _pw_cfg["accept_downloads"]:
        _ctx_kwargs["accept_downloads"] = True
    if _pw_cfg["record_video"]:
        _vid_dir = _pw_cfg["record_video_dir"] or "logs/videos"
        if not os.path.isabs(_vid_dir):
            _vid_dir = os.path.join(_BASE_DIR, _vid_dir)
        os.makedirs(_vid_dir, exist_ok=True)
        _ctx_kwargs["record_video_dir"] = _vid_dir

    # _pw_crash captures any unhandled exception from browser launch/execution so
    # the not_executed cleanup (in the finally block below) always runs even when
    # the browser itself fails to start.
    _pw_crash: BaseException | None = None
    try:
      with sync_playwright() as pw:
        # Persistent context (keeps cookies/sessions across runs)
        _use_persistent = bool(_pw_cfg["user_data_dir"])
        if _use_persistent:
            _udd = _pw_cfg["user_data_dir"]
            if not os.path.isabs(_udd):
                _udd = os.path.join(_BASE_DIR, _udd)
            os.makedirs(_udd, exist_ok=True)
            context = pw.chromium.launch_persistent_context(
                _udd,
                **_launch_kwargs,
                **_ctx_kwargs,
            )
            page = context.pages[0] if context.pages else context.new_page()
            browser = None  # persistent context has no separate browser handle
        else:
            browser = pw.chromium.launch(**_launch_kwargs)
            context = browser.new_context(**_ctx_kwargs)
            page = context.new_page()

        # Page-level defaults
        page.set_default_timeout(_pw_cfg["default_timeout"])
        page.set_default_navigation_timeout(_pw_cfg["default_navigation_timeout"])

        # Auto-accept native browser dialogs (alert / confirm / prompt).
        # Playwright's default is to *dismiss* confirm() → returns false, which
        # in JSF/enterprise apps can cause "Cancel" navigation (e.g. back to the
        # global hub) instead of the expected "OK" navigation.
        page.on("dialog", lambda dlg: dlg.accept())

        # Stealth: in headless mode some JSF / enterprise apps detect automation
        # via window.outerWidth == window.innerWidth (no browser chrome present).
        # Override to mimic a real headed browser so server-side JS doesn't
        # redirect to the global home / logout page.
        if _effective_headless:
            page.add_init_script("""(function(){
                try {
                    Object.defineProperty(window,'outerWidth',
                        {get:function(){return window.innerWidth+120;},configurable:true});
                    Object.defineProperty(window,'outerHeight',
                        {get:function(){return window.innerHeight+89;},configurable:true});
                } catch(e){}
            })();""")

        # Track which steps actually ran so we can mark the rest not_executed
        # (initialised before `with sync_playwright()` so it is always defined)

        try:
            # Navigate to first step URL
            _first_url = steps[0].page_url
            if _first_url:
                _safe_page_goto(page, _first_url, _nav_timeout, _page_timeout, _nav_retries, _nav_retry_wait)
            time.sleep(1)

            for _step_idx, step in enumerate(steps):
                _next_step = steps[_step_idx + 1] if _step_idx + 1 < len(steps) else None
                # Stop check
                if stop_event and stop_event.is_set():
                    break

                # Pause loop
                while pause_event and pause_event.is_set():
                    if stop_event and stop_event.is_set():
                        break
                    time.sleep(0.2)
                if stop_event and stop_event.is_set():
                    break

                # Prepare locator overrides
                _locs = step.raw_event.get("locators") or {}
                _pri_strat = ""
                _pri_loc = ""

                try:
                    _db_primary = step.primary_locator
                    if _db_primary and _db_primary.locator:
                        _pri_strat = _db_primary.strategy
                        _pri_loc = _db_primary.locator
                        _locs = {**_locs, _db_primary.strategy: _db_primary.locator}
                except Exception:
                    pass

                if not _pri_strat:
                    for _s in _ORDERED_STRATS:
                        if _locs.get(_s):
                            _pri_strat = _s
                            _pri_loc = str(_locs[_s])
                            break

                # Load ALL locators for this step.
                # Order: is_primary=TRUE first, then remaining by locator_rank.
                # This ensures the user-selected primary is tried first in
                # _find_element regardless of the stored rank value.
                _db_locator_chain: list[dict] = []
                try:
                    _all_step_locs = list(
                        Locator.objects.filter(
                            record_id=record_id, step_no=step.step_no
                        ).order_by("locator_rank", "id")
                    )
                    # Separate primary from the rest so primary always leads
                    _primary_locs  = [l for l in _all_step_locs if getattr(l, "is_primary", False)]
                    _fallback_locs = [l for l in _all_step_locs if not getattr(l, "is_primary", False)]
                    for _l in (_primary_locs + _fallback_locs):
                        if _l.strategy and _l.locator:
                            _db_locator_chain.append({
                                "strategy": _l.strategy,
                                "locator":  _l.locator,
                                "rank":     _l.locator_rank if _l.locator_rank is not None else 99,
                                "is_primary": bool(getattr(_l, "is_primary", False)),
                            })
                            # Keep _locs dict in sync so semantic fallbacks have full context
                            _locs.setdefault(_l.strategy, _l.locator)
                except Exception:
                    pass

                _re = step.raw_event
                _db_value = _data_map.get(step.step_no)
                _db_data_id = _data_id_map.get(step.step_no)
                _field_value = _db_value if _db_value is not None else (_re.get("value") or "")

                entry: dict = {
                    "step_no": step.step_no,
                    "action": step.action,
                    "page_url": step.page_url,
                    "element_tag": step.element_tag or "",
                    "locator_strategy": _pri_strat,
                    "locator_value": _pri_loc,
                    "field_name": str(_re.get("name") or _re.get("id") or ""),
                    "field_value": str(_field_value) if _field_value else "",
                    "steps_description": getattr(step, "steps_description", None) or "",
                    "validation": getattr(step, "validation", None) or "",
                    "status": "",
                    "ok": False,
                }

                _event_overrides: dict = {}
                if _pri_strat:
                    _event_overrides["_primary_strategy"] = _pri_strat
                if _locs:
                    _event_overrides["locators"] = _locs
                if _db_locator_chain:
                    _event_overrides["_db_locators"] = _db_locator_chain
                if _db_value is not None:
                    _event_overrides["value"] = _db_value
                if getattr(step, "raw_event_playwright", None):
                    _event_overrides["playwright_info"] = step.raw_event_playwright
                _event_overrides["_record_id"] = str(record_id)
                _event_overrides["_step_no"] = step.step_no

                _event_for_replay = {**step.raw_event, **_event_overrides}

                run_status = RunResult.STATUS_FAIL
                message: str | None = None

                # ── Outer retry wrapper ──────────────────────────────────
                # 1 + _step_retries attempts, _retry_delay seconds apart.
                for _outer_attempt in range(_step_retries + 1):
                    if _outer_attempt > 0:
                        _pw_logger.info(
                            "step-retry  outer=%d/%d  delay=%ss  record_id=%s  step=%s  action=%s",
                            _outer_attempt, _step_retries, _retry_delay,
                            record_id, step.step_no, step.action,
                        )
                        time.sleep(_retry_delay)
                        # Clear cached locator results for a fresh attempt
                        for _k in ("_used_strategy", "_used_locator", "_is_primary", "_used_rank", "_element_rect"):
                            _event_for_replay.pop(_k, None)
                    elif (_recorded_delay := _recorded_step_delay(_event_for_replay, max_delay=_cfg["max_step_delay"])) > 0:
                        time.sleep(_recorded_delay)
                    try:
                        message = _perform_action(
                            page,
                            step,
                            _event_for_replay,
                            step_timeout_ms=_step_timeout,
                            nav_timeout_ms=_nav_timeout,
                            page_load_timeout_ms=_page_timeout,
                            overlay_timeout_ms=_overlay_timeout,
                            poll_interval_s=_poll_interval,
                            nav_retries=_nav_retries,
                            nav_retry_wait_s=_nav_retry_wait,
                            next_step=_next_step,
                        )
                        _actual_strategy = _event_for_replay.get("_used_strategy")
                        _actual_locator = _event_for_replay.get("_used_locator")
                        if _actual_strategy:
                            entry["locator_strategy"] = _actual_strategy
                        if _actual_locator:
                            entry["locator_value"] = str(_actual_locator)
                        entry["status"] = message
                        entry["ok"] = True
                        run_status = RunResult.STATUS_PASS
                        if _step_delay > 0:
                            time.sleep(_step_delay)
                        time.sleep(_step_settle)
                        break  # success — no more retries needed
                    except Exception as exc:
                        message = f"Error: {exc}"
                        entry["status"] = message
                        _pw_logger.error(
                            "FAIL  record_id=%s  step=%s  action=%s  attempt=%d/%d  error=%s",
                            record_id, step.step_no, step.action,
                            _outer_attempt + 1, _step_retries + 1, message,
                        )
                        if stop_event and stop_event.is_set():
                            break  # don't retry if stop requested

                # Validation
                _validation = (getattr(step, "validation", None) or "").strip()
                if _validation and run_status == RunResult.STATUS_PASS:
                    _raw_text = str(step.raw_event.get("text") or "")
                    _v_passed, _v_detail = _check_validation_rule(_validation, _raw_text)
                    if not _v_passed:
                        run_status = RunResult.STATUS_FAIL
                        message = f"Validation failed: {_v_detail}"
                        entry["ok"] = False
                        entry["status"] = message

                # Screenshot — prefer pre-action, fallback to post-action
                _ss_bytes: bytes | None = _event_for_replay.pop("_pre_ss_bytes", None)
                if not _ss_bytes:
                    _ss_bytes = _capture_screenshot(page, _event_for_replay)
                if _ss_bytes:
                    try:
                        os.makedirs(_SCREENSHOTS_DIR, exist_ok=True)
                        _status_tag = "pass" if entry["ok"] else "fail"
                        _ss_file = os.path.join(
                            _SCREENSHOTS_DIR,
                            f"{record_id}_step{step.step_no}_{_status_tag}.png",
                        )
                        with open(_ss_file, "wb") as _ssf:
                            _ssf.write(_ss_bytes)
                    except Exception:
                        pass

                # Print result
                _ok_marker = "PASS" if entry["ok"] else "FAIL"
                _used_strat = _event_for_replay.get("_used_strategy") or _pri_strat or "?"
                _used_locator = _event_for_replay.get("_used_locator") or ""
                _is_primary = _event_for_replay.get("_is_primary", None)
                _used_rank  = _event_for_replay.get("_used_rank", None)
                print(
                    f"[{step.step_no:>4}] {_ok_marker}  {step.action:<12}  {message or ''}",
                    flush=True,
                )
                # locator_stat.log — strategy + locator used per step
                try:
                    from django.utils import timezone as _tz
                    _primary_tag = "YES" if _is_primary else ("NO" if _is_primary is not None else "?")
                    _rank_str    = str(_used_rank) if _used_rank is not None else "?"
                    _locator_stat_log = os.path.join(_LOG_DIR, "locator_stat.log")
                    with open(_locator_stat_log, "a", encoding="utf-8") as _lf:
                        _lf.write(
                            f"{_tz.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                            f"record_id={record_id}  step={step.step_no}  "
                            f"is_primary={_primary_tag}  rank={_rank_str}  "
                            f"strategy={_used_strat}  locator={_used_locator}  "
                            f"{_ok_marker}\n"
                        )
                except Exception:
                    pass

                _result_raw_event = dict(step.raw_event or {})
                if _db_value is not None:
                    _result_raw_event["value"] = _db_value
                    if step.action in ("input", "change"):
                        _result_raw_event["text"] = _db_value
                    _playwright_info = _result_raw_event.get("playwright_info")
                    if isinstance(_playwright_info, dict):
                        _result_raw_event["playwright_info"] = {**_playwright_info, "value": _db_value}
                    _selenium_info = _result_raw_event.get("selenium_info")
                    if isinstance(_selenium_info, dict):
                        _result_raw_event["selenium_info"] = {**_selenium_info, "value": _db_value}
                _result_raw_event["_effective_data_id"] = _db_data_id if _db_data_id is not None else step.data_id

                # Save to run_table
                try:
                    from django.utils import timezone as _tz
                    RunResult.objects.create(
                        run_id=run_id,
                        record_id=record_id,
                        step_no=step.step_no,
                        action=step.action,
                        page_url=step.page_url,
                        element_tag=step.element_tag,
                        locator_id=None,
                        data_id=_db_data_id if _db_data_id is not None else step.data_id,
                        raw_event=_result_raw_event,
                        status=run_status,
                        message=message,
                        runner=_runner,
                        author=step.recorder,
                        folder_name=folder_name,
                        parent_folder_id=_parent_folder_id,
                        sub_folder_id=_sub_folder_id,
                        end_folder_id=_end_folder_id,
                        run_date=_tz.now(),
                        screenshot=_ss_bytes,
                        steps_description=getattr(step, "steps_description", None),
                        page_title=getattr(step, "page_title", None),
                        engine='playwright',
                    )
                except Exception as db_exc:
                    print(f"[run_table] Failed to save step {step.step_no}: {db_exc}", flush=True)

                # Locator stats
                try:
                    if entry["ok"]:
                        _stat_strategy = _event_for_replay.get("_used_strategy") or _pri_strat or ""
                        _stat_locator = _event_for_replay.get("_used_locator") or _pri_loc or ""
                        _stat_rank = _event_for_replay.get("_used_rank")
                        _stat_is_primary = _event_for_replay.get("_is_primary")
                        _stat_rect = _event_for_replay.get("_element_rect") or {}
                        LocatorStat.objects.create(
                            record_id=record_id,
                            step_no=step.step_no,
                            action=step.action,
                            strategy=_stat_strategy,
                            locator=_stat_locator,
                            is_primary=bool(_stat_is_primary),
                            locator_rank=_stat_rank if isinstance(_stat_rank, int) else None,
                            run_id=run_id,
                            pos_x=_stat_rect.get("x"),
                            pos_y=_stat_rect.get("y"),
                            page_url=step.page_url,
                            runner=_runner,
                            author=step.recorder,
                            folder_name=folder_name,
                            created_at=_tz.now(),
                        )
                except Exception as _ls_exc:
                    print(f"[locators_stat] Failed to save step {step.step_no}: {_ls_exc}", flush=True)

                _executed_step_nos.add(step.step_no)
                results.append(entry)
                if on_step:
                    try:
                        on_step(entry)
                    except Exception:
                        pass

                # Stop the run immediately on the first failed step.
                # Remaining steps will be tagged as not_executed below.
                if not entry["ok"]:
                    _pw_logger.warning(
                        "run-aborted-on-failure  record_id=%s  step=%s  action=%s",
                        record_id, step.step_no, step.action,
                    )
                    print(
                        f"[playwright] Step {step.step_no} FAILED — stopping run. "
                        f"Remaining steps will be tagged as Not Executed.",
                        flush=True,
                    )
                    break

        finally:
            if not keep_open:
                context.close()
                if browser is not None:
                    browser.close()

    except BaseException as _exc:
        import traceback as _tb
        _pw_crash = _exc
        _pw_logger.error(
            "PLAYWRIGHT CRASH  record_id=%s  run_id=%s\n%s",
            record_id, run_id, _tb.format_exc(),
        )
    finally:
        # Mark any steps that were not reached (stopped early, aborted on
        # failure, or a browser crash) as not_executed.
        try:
            from django.utils import timezone as _tz
            for _ne_step in steps:
                if _ne_step.step_no not in _executed_step_nos:
                    try:
                        RunResult.objects.create(
                            run_id=run_id,
                            record_id=record_id,
                            step_no=_ne_step.step_no,
                            action=_ne_step.action,
                            page_url=_ne_step.page_url,
                            element_tag=_ne_step.element_tag,
                            locator_id=None,
                            data_id=None,
                            raw_event=_ne_step.raw_event,
                            status=RunResult.STATUS_NOT_EXECUTED,
                            message=None,
                            runner=_runner,
                            author=_ne_step.recorder,
                            folder_name=folder_name,
                            parent_folder_id=_parent_folder_id,
                            sub_folder_id=_sub_folder_id,
                            end_folder_id=_end_folder_id,
                            run_date=_tz.now(),
                            steps_description=getattr(_ne_step, 'steps_description', None),
                            page_title=getattr(_ne_step, 'page_title', None),
                            engine='playwright',
                        )
                    except Exception as _ne_exc:
                        print(f"[run_table] Failed to save not_executed step {_ne_step.step_no}: {_ne_exc}", flush=True)
        except Exception:
            pass

    # Recalculate is_primary for each step based on all-time locator_stat hits.
    try:
        from .locator_utils import update_primary_locators_from_stats
        update_primary_locators_from_stats(run_id)
    except Exception as _lpu_exc:
        print(f"[locators] is_primary update failed: {_lpu_exc}", flush=True)

    # Re-raise any crash that occurred inside the playwright context so the
    # caller (views.py _run()) can set job["status"] = "error" correctly.
    if _pw_crash is not None:
        raise _pw_crash

    return results

