"""
Selenium-based replay engine.

Supports pause and stop via threading.Event signals passed in from the caller.
"""

import os
import re
import time
import datetime
import logging
import logging.handlers
import threading
from typing import Any, Callable

from selenium import webdriver
from selenium.common.exceptions import (
    NoSuchElementException,
    WebDriverException,
    ElementNotInteractableException,
    ElementClickInterceptedException,
    InvalidElementStateException,
    StaleElementReferenceException,
    TimeoutException,
    UnexpectedAlertPresentException,
)
from selenium.webdriver.chrome.options import Options as ChromeOptions
from selenium.webdriver.chrome.service import Service as ChromeService
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.firefox.options import Options as FirefoxOptions
from selenium.webdriver.firefox.service import Service as FirefoxService
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait, Select as SeleniumSelect
from selenium.webdriver.support import expected_conditions as EC

try:
    from requests.exceptions import ConnectionError as RequestsConnectionError
except ImportError:
    RequestsConnectionError = OSError

try:
    from webdriver_manager.chrome import ChromeDriverManager
    _WDM_CHROME_AVAILABLE = True
except ImportError:
    _WDM_CHROME_AVAILABLE = False

try:
    from webdriver_manager.microsoft import EdgeChromiumDriverManager
    _WDM_EDGE_AVAILABLE = True
except ImportError:
    _WDM_EDGE_AVAILABLE = False

try:
    from webdriver_manager.firefox import GeckoDriverManager
    _WDM_FIREFOX_AVAILABLE = True
except ImportError:
    _WDM_FIREFOX_AVAILABLE = False

try:
    from django.db import connection as _db_connection
    from django.utils import timezone as _tz
    def _get_config(key: str, default: str = "") -> str:
        """Read a single value from app_config; returns *default* on any error."""
        try:
            with _db_connection.cursor() as _cur:
                _cur.execute("SELECT value FROM app_config WHERE key = %s", [key])
                _row = _cur.fetchone()
                return _row[0] if _row else default
        except Exception:
            return default
except ImportError:
    def _get_config(key: str, default: str = "") -> str:  # type: ignore[misc]
        return default

    class _tz:  # type: ignore[no-redef]
        @staticmethod
        def now():
            return datetime.datetime.utcnow()


# ---------------------------------------------------------------------------
# Driver helpers
# ---------------------------------------------------------------------------

def _clean_wd_msg(exc: Exception) -> str:
    """Return just the first meaningful line of a WebDriverException message,
    discarding the Chrome C++ stacktrace that follows 'Stacktrace:'."""
    msg = str(exc)
    # WebDriverException embeds "Message: ...\nStacktrace: ..."
    for marker in ("Stacktrace:", "\n  "):
        idx = msg.find(marker)
        if idx != -1:
            msg = msg[:idx].strip()
            break
    # Also strip the leading 'Message: ' prefix Selenium sometimes adds
    if msg.startswith("Message: "):
        msg = msg[len("Message: "):]
    return msg.strip()


NAV_RETRIES       = 2   # extra attempts after the first (overridden by app_config)
NAV_RETRY_WAIT    = 3   # seconds between retries (overridden by app_config)
_STEP_MAX_RETRIES = 2   # max per-step retries on transient failures (StaleElement etc.)

# ---------------------------------------------------------------------------
# Configurable replay timeouts — loaded from app_config at the start of each
# replay_session().  Module-level defaults are used if no config is found.
# ---------------------------------------------------------------------------
_STEP_TIMEOUT   = 10   # seconds — primary locator wait  (Pass 1)
_POLL_INTERVAL  = 0.5  # seconds — not directly used in Selenium (WebDriverWait handles it)
_STEP_RETRIES   = 2    # outer retry count per step  (1 + 2 = 3 total attempts)
_RETRY_DELAY    = 5    # seconds between outer retries
_STEP_SETTLE    = 0.3  # seconds — post-step settle time
_WINDOW_TIMEOUT = 15   # seconds — page-ready timeout
_NAV_RETRIES    = 2    # nav-level retry count
_NAV_RETRY_WAIT = 3    # seconds between nav retries


def _load_replay_config() -> dict:
    """Load replay timeout settings from app_config.  Returns a dict of overrides."""
    cfg = {}
    _keys = {
        "replay.step_timeout":    ("step_timeout",    10),
        "replay.overlay_timeout":  ("overlay_timeout", 60),
        "replay.poll_interval":   ("poll_interval",    0.5),
        "replay.step_retries":    ("step_retries",     2),
        "replay.retry_delay":     ("retry_delay",      5),
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


def _safe_navigate(driver: webdriver.Chrome, url: str) -> None:
    """Navigate the driver to *url* with retry on transient network errors."""
    last_exc: Exception | None = None
    for attempt in range(NAV_RETRIES + 1):
        try:
            driver.get(url)
            return
        except WebDriverException as exc:
            last_exc = exc
            msg = _clean_wd_msg(exc).lower()
            # SSL / certificate errors — these won't self-heal with retries;
            # log clearly so the user knows to add --ignore-certificate-errors.
            if any(e in msg for e in ("err_cert_", "err_ssl_", "net::err_cert",
                                       "ssl_protocol_error", "certificate")):
                _browser = _configured_selenium_browser()
                _browser_label = "Chrome" if _browser == "chrome" else ("Edge" if _browser == "msedge" else "Firefox")
                _replay_logger.error(
                    "ssl-cert-error  url=%s  "
                    "hint='check %s browser settings in /configuration/ and add the needed certificate-bypass arguments if supported'  "
                    "error=%s",
                    url, _browser_label, msg,
                )
                raise
            # Transient network errors — retry
            if any(e in msg for e in ("err_connection_timed_out", "err_name_not_resolved",
                                       "err_connection_refused", "err_internet_disconnected",
                                       "err_network_changed", "timeout")):
                if attempt < NAV_RETRIES:
                    time.sleep(NAV_RETRY_WAIT)
                    continue
            raise
    if last_exc:
        raise last_exc


_WEBDRIVERS_DIR         = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webdrivers")
_WEBDRIVERS_CHROME_DIR  = os.path.join(_WEBDRIVERS_DIR, "chrome")
_WEBDRIVERS_FIREFOX_DIR = os.path.join(_WEBDRIVERS_DIR, "firefox")
_WEBDRIVERS_EDGE_DIR    = os.path.join(_WEBDRIVERS_DIR, "edge")
_LOCATOR_LOG            = os.path.join(os.path.dirname(os.path.dirname(__file__)), "locator.log")
_LOGGER_LOG             = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logger.log")
_SCREENSHOTS_DIR        = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs", "screenshots")

# ---------------------------------------------------------------------------
# Module logger — writes to logger.log (rotating, 5 MB × 3 backups)
# All locator failures, fallbacks, and step pass/fail results land here.
# ---------------------------------------------------------------------------
_replay_logger = logging.getLogger("replay")
if not _replay_logger.handlers:  # avoid duplicate handlers on Django auto-reload
    _replay_logger.setLevel(logging.DEBUG)
    _log_fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-7s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _fh = logging.handlers.RotatingFileHandler(
        _LOGGER_LOG, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    _fh.setFormatter(_log_fmt)
    _replay_logger.addHandler(_fh)
    _replay_logger.propagate = False  # don't bubble up to Django root logger


def _get_recordings_folder_label() -> str:
    """Mirror of views._get_recordings_folder_label — reads config without importing views."""
    folder_name = _get_config("projects.recordings_folder_label", "Baseline").strip().replace("\\", "/")
    parts = [p.strip() for p in folder_name.split("/") if p.strip()]
    folder_name = "/".join(parts)
    if not folder_name or folder_name.lower() in {"baseline", "unfiled", ""}:
        return "Baseline"
    return folder_name


def _is_recordings_folder_name_local(folder_name: str) -> bool:
    """Lightweight check — matches the Baseline/Recordings root and any DB is_baseline alias."""
    val = (folder_name or "").strip().lower()
    if not val:
        return False
    label = _get_recordings_folder_label().lower()
    if val in {"records", "recordings", "baseline", label}:
        return True
    try:
        with _db_connection.cursor() as _cur:
            _cur.execute(
                "SELECT 1 FROM parent_folders WHERE is_baseline = TRUE AND LOWER(parent_folder) = %s LIMIT 1",
                [val],
            )
            return _cur.fetchone() is not None
    except Exception:
        return False


def _create_driver(headless: bool = False, rdp_port: int | None = None):
    browser_name = _configured_selenium_browser()
    if browser_name == "msedge":
        return _create_edge_driver(headless=headless, rdp_port=rdp_port), browser_name
    if browser_name == "firefox":
        return _create_firefox_driver(headless=headless, rdp_port=rdp_port), browser_name
    return _create_chrome_driver(headless=headless, rdp_port=rdp_port), browser_name


def _configured_selenium_browser() -> str:
    browser_name = (_get_config("recorder.browser", "chrome") or "chrome").strip().lower()
    return browser_name if browser_name in {"chrome", "firefox", "msedge"} else "chrome"


def _create_chrome_driver(headless: bool = False, rdp_port: int | None = None):
    options = ChromeOptions()
    if headless:
        options.add_argument("--headless=new")
    if rdp_port:
        options.add_argument(f"--remote-debugging-port={rdp_port}")
        options.add_experimental_option("detach", True)

    _extra = _get_config("chrome.extra_arguments", "")
    for _arg in (line.strip() for line in _extra.splitlines()):
        if _arg:
            options.add_argument(_arg)

    import json as _json
    for _line in _get_config("chrome.experimental_options", "").splitlines():
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

    _wd_filename = _get_config("chrome.webdriver_path", "").strip()
    if _wd_filename:
        _wd_path = os.path.join(_WEBDRIVERS_CHROME_DIR, _wd_filename)
        if os.path.isfile(_wd_path):
            try:
                return webdriver.Chrome(service=ChromeService(_wd_path), options=options)
            except WebDriverException as _e:
                print(f"[warning] Pinned chromedriver failed ({_e.msg.splitlines()[0]}), falling back to auto-detection.")

    try:
        return webdriver.Chrome(options=options)
    except WebDriverException:
        pass

    if _WDM_CHROME_AVAILABLE:
        try:
            service = ChromeService(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=options)
        except (RequestsConnectionError, Exception):
            pass

    return webdriver.Chrome(service=ChromeService("chromedriver"), options=options)


def _create_edge_driver(headless: bool = False, rdp_port: int | None = None):
    options = EdgeOptions()
    if headless:
        options.add_argument("--headless=new")
    if rdp_port:
        options.add_argument(f"--remote-debugging-port={rdp_port}")
        options.add_experimental_option("detach", True)

    _extra = _get_config("edge.extra_arguments", "")
    for _arg in (line.strip() for line in _extra.splitlines()):
        if _arg:
            options.add_argument(_arg)

    _wd_filename = _get_config("edge.webdriver_path", "").strip()
    if _wd_filename:
        _wd_path = os.path.join(_WEBDRIVERS_EDGE_DIR, _wd_filename)
        if os.path.isfile(_wd_path):
            try:
                return webdriver.Edge(service=EdgeService(_wd_path), options=options)
            except WebDriverException as _e:
                print(f"[warning] Pinned edgedriver failed ({_e.msg.splitlines()[0]}), falling back to auto-detection.")

    try:
        return webdriver.Edge(options=options)
    except WebDriverException:
        pass

    if _WDM_EDGE_AVAILABLE:
        try:
            return webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()), options=options)
        except (RequestsConnectionError, Exception):
            pass

    return webdriver.Edge(service=EdgeService("msedgedriver"), options=options)


def _create_firefox_driver(headless: bool = False, rdp_port: int | None = None):
    options = FirefoxOptions()
    if headless:
        options.add_argument("-headless")
    if rdp_port:
        _replay_logger.warning("firefox-replay  keep_open requested but replay add-step attach is only supported for Chromium-based browsers")

    _extra = _get_config("firefox.extra_arguments", "")
    for _arg in (line.strip() for line in _extra.splitlines()):
        if _arg:
            options.add_argument(_arg)

    _wd_filename = _get_config("firefox.webdriver_path", "").strip()
    if _wd_filename:
        _wd_path = os.path.join(_WEBDRIVERS_FIREFOX_DIR, _wd_filename)
        if os.path.isfile(_wd_path):
            try:
                return webdriver.Firefox(service=FirefoxService(_wd_path), options=options)
            except WebDriverException as _e:
                print(f"[warning] Pinned geckodriver failed ({_e.msg.splitlines()[0]}), falling back to auto-detection.")

    try:
        return webdriver.Firefox(options=options)
    except WebDriverException:
        pass

    if _WDM_FIREFOX_AVAILABLE:
        try:
            return webdriver.Firefox(service=FirefoxService(GeckoDriverManager().install()), options=options)
        except (RequestsConnectionError, Exception):
            pass

    return webdriver.Firefox(service=FirefoxService("geckodriver"), options=options)


# ---------------------------------------------------------------------------
# Locator resolution
# ---------------------------------------------------------------------------

def _unescape_css_id(val: str) -> str:
    """Convert a CSS-escaped id (e.g. 'foo\\:bar') to the raw DOM id ('foo:bar')."""
    # Replace CSS hex escapes like \26 or \000026 ? chr(0x26)
    val = re.sub(r'\\([0-9a-fA-F]{1,6})\s?', lambda m: chr(int(m.group(1), 16)), val)
    # Replace simple backslash escapes like \: ? :
    val = re.sub(r'\\(.)', r'\1', val)
    return val


def _looks_like_full_selector(value: str) -> bool:
    """Return True if *value* is already a complete CSS selector or XPath expression
    rather than a bare attribute value (e.g. 'input[name="foo"]' vs 'foo')."""
    v = value.strip()
    if v.startswith(("//", "./", "(//")):   # XPath
        return True
    # CSS with structural characters: tag[attr], [attr=val], .class, #id path, descendant >
    return bool(re.search(r'\[|>|~|\+|^#|^\.\w', v))


_STRATEGY_MAP: dict[str, Any] = {
    "xpath":           By.XPATH,
    "id":              By.ID,
    "label":           By.XPATH,
    "name":            By.CSS_SELECTOR,      # stored as "tag[name=...]"
    "value":           By.CSS_SELECTOR,      # wrapped as [value="..."]
    "placeholder":     By.CSS_SELECTOR,      # wrapped as [placeholder="..."]
    "class":           By.CLASS_NAME,
    "className":       By.CLASS_NAME,
    "tagName":         By.TAG_NAME,
    "css":             By.CSS_SELECTOR,
    "href":            By.CSS_SELECTOR,      # wrapped as [href="..."]
    "text":            By.XPATH,             # wrapped as //*[normalize-space(text())="..."]
    "linkText":        By.LINK_TEXT,
    "partialLinkText": By.PARTIAL_LINK_TEXT,
    "type":            By.CSS_SELECTOR,      # wrapped as [type="..."]
    "role":            By.CSS_SELECTOR,      # wrapped as [role="..."]
    "title":           By.CSS_SELECTOR,      # wrapped as [title="..."]
    "alt":             By.CSS_SELECTOR,      # wrapped as [alt="..."]
    "ariaLabel":       By.CSS_SELECTOR,      # stored as "tag[aria-label=...]"
    "dataTestId":      By.CSS_SELECTOR,      # stored as "[data-testid=..."]
}

# Strategies whose raw value must be wrapped into a selector before use
_ATTR_WRAP: dict[str, Any] = {
    "value":       lambda v: f'[value="{v}"]',
    "placeholder": lambda v: f'[placeholder="{v}"]',
    "type":        lambda v: f'[type="{v}"]',
    "role":        lambda v: f'[role="{v}"]',
    "title":       lambda v: f'[title="{v}"]',
    "alt":         lambda v: f'[alt="{v}"]',
    "href":        lambda v: f'[href="{v}"]',
    "text":        lambda v: f'//*[normalize-space(text())="{v}"]',
}


def _xpath_literal(value: str) -> str:
    """Return a safe XPath string literal."""
    if "'" not in value:
        return f"'{value}'"
    if '"' not in value:
        return f'"{value}"'
    parts = value.split("'")
    return "concat(" + ", \"'\", ".join(_xpath_literal(part) for part in parts) + ")"


def _semantic_locators(raw_event: dict[str, Any]) -> list[tuple[Any, str, str]]:
    """Build semantic fallback locators from enriched Selenium recorder metadata."""
    candidates: list[tuple[Any, str, str]] = []
    seen: set[tuple[Any, str]] = set()

    def _add(by: Any, value: str, label: str) -> None:
        key = (by, value)
        if not value or key in seen:
            return
        seen.add(key)
        candidates.append((by, value, label))

    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = info.get("attributes") if isinstance(info.get("attributes"), dict) else {}
    tag = str(raw_event.get("tag") or info.get("tagName") or "").strip().lower()
    text = str(raw_event.get("text") or "").strip()
    value = str(raw_event.get("value") or info.get("value") or "").strip()
    input_type = str(raw_event.get("inputType") or raw_event.get("type") or attrs.get("type") or "").strip().lower()
    locators = raw_event.get("locators") if isinstance(raw_event.get("locators"), dict) else {}
    accessible_name = str(info.get("accessibleName") or info.get("labelText") or locators.get("label") or attrs.get("aria-label") or "").strip()

    if tag == "button" and text:
        lit = _xpath_literal(text)
        _add(By.XPATH, f"(//button[normalize-space()={lit}] | //input[(@type='submit' or @type='button' or @type='reset') and @value={lit}] | //*[@role='button' and normalize-space()={lit}])[1]", f"role-button:{text}")

    if tag in ("input", "textarea") and input_type not in ("checkbox", "radio", "submit", "button", "reset", "hidden") and accessible_name:
        lit = _xpath_literal(accessible_name)
        _add(By.XPATH, f"(//*[@aria-label={lit}] | //*[@id=(//label[normalize-space()={lit}]/@for)] | //label[normalize-space()={lit}]//*[self::input or self::textarea])[1]", f"label-textbox:{accessible_name}")

    if tag == "select" and accessible_name:
        lit = _xpath_literal(accessible_name)
        _add(By.XPATH, f"(//*[@aria-label={lit}] | //*[@id=(//label[normalize-space()={lit}]/@for)] | //label[normalize-space()={lit}]//*[self::select])[1]", f"label-combobox:{accessible_name}")

    if input_type == "checkbox" and accessible_name:
        lit = _xpath_literal(accessible_name)
        _add(By.XPATH, f"(//*[@aria-label={lit} and self::input[@type='checkbox']] | //*[@id=(//label[normalize-space()={lit}]/@for)] | //label[normalize-space()={lit}]//*[self::input[@type='checkbox']])[1]", f"label-checkbox:{accessible_name}")

    if input_type == "radio" and accessible_name:
        lit = _xpath_literal(accessible_name)
        _add(By.XPATH, f"(//*[@aria-label={lit} and self::input[@type='radio']] | //*[@id=(//label[normalize-space()={lit}]/@for)] | //label[normalize-space()={lit}]//*[self::input[@type='radio']])[1]", f"label-radio:{accessible_name}")

    if text:
        lit = _xpath_literal(text)
        role_hint = str(attrs.get("role") or "").strip().lower()
        if role_hint in {"cell", "gridcell"} or tag in {"td", "th"}:
            _add(By.XPATH, f"(//*[@role='cell' and normalize-space()={lit}] | //td[normalize-space()={lit}] | //th[normalize-space()={lit}])[1]", f"role-cell:{text}")
        if tag == "a":
            _add(By.XPATH, f"(//a[normalize-space()={lit}])[1]", f"link:{text}")
        if tag in {"span", "div", "label"}:
            _add(By.XPATH, f"//*[normalize-space()={lit}][1]", f"text:{text}")

    if input_type in ("submit", "button", "reset") and value:
        lit = _xpath_literal(value)
        _add(By.XPATH, f"(//input[(@type='submit' or @type='button' or @type='reset') and @value={lit}])[1]", f"input-value:{value}")

    return candidates


def _open_parent_dropdown(driver: webdriver.Chrome, el: Any) -> None:
    """If *el* lives inside a closed Bootstrap dropdown, open the toggle button."""
    try:
        driver.execute_script(
            """
            var el = arguments[0];
            var parent = el.closest('.dropdown, .dropup, .dropend, .dropstart,
                                     .nav-item, [class*="dropdown"]');
            if (parent) {
                var toggle = parent.querySelector(
                    '[data-bs-toggle="dropdown"], [data-toggle="dropdown"],
                     .dropdown-toggle, [aria-haspopup="true"]'
                );
                if (toggle && toggle !== el) { toggle.click(); }
            }
            """,
            el,
        )
        time.sleep(0.35)
    except Exception:
        pass


# Pass 1  primary (xpath / DB is_primary / auto-derived), 10 s
# Pass 2  all strategies, 5 s each
# Pass 3  dropdown-reveal + all strategies, 3 s each
# Coordinate fallback
# Raise only if everything above fails
def _find_element(
    driver: webdriver.Chrome,
    raw_event: dict[str, Any],
    condition=None,
    primary_timeout: int = 30,
    fallback_timeout: int = 30,
    presence_timeout: int = 3,
    reveal_timeout: int = 5,
) -> tuple[Any, str, int]:
    """
    Try locators in DB order (is_primary=True first, then locator_rank asc).
    Falls back to recorded coordinates if all strategies fail (rank=0).
    Returns (element, strategy_used, rank_used).
    """
    if condition is None:
        condition = EC.element_to_be_clickable
    locators: dict = raw_event.get("locators") or {}
    expected_tag: str | None = (raw_event.get("tag") or "").lower() or None
    _static_ordered = ("xpath", "id", "name", "value", "placeholder", "class", "className",
                       "tagName", "css", "href", "text", "label", "linkText", "partialLinkText",
                       "type", "role", "title", "alt", "ariaLabel", "dataTestId")

    def _prep(strategy: str, raw_val: str) -> tuple[Any, str]:
        """Return (by, value) ready for WebDriverWait."""
        # If the stored value is already a full CSS/XPath expression, bypass
        # strategy-specific wrapping (e.g. 'input[name="username"]' stored under 'name').
        _bypass_strategies = {"xpath", "css", "text", "linkText", "partialLinkText",
                               "role", "tagName", "class", "className"}
        if strategy not in _bypass_strategies and _looks_like_full_selector(raw_val):
            v = raw_val.strip()
            if v.startswith(("//", "./", "(//")):
                return By.XPATH, v
            return By.CSS_SELECTOR, v

        by = _STRATEGY_MAP.get(strategy, By.CSS_SELECTOR)
        val = raw_val
        if strategy == "label":
            label_for = str(val or "").strip()
            if label_for.lower().startswith("for="):
                label_for = label_for[4:].strip()
                if label_for:
                    return By.CSS_SELECTOR, f'label[for="{label_for}"]'
            literal = _xpath_literal(val)
            input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").strip().lower()
            if input_type in ("checkbox", "radio"):
                return By.XPATH, (
                    f"(//*[@aria-label={literal} and self::input[@type='{input_type}']]"
                    f" | //*[@id=(//label[normalize-space()={literal}]/@for)]"
                    f" | //label[normalize-space()={literal}]//*[self::input[@type='{input_type}']])[1]"
                )
            tag = str(raw_event.get("tag") or "").strip().lower()
            if tag == "select":
                return By.XPATH, f"(//*[@aria-label={literal}] | //*[@id=(//label[normalize-space()={literal}]/@for)] | //label[normalize-space()={literal}]//*[self::select])[1]"
            if tag in ("input", "textarea"):
                return By.XPATH, f"(//*[@aria-label={literal}] | //*[@id=(//label[normalize-space()={literal}]/@for)] | //label[normalize-space()={literal}]//*[self::input or self::textarea])[1]"
            return By.XPATH, f"(//label[normalize-space()={literal}] | //*[normalize-space()={literal}])[1]"
        if by == By.ID:
            if val.startswith("#"):
                val = val[1:]
            val = _unescape_css_id(val)
        if strategy in _ATTR_WRAP:
            val = _ATTR_WRAP[strategy](val)
        return by, val

    def _store_rect(el) -> None:
        try:
            _r = driver.execute_script(
                "var r=arguments[0].getBoundingClientRect();"
                "return {"
                "docX:r.left+window.scrollX,docY:r.top+window.scrollY,"
                "viewX:r.left,viewY:r.top,w:r.width,h:r.height"
                "};", el)
            raw_event["_element_rect"] = _r
            raw_event["_viewport_rect"] = {
                "x": _r.get("viewX"),
                "y": _r.get("viewY"),
                "w": _r.get("w"),
                "h": _r.get("h"),
            }
        except Exception:
            pass

    # Build the ordered candidate list:
    # - If _db_locators injected: is_primary=True entry leads, rest follow by rank
    # - Otherwise: fall back to the static strategy order
    db_locators: list[dict] = raw_event.get("_db_locators") or []
    if db_locators:
        ordered_pairs: list[tuple[str, str, int, bool]] = [
            (e["strategy"], e["locator"], e["rank"], e.get("is_primary", False))
            for e in db_locators
            if e.get("strategy") and e.get("locator")
        ]
    else:
        ordered_pairs = [
            (s, str(locators[s]), rank, rank == 1)
            for rank, s in enumerate(_static_ordered, start=1)
            if locators.get(s)
        ]

    record_id = raw_event.get("_record_id", "?")
    step_no   = raw_event.get("_step_no",   "?")

    # --- Pass 1: first entry (is_primary=True or static rank-1) — 10 s ---
    if ordered_pairs:
        p_strat, p_val, p_rank, p_primary = ordered_pairs[0]
        _by, _prep_val = _prep(p_strat, p_val)
        try:
            el = WebDriverWait(driver, primary_timeout).until(condition((_by, _prep_val)))
            actual_tag = el.tag_name.lower()
            if expected_tag and actual_tag != expected_tag:
                print(f"[replay] Tag mismatch: expected={expected_tag!r} got={actual_tag!r} "
                      f"(strategy={p_strat!r}) proceeding anyway", flush=True)
            raw_event["_used_strategy"] = p_strat
            raw_event["_used_locator"]  = p_val
            raw_event["_is_primary"]    = p_primary
            raw_event["_used_rank"]     = p_rank
            _store_rect(el)
            return el, p_strat, p_rank
        except (NoSuchElementException, TimeoutException):
            _replay_logger.warning(
                "primary-locator-failed  strategy=%r  locator=%r  is_primary=%s  record_id=%s  step=%s",
                p_strat, p_val, p_primary, record_id, step_no,
            )
            print(f"[replay] Primary locator failed "
                  f"(strategy={p_strat!r} is_primary={p_primary}); trying fallbacks.", flush=True)

    # --- Pass 2: remaining locators in rank order (5 s each) ---
    for strat, val, rank, is_p in ordered_pairs[1:]:
        by, prep_val = _prep(strat, val)
        try:
            el = WebDriverWait(driver, fallback_timeout).until(condition((by, prep_val)))
            actual_tag = el.tag_name.lower()
            if expected_tag and actual_tag != expected_tag:
                print(f"[replay] Tag mismatch: expected={expected_tag!r} got={actual_tag!r} "
                      f"(strategy={strat!r}) proceeding anyway", flush=True)
            print(f"[replay] Fallback succeeded; rank={rank} strategy={strat!r}", flush=True)
            raw_event["_used_strategy"] = strat
            raw_event["_used_locator"]  = val
            raw_event["_is_primary"]    = is_p
            raw_event["_used_rank"]     = rank
            _store_rect(el)
            return el, strat, rank
        except (NoSuchElementException, TimeoutException, StaleElementReferenceException):
            _replay_logger.debug(
                "fallback-miss  rank=%s  strategy=%r  locator=%r  record_id=%s  step=%s",
                rank, strat, val, record_id, step_no,
            )
            continue

    # --- Pass 3: semantic fallbacks (label/text/role heuristics) ---
    for by, prep_val, label in _semantic_locators(raw_event):
        try:
            el = WebDriverWait(driver, fallback_timeout).until(condition((by, prep_val)))
            actual_tag = el.tag_name.lower()
            if expected_tag and actual_tag != expected_tag:
                print(f"[replay] Tag mismatch: expected={expected_tag!r} got={actual_tag!r} "
                      f"(strategy={label!r}) proceeding anyway", flush=True)
            print(f"[replay] Semantic fallback succeeded; strategy={label!r}", flush=True)
            raw_event["_used_strategy"] = label
            raw_event["_used_locator"] = prep_val
            raw_event["_is_primary"] = False
            raw_event["_used_rank"] = 0
            _store_rect(el)
            return el, label, 0
        except (NoSuchElementException, TimeoutException, StaleElementReferenceException):
            _replay_logger.debug(
                "semantic-miss  label=%r  locator=%r  record_id=%s  step=%s",
                label, prep_val, record_id, step_no,
            )
            continue

    # --- Pass 4: dropdown reveal (presence → open dropdown → condition) ---
    for strat, val, rank, is_p in ordered_pairs:
        by, prep_val = _prep(strat, val)
        try:
            el = WebDriverWait(driver, presence_timeout).until(
                EC.presence_of_element_located((by, prep_val))
            )
            actual_tag = el.tag_name.lower()
            if expected_tag and actual_tag != expected_tag:
                print(f"[replay] Tag mismatch (pass3): expected={expected_tag!r} got={actual_tag!r} "
                      f"(strategy={strat!r}) proceeding anyway", flush=True)
            _open_parent_dropdown(driver, el)
            try:
                el = WebDriverWait(driver, reveal_timeout).until(condition((by, prep_val)))
            except (NoSuchElementException, TimeoutException):
                pass
            print(f"[replay] Dropdown-reveal used; rank={rank} strategy={strat!r}", flush=True)
            raw_event["_used_strategy"] = strat
            raw_event["_used_locator"]  = val
            raw_event["_is_primary"]    = is_p
            raw_event["_used_rank"]     = rank
            _store_rect(el)
            return el, strat, rank
        except (NoSuchElementException, TimeoutException, StaleElementReferenceException):
            _replay_logger.debug(
                "dropdown-miss  rank=%s  strategy=%r  locator=%r  record_id=%s  step=%s",
                rank, strat, val, record_id, step_no,
            )
            continue

    # --- Coordinate fallback ---
    pos_x = raw_event.get("pos_x")
    pos_y = raw_event.get("pos_y")
    if pos_x is not None and pos_y is not None:
        el = driver.execute_script(
            """
            window.scrollTo(0, arguments[1] - window.innerHeight / 2);
            return document.elementFromPoint(
                arguments[0] - window.scrollX,
                arguments[1] - window.scrollY
            );
            """,
            float(pos_x), float(pos_y),
        )
        if el:
            # Validate: the element at these coordinates must match the
            # expected tag.  On a blank/error page elementFromPoint returns
            # <body>/<html> which would cause a false-positive "pass".
            _expected_tag = (raw_event.get("tag") or "").lower()
            _found_tag = ""
            try:
                _found_tag = el.tag_name.lower()
            except Exception:
                pass
            if _expected_tag and _found_tag and _found_tag != _expected_tag:
                _replay_logger.warning(
                    "coordinate-fallback-tag-mismatch  expected=%s  found=%s  "
                    "pos_x=%s  pos_y=%s  record_id=%s  step=%s",
                    _expected_tag, _found_tag, pos_x, pos_y,
                    raw_event.get("_record_id", "?"), raw_event.get("_step_no", "?"),
                )
            else:
                print("[replay] All locator strategies failed; using coordinate fallback.", flush=True)
                _replay_logger.warning(
                    "coordinate-fallback  pos_x=%s  pos_y=%s  record_id=%s  step=%s",
                    pos_x, pos_y,
                    raw_event.get("_record_id", "?"), raw_event.get("_step_no", "?"),
                )
                raw_event["_used_strategy"] = "coordinates"
                raw_event["_used_locator"]  = f"{pos_x},{pos_y}"
                raw_event["_is_primary"]    = False
                raw_event["_used_rank"]     = 0
                try:
                    _r = driver.execute_script(
                        "var r=arguments[0].getBoundingClientRect();"
                        "return {"
                        "docX:r.left+window.scrollX,docY:r.top+window.scrollY,"
                        "viewX:r.left,viewY:r.top,w:r.width,h:r.height"
                        "};", el)
                    raw_event["_element_rect"] = _r
                    raw_event["_viewport_rect"] = {
                        "x": _r.get("viewX"),
                        "y": _r.get("viewY"),
                        "w": _r.get("w"),
                        "h": _r.get("h"),
                    }
                except Exception:
                    pass
                return el, "coordinates", 0
    _replay_logger.error(
        "element-not-found  all-strategies-exhausted  locators=%r  record_id=%s  step=%s",
        locators,
        raw_event.get("_record_id", "?"), raw_event.get("_step_no", "?"),
    )
    raise NoSuchElementException(
        f"Could not locate element by any strategy or coordinates. Locators: {locators}"
    )


def _dismiss_modal(driver: webdriver.Chrome) -> None:
    """Try to close any visible modal overlay so the next click is not intercepted."""
    # 1. Try pressing Escape  most Bootstrap/custom modals honour it
    try:
        from selenium.webdriver.common.keys import Keys as _Keys
        webdriver.ActionChains(driver).send_keys(_Keys.ESCAPE).perform()
        time.sleep(0.3)
    except Exception:
        pass
    # 2. Try clicking a close/dismiss button inside any visible modal
    for selector in (
        ".modal.show [data-dismiss='modal']",
        ".modal.show .btn-close",
        ".modal.show .close",
        ".modal[style*='display: block'] [data-dismiss='modal']",
        ".modal[style*='display: block'] .btn-close",
    ):
        try:
            btn = driver.find_element(By.CSS_SELECTOR, selector)
            driver.execute_script("arguments[0].click();", btn)
            time.sleep(0.3)
            return
        except Exception:
            continue
    # 3. Try removing modal backdrop via JS as last resort
    try:
        driver.execute_script("""
            document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
            document.querySelectorAll('.modal.show').forEach(el => {
                el.classList.remove('show');
                el.style.display = 'none';
            });
            document.body.classList.remove('modal-open');
        """)
        time.sleep(0.2)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Page readiness / element visibility helpers
# ---------------------------------------------------------------------------

_PAGE_READY_TIMEOUT    = 15   # seconds to wait for document.readyState == 'complete'
_ELEMENT_VISIBLE_TIMEOUT = 10  # seconds to wait for element visibility after it is found


def _wait_for_page_ready(driver: webdriver.Chrome, timeout: int = _PAGE_READY_TIMEOUT) -> None:
    """Block until document.readyState == 'complete'.  Logs a warning (does not raise) on timeout."""
    try:
        WebDriverWait(driver, timeout).until(
            lambda d: d.execute_script("return document.readyState") == "complete"
        )
    except TimeoutException:
        _replay_logger.warning(
            "page-not-ready: document.readyState did not reach 'complete' within %ss", timeout
        )


def _wait_for_visible(driver: webdriver.Chrome, el: Any, timeout: int = _ELEMENT_VISIBLE_TIMEOUT) -> None:
    """Scroll *el* into the viewport then wait until it is visible.  Logs a warning on timeout."""
    try:
        driver.execute_script(
            "arguments[0].scrollIntoView({block:'center', behavior:'instant'});", el
        )
        WebDriverWait(driver, timeout).until(EC.visibility_of(el))
    except TimeoutException:
        _replay_logger.warning(
            "element-not-visible: element did not become visible within %ss", timeout
        )


_IN_PROGRESS_OVERLAY_TIMEOUT = 60  # seconds — JSF #inProgressPage processing ceiling


def _wait_for_in_progress_overlay(
    driver: webdriver.Chrome,
    timeout: int = _IN_PROGRESS_OVERLAY_TIMEOUT,
) -> None:
    """Wait until the JSF #inProgressPage blocking overlay is gone.

    Also handles generic variants: any element whose id starts with
    'inProgress' or whose computed display/visibility hides content.
    Silently returns when the element is absent or hidden.  Logs a
    warning (does not raise) when the timeout is reached.

    Two-phase approach:
    1. Quick JS check — if no inProgress* element is present at all, return
       instantly (avoids the implicit-wait penalty on non-JSF pages).
    2. If an overlay IS present and visible, poll until it disappears or timeout.
    """
    # Fast-path: JS check avoids the implicit_wait penalty on pages without the overlay.
    _OVERLAY_PRESENT_JS = """
        var selectors = ['#inProgressPage', '[id^="inProgress"]'];
        for (var i = 0; i < selectors.length; i++) {
            var el = document.querySelector(selectors[i]);
            if (!el) continue;
            var st = window.getComputedStyle(el);
            if (st.display === 'none' || st.visibility === 'hidden') continue;
            if (Number(st.opacity || '1') === 0) continue;
            var r = el.getBoundingClientRect();
            if (r.width >= 4 && r.height >= 4) return true;
        }
        return false;
    """
    try:
        visible = driver.execute_script(_OVERLAY_PRESENT_JS)
    except Exception:
        visible = False
    if not visible:
        return  # No overlay — skip entirely (instant)

    # Overlay IS visible — poll until it disappears or timeout.
    deadline = time.monotonic() + timeout
    while True:
        try:
            visible = driver.execute_script(_OVERLAY_PRESENT_JS)
        except Exception:
            visible = False
        if not visible:
            return
        if time.monotonic() >= deadline:
            _replay_logger.warning(
                "in-progress-overlay-timeout: #inProgressPage still visible after %ss", timeout
            )
            return
        time.sleep(0.25)


def _url_base_path(url: str) -> str:
    """Strip fragment, query string, and JSF path params from a URL."""
    if not url:
        return ""
    url = url.split("#", 1)[0]
    url = url.split("?", 1)[0]
    url = url.split(";", 1)[0]
    return url.rstrip("/")


def _dismiss_alert(driver: webdriver.Chrome) -> None:
    """Accept/dismiss any pending browser alert, confirm or prompt dialog.

    Called proactively before every action so that an unexpected dialog
    (e.g. "Leave site?" after a previous click) never blocks the next step.
    Silently swallowed if no alert is present.
    """
    try:
        alert = driver.switch_to.alert
        try:
            alert.dismiss()
        except Exception:
            try:
                alert.accept()
            except Exception:
                pass
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Pre-action screenshot helper
# ---------------------------------------------------------------------------

def _capture_pre_screenshot(driver: webdriver.Chrome, raw_event: dict[str, Any]) -> None:
    _el_rect = raw_event.get("_viewport_rect") or raw_event.get("_element_rect")
    try:
        if _el_rect:
            try:
                driver.execute_script("""
                    var d = document.createElement('div');
                    d.id = '__ss_hl__';
                    d.style.cssText =
                        'position:fixed;border:3px solid red;'
                        + 'background:rgba(255,0,0,0.15);'
                        + 'z-index:2147483647;pointer-events:none;box-sizing:border-box;'
                        + 'left:'  + arguments[0] + 'px;'
                        + 'top:'   + arguments[1] + 'px;'
                        + 'width:' + arguments[2] + 'px;'
                        + 'height:'+ arguments[3] + 'px;';
                    document.documentElement.appendChild(d);
                """, _el_rect['x'], _el_rect['y'], _el_rect['w'], _el_rect['h'])
            except Exception:
                pass
        raw_event["_pre_ss_bytes"] = driver.get_screenshot_as_png()
        if _el_rect:
            try:
                driver.execute_script(
                    "var e=document.getElementById('__ss_hl__');if(e)e.remove();")
            except Exception:
                pass
    except Exception:
        pass


def _wait_for_same_page_next_step_target(
    driver: webdriver.Chrome,
    current_page_url: str,
    current_raw_event: dict[str, Any],
    next_step,
) -> None:
    """Best-effort stabilization for same-page AJAX/UI updates."""
    if next_step is None or not getattr(next_step, "raw_event", None):
        return
    expected = _url_base_path(current_page_url or "")
    if _url_base_path(getattr(next_step, "page_url", "") or "") != expected:
        return
    if _url_base_path(driver.current_url or "") != expected:
        return
    try:
        _next_raw = dict(next_step.raw_event or {})
        _next_raw["_record_id"] = current_raw_event.get("_record_id", "?")
        _next_raw["_step_no"] = getattr(next_step, "step_no", "?")
        _find_element(
            driver,
            _next_raw,
            EC.presence_of_element_located,
            primary_timeout=30,
            fallback_timeout=30,
            presence_timeout=3,
            reveal_timeout=2,
        )
    except Exception:
        pass


def _resolve_toggle_control(driver: webdriver.Chrome, current_el: Any) -> Any:
    """Resolve a visible label/container click target back to the checkbox/radio control."""
    try:
        resolved = driver.execute_script(
            """
            var el = arguments[0];
            if (!el) return null;
            if (el.matches && el.matches('input[type="checkbox"], input[type="radio"]')) {
                return el;
            }
            var control = null;
            var label = el.closest ? el.closest('label') : null;
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
                var container = el.closest ? el.closest('.checkbox, .radio, [role="checkbox"], [role="radio"]') : null;
                if (container) {
                    control = container.querySelector('input[type="checkbox"], input[type="radio"]');
                }
            }
            return control || el;
            """,
            current_el,
        )
    except Exception:
        return current_el
    return resolved or current_el


def _toggle_label_candidates(raw_event: dict[str, Any]) -> list[str]:
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

    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    locators = raw_event.get("locators") if isinstance(raw_event.get("locators"), dict) else {}
    attrs = info.get("attributes") if isinstance(info.get("attributes"), dict) else {}
    _add(info.get("accessibleName") or "")
    _add(info.get("labelText") or "")
    _add(locators.get("label") or "")
    _add(attrs.get("aria-label") or "")
    return candidates


def _toggle_target_id(raw_event: dict[str, Any]) -> str:
    locators = raw_event.get("locators") if isinstance(raw_event.get("locators"), dict) else {}
    label_locator = str(locators.get("label") or "").strip()
    if label_locator.lower().startswith("for="):
        return label_locator[4:].strip()
    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = info.get("attributes") if isinstance(info.get("attributes"), dict) else {}
    return str(raw_event.get("id") or info.get("id") or attrs.get("id") or "").strip()


def _toggle_autosubmit_enabled(raw_event: dict[str, Any] | None) -> bool:
    if not isinstance(raw_event, dict):
        return False
    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = info.get("attributes") if isinstance(info.get("attributes"), dict) else {}
    return str(attrs.get("data-autosubmit") or "").strip().lower() in {"true", "1", "yes", "on"}


def _prefer_label_toggle_click(raw_event: dict[str, Any] | None) -> bool:
    if not isinstance(raw_event, dict):
        return False
    if str(raw_event.get("_used_strategy") or "").strip().lower() == "label":
        return True
    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    state = info.get("state") if isinstance(info.get("state"), dict) else {}
    return state.get("visible") is False


def _refresh_toggle_control(driver: webdriver.Chrome, current_el: Any, raw_event: dict[str, Any] | None = None) -> Any:
    target_id = _toggle_target_id(raw_event or {})
    if target_id:
        try:
            return _resolve_toggle_control(driver, driver.find_element(By.ID, target_id))
        except Exception:
            pass
    return _resolve_toggle_control(driver, current_el)


def _click_toggle_label(driver: webdriver.Chrome, raw_event: dict[str, Any]) -> bool:
    """Try clicking a visible label or text node for a checkbox/radio control."""
    target_id = _toggle_target_id(raw_event)
    if target_id:
        try:
            explicit_label = driver.find_element(By.CSS_SELECTOR, f'label[for="{target_id}"]')
            for _click in (
                lambda: explicit_label.click(),
                lambda: driver.execute_script("arguments[0].click();", explicit_label),
                lambda: ActionChains(driver).move_to_element(explicit_label).click().perform(),
            ):
                try:
                    _click()
                    return True
                except Exception:
                    continue
        except Exception:
            pass

    for label in _toggle_label_candidates(raw_event):
        literal = _xpath_literal(label)
        xpath_candidates = (
            f"(//label[normalize-space()={literal}])[1]",
            f"(//*[normalize-space()={literal}])[1]",
        )
        for xpath in xpath_candidates:
            try:
                label_el = driver.find_element(By.XPATH, xpath)
            except Exception:
                continue
            for _click in (
                lambda: label_el.click(),
                lambda: driver.execute_script("arguments[0].click();", label_el),
                lambda: ActionChains(driver).move_to_element(label_el).click().perform(),
            ):
                try:
                    _click()
                    return True
                except Exception:
                    continue
    return False


def _set_toggle_state(driver: webdriver.Chrome, current_el: Any, expected_checked: bool | None, raw_event: dict[str, Any] | None = None) -> bool:
    """Best-effort checkbox/radio toggle that returns the final selected state."""
    toggle_el = _resolve_toggle_control(driver, current_el)
    autosubmit = _toggle_autosubmit_enabled(raw_event)
    prefer_label = _prefer_label_toggle_click(raw_event)

    def _selected() -> bool:
        nonlocal toggle_el
        try:
            return bool(toggle_el.is_selected())
        except Exception:
            try:
                return bool(driver.execute_script("return !!arguments[0].checked;", toggle_el))
            except Exception:
                try:
                    toggle_el = _refresh_toggle_control(driver, toggle_el, raw_event)
                    return bool(driver.execute_script("return !!arguments[0].checked;", toggle_el))
                except Exception:
                    return False

    before = _selected()
    target = before if expected_checked is None else bool(expected_checked)
    if before == target:
        return before

    click_errors: list[Exception] = []
    click_attempts = [
        lambda: toggle_el.click(),
        lambda: driver.execute_script("arguments[0].click();", toggle_el),
        lambda: ActionChains(driver).move_to_element(toggle_el).click().perform(),
        lambda: _click_toggle_label(driver, raw_event or {}),
    ]
    if prefer_label:
        click_attempts = [click_attempts[-1], *click_attempts[:-1]]

    for _click in click_attempts:
        try:
            result = _click()
        except Exception as exc:
            click_errors.append(exc)
        else:
            if result is False:
                click_errors.append(ElementNotInteractableException("Visible checkbox label was not clickable"))
        if autosubmit:
            try:
                _wait_for_in_progress_overlay(driver)
            except Exception:
                pass
            toggle_el = _refresh_toggle_control(driver, toggle_el, raw_event)
        if _selected() == target:
            return target

    try:
        final_state = driver.execute_script(
            """
            var el = arguments[0], expected = arguments[1];
            if (!el) return false;
            el.checked = !!expected;
            el.dispatchEvent(new Event('input', {bubbles:true, cancelable:true}));
            el.dispatchEvent(new Event('change', {bubbles:true, cancelable:true}));
            return !!el.checked;
            """,
            toggle_el,
            target,
        )
        if bool(final_state) == target:
            return target
    except Exception as exc:
        click_errors.append(exc)

    if click_errors:
        raise ElementNotInteractableException(_clean_wd_msg(click_errors[-1]))
    raise ElementNotInteractableException("Toggle state did not change")


def _recorded_step_delay(raw_event: dict[str, Any] | None, max_delay: float = 2.0) -> float:
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
        # Legacy plain-text: exact match
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
        # Unknown category — fallback to exact match on value1
        passed = (actual == value1)

    if passed:
        return True, ""
    detail = f"expected '{rule}' but got '{actual_text[:120]}'"
    return False, detail


def _select_current_option(el: Any) -> tuple[str, str]:
    try:
        select_widget = SeleniumSelect(el)
        option = select_widget.first_selected_option
        return str(option.get_attribute("value") or ""), str(option.text or "").strip()
    except Exception:
        try:
            value = str(el.get_attribute("value") or "")
        except Exception:
            value = ""
        return value, ""


def _set_select_value(driver: webdriver.Chrome, el: Any, raw_event: dict[str, Any]) -> str:
    text = str(raw_event.get("text") or "").strip()
    value = str(raw_event.get("value") or "").strip()
    select_widget = SeleniumSelect(el)

    if text:
        try:
            select_widget.select_by_visible_text(text)
            return text
        except Exception:
            pass
    if value:
        try:
            select_widget.select_by_value(value)
            return value
        except Exception:
            pass

    matched = driver.execute_script(
        """
        var el = arguments[0], wantedText = arguments[1], wantedValue = arguments[2];
        var options = Array.prototype.slice.call(el && el.options ? el.options : []);
        var match = options.find(function(opt) {
            var optText = String((opt.textContent || '').trim());
            var optValue = String(opt.value || '');
            return (wantedText && optText === wantedText) || (wantedValue && optValue === wantedValue);
        });
        if (!match) return '';
        el.value = match.value;
        match.selected = true;
        el.dispatchEvent(new Event('input', {bubbles:true, cancelable:true}));
        el.dispatchEvent(new Event('change', {bubbles:true, cancelable:true}));
        return String(match.value || '');
        """,
        el,
        text,
        value,
    )
    if matched:
        current_value, current_text = _select_current_option(el)
        if (text and current_text == text) or (value and current_value == value):
            return current_text or current_value

    try:
        el.click()
    except Exception:
        try:
            driver.execute_script("arguments[0].focus();", el)
        except Exception:
            pass
    try:
        el.send_keys(Keys.HOME)
    except Exception:
        pass
    if text:
        el.send_keys(text)
    elif value:
        el.send_keys(value)
    try:
        el.send_keys(Keys.ENTER)
    except Exception:
        pass

    current_value, current_text = _select_current_option(el)
    if text and current_text == text:
        return current_text
    if value and current_value == value:
        return current_value
    raise ElementNotInteractableException(f"Could not select option text={text!r} value={value!r}")


def _should_skip_stale_postback_step(raw_event: dict[str, Any], step, next_step, current_url: str) -> bool:
    """Return True when the browser already advanced past a stale autosubmit step."""
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
    info = raw_event.get("selenium_info") if isinstance(raw_event.get("selenium_info"), dict) else {}
    attrs = info.get("attributes") if isinstance(info.get("attributes"), dict) else {}
    autosubmit = str(attrs.get("data-autosubmit") or "").strip().lower() in {"true", "1", "yes", "on"}
    return autosubmit or tag == "select"


# ---------------------------------------------------------------------------
# Action executors
# ---------------------------------------------------------------------------

def _perform_action(driver: webdriver.Chrome, step, raw_event: dict[str, Any],
                    overlay_timeout: int = _IN_PROGRESS_OVERLAY_TIMEOUT,
                    next_step=None) -> str:
    action = raw_event.get("action", step.action)
    page_url = step.page_url

    _current = _url_base_path(driver.current_url)
    _expected = _url_base_path(page_url)
    _already_navigated = (_current != _expected)

    if _already_navigated:
        # A preceding click/submit already navigated away from the step's source
        # page.  For submit (and navigate_*) steps this is expected — the form was
        # already submitted by the click that preceded it.  Navigating BACK to
        # execute a redundant submit would cause a double page-load.
        if action in ("submit", "navigate_back", "navigate_forward", "navigate_unknown"):
            return f"Form submitted"
        if _should_skip_stale_postback_step(raw_event, step, next_step, driver.current_url):
            return "Skipped stale autosubmit step after navigation"
        _safe_navigate(driver, page_url)
        time.sleep(0.5)
        # After navigating to the expected page, verify the browser actually landed
        # there.  A redirect (e.g. back to the login page when credentials were
        # invalid) means the expected page is unreachable — fail immediately so
        # the coordinate fallback does not click a random element on the wrong page.
        _post_nav_url = _url_base_path(driver.current_url)
        if _post_nav_url != _expected:
            raise NoSuchElementException(
                f"Page URL mismatch after navigation: expected {_expected!r} but "
                f"browser is at {_post_nav_url!r} — possible auth failure or redirect."
            )

    _wait_for_page_ready(driver)
    _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
    _dismiss_alert(driver)
    _bad_state = (ElementNotInteractableException, InvalidElementStateException)

    _action_attempt = 0
    while True:
        try:
            if action in ("navigate", "open", "goto"):
                if page_url:
                    _safe_navigate(driver, page_url)
                    _wait_for_page_ready(driver)
                    _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                    return f"Opened URL: {page_url}"
                return "Skipped navigation (missing page_url)"

            if action == "click":
                el, strat, rank = _find_element(driver, raw_event, EC.element_to_be_clickable)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").lower()
                toggle_el = _resolve_toggle_control(driver, el)
                if not input_type:
                    try:
                        input_type = str(toggle_el.get_attribute("type") or "").lower()
                    except Exception:
                        input_type = ""

                # --- <select> element: use value selection instead of raw click ---
                # A raw .click() on a JSF/autosubmit select can trigger an
                # unintended form submission.  Treat it like a change event.
                if el.tag_name.lower() == "select":
                    selected = _set_select_value(driver, el, raw_event)
                    _wait_for_same_page_next_step_target(driver, page_url, raw_event, next_step)
                    return f"Selected: {selected}"

                _pre_click_url = driver.current_url
                if input_type in ("checkbox", "radio"):
                    expected_checked = raw_event.get("checked")
                    final_checked = _set_toggle_state(driver, el, bool(expected_checked) if expected_checked is not None else None, raw_event)
                    if expected_checked is not None and final_checked != bool(expected_checked):
                        raise ElementNotInteractableException(
                            f"Expected {input_type} checked={bool(expected_checked)} but got {final_checked}"
                        )
                else:
                    try:
                        el.click()
                    except ElementClickInterceptedException:
                        _dismiss_modal(driver)
                        try:
                            el.click()
                        except Exception:
                            try:
                                ActionChains(driver).move_to_element(el).click().perform()
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", el)
                                except Exception:
                                    # Use event dispatch for SVG/custom elements
                                    driver.execute_script(
                                        "arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));", el
                                    )
                    except _bad_state:
                        try:
                            ActionChains(driver).move_to_element(el).click().perform()
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", el)
                            except Exception:
                                # Use event dispatch for SVG/custom elements
                                driver.execute_script(
                                    "arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));", el
                                )
                    except Exception:
                        try:
                            driver.execute_script("arguments[0].click();", el)
                        except Exception:
                            # SVG or any other element - use event dispatch
                            try:
                                driver.execute_script(
                                    "arguments[0].dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}));", el
                                )
                            except Exception:
                                pass
                # If the click triggered a page navigation, wait for the new page to be ready
                try:
                    if driver.current_url != _pre_click_url:
                        _wait_for_page_ready(driver)
                        _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                except Exception:
                    pass
                _wait_for_same_page_next_step_target(driver, page_url, raw_event, next_step)
                return "Left‑mouse‑click is pressed."

            elif action == "dblclick":
                el, strat, rank = _find_element(driver, raw_event, EC.element_to_be_clickable)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                try:
                    ActionChains(driver).double_click(el).perform()
                    return "Double-click is pressed"
                except _bad_state:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new MouseEvent('dblclick',{bubbles:true,cancelable:true}));", el
                    )
                return "Double-click is pressed."

            elif action == "contextmenu":
                el, strat, rank = _find_element(driver, raw_event, EC.element_to_be_clickable)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                try:
                    ActionChains(driver).context_click(el).perform()
                    return "Right-click is pressed"
                except _bad_state:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new MouseEvent('contextmenu',{bubbles:true,cancelable:true}));", el
                    )
                return "Right-click is pressed."

            elif action in ("input", "change"):
                el, strat, rank = _find_element(driver, raw_event, EC.visibility_of_element_located)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                value = raw_event.get("value") or ""

                input_type = str(raw_event.get("inputType") or raw_event.get("type") or "").lower()
                if input_type in ("checkbox", "radio"):
                    checked = raw_event.get("checked")
                    final_checked = _set_toggle_state(driver, el, bool(checked) if checked is not None else None, raw_event)
                    if checked is not None and final_checked != bool(checked):
                        raise ElementNotInteractableException(
                            f"Expected {input_type} checked={bool(checked)} but got {final_checked}"
                        )
                    if _toggle_autosubmit_enabled(raw_event):
                        _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                        _wait_for_same_page_next_step_target(driver, page_url, raw_event, next_step)
                    return f"{'Checked' if final_checked else 'Unchecked'} {input_type}"

                # --- <select> element: use Selenium Select class ---
                if el.tag_name.lower() == "select":
                    selected = _set_select_value(driver, el, raw_event)
                    return f"Selected: {selected or value}"

                # Both `input` and `change` must write the value then fire events.
                # A `change` step may arrive without a preceding `input` step (the
                # dedup collapses per-keystroke inputs → kept only as `change`), so
                # we MUST set the field value here; just dispatching a bare change
                # event on an empty field would submit a blank password.
                # Strategy 1 — JS native setter (reliable for React/Vue/JSF apps)
                _js_typed = driver.execute_script(
                    """
                    var el = arguments[0], val = arguments[1], isChange = arguments[2];
                    try {
                        el.focus();
                        var desc = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                                || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                        if (desc && desc.set) {
                            desc.set.call(el, val);
                        } else {
                            el.value = val;
                        }
                        el.dispatchEvent(new Event('input',  {bubbles:true, cancelable:true}));
                        el.dispatchEvent(new Event('change', {bubbles:true, cancelable:true}));
                        if (isChange) {
                            // Simulate blur so framework validators run (mirrors what Tab/click does)
                            el.dispatchEvent(new Event('blur', {bubbles:true}));
                        }
                        return true;
                    } catch(e) { return false; }
                    """,
                    el, value, (action == "change"),
                )
                if _js_typed:
                    return f"User input recorded: {value!r}"

                # Strategy 2 — Selenium send_keys fallback (plain inputs without frameworks)
                try:
                    el.clear()
                    el.send_keys(value)
                    return f"User input recorded: {value!r}"
                except _bad_state:
                    pass

                # Strategy 3 — ActionChains triple-click-select-all then type
                try:
                    ActionChains(driver).triple_click(el).send_keys(value).perform()
                    return f"User input recorded: {value!r}"
                except Exception:
                    pass

                return f"typed (best-effort): {value!r}"

            elif action == "keydown":
                key = raw_event.get("key", "")
                if key == "Tab":
                    # Tab is recorded to mark a field-commit boundary but
                    # the actual value was already typed by the preceding
                    # ``input`` step.  Replaying send_keys(TAB) on a field
                    # that no longer has focus causes spurious navigation on
                    # some apps.  Skip it — the session flow is preserved by
                    # the input + click steps around it.
                    return "Tab key is pressed"
                el, strat, rank = _find_element(driver, raw_event, EC.element_to_be_clickable)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                try:
                    if key == "Enter":
                        el.send_keys(Keys.RETURN)
                    else:
                        el.send_keys(key)
                    return f"keydown: {key}"
                except _bad_state:
                    driver.execute_script(
                        "arguments[0].dispatchEvent(new KeyboardEvent('keydown',{key:arguments[1],bubbles:true}));",
                        el, key,
                    )
                return f"keydown: {key}"

            elif action == "submit":
                el, strat, rank = _find_element(driver, raw_event, EC.element_to_be_clickable)
                _wait_for_visible(driver, el)
                _capture_pre_screenshot(driver, raw_event)
                driver.execute_script(
                    "var f=arguments[0].closest('form'); if(f){ HTMLFormElement.prototype.submit.call(f); }",
                    el,
                )
                return "Form is submitted"

            elif action == "scroll":
                delta_x = int(raw_event.get("delta_x") or 0)
                delta_y = int(raw_event.get("delta_y") or 0)
                try:
                    ActionChains(driver).scroll_by_amount(delta_x, delta_y).perform()
                except Exception:
                    driver.execute_script(
                        "window.scrollBy(arguments[0], arguments[1]);", delta_x, delta_y
                    )
                direction = "up" if delta_y < 0 else "down"
                return f"Scrolled {direction} (\u0394x={delta_x}, \u0394y={delta_y})"

            elif action == "navigate_back":
                try:
                    driver.back()
                    _wait_for_page_ready(driver)
                    _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                    return "Browser back button"
                except Exception as e:
                    return f"Failed to go back: {str(e)}"

            elif action == "navigate_forward":
                try:
                    driver.forward()
                    _wait_for_page_ready(driver)
                    _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                    return "Browser forward button"
                except Exception as e:
                    return f"Failed to go forward: {str(e)}"

            elif action == "navigate_unknown":
                # When direction can't be determined, try back first (most common)
                try:
                    driver.back()
                    _wait_for_page_ready(driver)
                    _wait_for_in_progress_overlay(driver, timeout=overlay_timeout)
                    return "Browser navigation (back)"
                except Exception as e:
                    return f"Skipped navigation (unknown direction): {str(e)}"

            else:
                return f"Skipped (unsupported action: {action})"

        except StaleElementReferenceException as _stale_exc:
            if _action_attempt < _STEP_MAX_RETRIES:
                _action_attempt += 1
                _replay_logger.warning(
                    "stale-retry-in-action  attempt=%d/%d  action=%s",
                    _action_attempt, _STEP_MAX_RETRIES, action,
                )
                for _k in ("_used_strategy", "_used_locator", "_is_primary", "_used_rank", "_element_rect"):
                    raw_event.pop(_k, None)
                time.sleep(0.4 * _action_attempt)
            else:
                raise


# ---------------------------------------------------------------------------
# Public API
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
    Replay all steps for *record_id*.

    steps:       pre-loaded Step objects (avoids DB query in background thread).
                 If None, queries the DB directly (for standalone use).
    pause_event: when set the loop waits until cleared.
    stop_event:  when set the loop exits early.
    on_step:     called with each result dict as steps complete.
    run_id:      UUID string for this replay run. Auto-generated if not supplied.
                 All RunResult rows for this replay share the same run_id.
    """
    import uuid as _uuid
    from .models import LocatorStat, RunResult, Locator  # local import  avoids top-level Django dependency

    if run_id is None:
        run_id = str(_uuid.uuid4())

    _runner = runner or os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"

    if steps is None:
        from .models import Step, Recording, Locator  # avoid circular import at module level
        _raw = list(Step.objects.filter(record_id=record_id).order_by("step_no", "id"))
        if not _raw:
            _raw = list(Recording.objects.filter(record_id=record_id).order_by("step_no", "id"))
        # Deduplicate by step_no  the table can hold multiple rows per step when a
        # session has been copied into folders; keep the first (original) row only.
        _seen: set[int] = set()
        steps = []
        for _s in _raw:
            if _s.step_no not in _seen:
                _seen.add(_s.step_no)
                steps.append(_s)
    if not steps:
        return [{"step_no": 0, "action": "", "page_url": "",
                 "status": "No steps found for this session.", "ok": False}]

    # Always sort by step_no regardless of how steps were supplied.
    steps = sorted(steps, key=lambda s: s.step_no)

    # Load configurable replay timeouts from app_config
    _cfg = _load_replay_config()
    _step_timeout   = _cfg["step_timeout"]
    _step_retries   = int(_cfg["step_retries"])
    _retry_delay    = _cfg["retry_delay"]
    _step_settle    = _cfg["step_settle"]
    _window_timeout = _cfg["window_timeout"]
    _nav_retries    = int(_cfg["nav_retries"])
    _nav_retry_wait = _cfg["nav_retry_wait"]
    _overlay_timeout = int(_cfg["overlay_timeout"])
    _replay_logger.info(
        "replay-config  step_timeout=%s  overlay_timeout=%s  step_retries=%s  "
        "retry_delay=%s  step_settle=%s  window_timeout=%s  nav_retries=%s  nav_retry_wait=%s",
        _step_timeout, _overlay_timeout, _step_retries, _retry_delay,
        _step_settle, _window_timeout, _nav_retries, _nav_retry_wait,
    )

    if not folder_name:
        _first_fn = getattr(steps[0], "folder_name", None) if steps else None
        _first_fn_s = (str(_first_fn).strip().lower() if _first_fn else "")
        _label = _get_recordings_folder_label()
        folder_name = _label if _is_recordings_folder_name_local(_first_fn_s) else (_first_fn or _label)

    _parent_folder_id = getattr(steps[0], "parent_folder_id", None) if steps else None
    _sub_folder_id    = getattr(steps[0], "sub_folder_id",    None) if steps else None
    _end_folder_id    = getattr(steps[0], "end_folder_id",    None) if steps else None

    executed_step_nos: set[int] = set()
    results = []

    # Pre-load data table values for this session so edits made via the UI
    # are used during replay instead of the original raw_event["value"].
    try:
        from .models import DataEntry as _DataEntry
        _data_rows = list(_DataEntry.objects.filter(record_id=record_id))
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

    # Resolve by field name from project test data (folder-scoped), so replay
    # always uses the latest value before execution.
    _latest_name_cache: dict[str, tuple[int | None, str | None]] = {}

    def _latest_folder_value_for_name(name: str) -> tuple[int | None, str | None]:
        key = (name or "").strip()
        if not key:
            return (None, None)
        if key in _latest_name_cache:
            return _latest_name_cache[key]

        result: tuple[int | None, str | None] = (None, None)
        try:
            with _db_connection.cursor() as _cur:
                _folder = (folder_name or "").strip()
                if _folder:
                    _like = _folder + "/%"
                    _cur.execute(
                        """
                        SELECT id, value
                          FROM data
                         WHERE field_name = %s
                           AND (
                               TRIM(COALESCE(folder_name, '')) = %s
                               OR TRIM(COALESCE(folder_name, '')) LIKE %s
                           )
                         ORDER BY COALESCE(is_global, FALSE) DESC,
                                  COALESCE(created_at, NOW()) DESC,
                                  id DESC
                         LIMIT 1
                        """,
                        [key, _folder, _like],
                    )
                else:
                    _cur.execute(
                        """
                        SELECT id, value
                          FROM data
                         WHERE field_name = %s
                         ORDER BY COALESCE(is_global, FALSE) DESC,
                                  COALESCE(created_at, NOW()) DESC,
                                  id DESC
                         LIMIT 1
                        """,
                        [key],
                    )
                _row = _cur.fetchone()
                if _row:
                    result = (_row[0], _row[1])
        except Exception:
            pass

        _latest_name_cache[key] = result
        return result

    driver, browser_name = _create_driver(headless=headless, rdp_port=rdp_port if keep_open else None)
    _wait_key = {
        "chrome": "chrome.implicit_wait",
        "msedge": "edge.implicit_wait",
        "firefox": "firefox.implicit_wait",
    }.get(browser_name, "chrome.implicit_wait")
    _impl_wait = int(_get_config(_wait_key, "10") or "10")
    driver.implicitly_wait(_impl_wait)
    _replay_logger.info(
        "driver-started  configured=%s  reported=%s  version=%s",
        browser_name,
        driver.capabilities.get("browserName"),
        driver.capabilities.get("browserVersion") or driver.capabilities.get("version"),
    )

    try:
        _safe_navigate(driver, steps[0].page_url)
        time.sleep(1)

        for idx, step in enumerate(steps):
            # --- stop check ---
            if stop_event and stop_event.is_set():
                break

            # --- pause loop ---
            while pause_event and pause_event.is_set():
                if stop_event and stop_event.is_set():
                    break
                time.sleep(0.2)

            if stop_event and stop_event.is_set():
                break


            _ordered_strats = ("xpath","id","name","value","placeholder",
                               "class","className","tagName","css","href",
                               "text","linkText","partialLinkText","type",
                               "role","title","alt","ariaLabel","dataTestId")
            _locs = step.raw_event.get("locators") or {}
            _pri_strat = ""
            _pri_loc   = ""

            # Priority 1: DB is_primary=True (explicit user override via the UI)
            try:
                _db_primary = step.primary_locator
                if _db_primary and _db_primary.locator:
                    _pri_strat = _db_primary.strategy
                    _pri_loc   = _db_primary.locator
            except Exception:
                pass
            # Priority 2: first available strategy in ordered list (xpath-first)
            if not _pri_strat:
                for _s in _ordered_strats:
                    if _locs.get(_s):
                        _pri_strat = _s
                        _pri_loc   = str(_locs[_s])
                        break

            # Load ALL locators from DB: is_primary=True first, then by locator_rank.
            # _find_element will try them in this exact order.
            _db_locator_chain: list[dict] = []
            try:
                _all_locs = list(
                    Locator.objects.filter(
                        record_id=record_id, step_no=step.step_no
                    ).order_by("locator_rank", "id")
                )
                _primary_locs  = [l for l in _all_locs if getattr(l, "is_primary", False)]
                _fallback_locs = [l for l in _all_locs if not getattr(l, "is_primary", False)]
                for _l in (_primary_locs + _fallback_locs):
                    if _l.strategy and _l.locator:
                        _db_locator_chain.append({
                            "strategy":   _l.strategy,
                            "locator":    _l.locator,
                            "rank":       _l.locator_rank if _l.locator_rank is not None else 99,
                            "is_primary": bool(getattr(_l, "is_primary", False)),
                        })
                        _locs.setdefault(_l.strategy, _l.locator)
            except Exception:
                pass

            # Ensure the primary locator value is reflected in _locs for semantic fallbacks
            if _pri_strat and _pri_loc:
                _locs[_pri_strat] = _pri_loc
            _re = step.raw_event
            _field_name = (getattr(step, "field_name", None) or _re.get("name") or _re.get("id") or "").strip()
            _latest_data_id = None
            _latest_value = None
            if _field_name:
                _latest_data_id, _latest_value = _latest_folder_value_for_name(_field_name)
            # Use DB data value (editable via UI) if available; fall back to raw_event
            _db_value    = _latest_value if _latest_value is not None else _data_map.get(step.step_no)
            _db_data_id  = _latest_data_id if _latest_data_id is not None else _data_id_map.get(step.step_no)
            _field_value = _db_value if _db_value is not None else (_re.get("value") or "")
            entry: dict = {
                "step_no":          step.step_no,
                "action":           step.action,
                "page_url":         step.page_url,
                "element_tag":      step.element_tag or "",
                "locator_strategy": _pri_strat,
                "locator_value":    _pri_loc,
                "field_name":       str(_field_name)  if _field_name  else "",
                "field_value":      str(_field_value) if _field_value else "",
                "steps_description": getattr(step, "steps_description", None) or "",
                "validation":       getattr(step, "validation", None) or "",
                "status": "",
                "ok": False,
            }
            # Inject resolved primary so _find_element uses it for Pass 1.
            # Also inject DB data value so _perform_action types the updated value.
            _event_overrides: dict = {}
            if _pri_strat:
                _event_overrides["_primary_strategy"] = _pri_strat
            _event_overrides["locators"] = _locs  # always pass merged locators dict
            if _db_locator_chain:
                _event_overrides["_db_locators"] = _db_locator_chain
            if _db_value is not None:
                _event_overrides["value"] = _db_value
            # Inject identifiers so _find_element can include them in log messages
            _event_overrides["_record_id"] = str(record_id)
            _event_overrides["_step_no"]   = step.step_no
            _event_for_replay = (
                {**step.raw_event, **_event_overrides}
                if _event_overrides else step.raw_event
            )

            # --- Driver alive / unexpected-alert check ---
            # Verify Chrome is still responding before spending time on locators.
            # UnexpectedAlertPresentException means the driver is alive but blocked by a dialog.
            try:
                _ = driver.current_url
            except UnexpectedAlertPresentException:
                _dismiss_alert(driver)   # dismiss dialog and proceed with the step
            except WebDriverException:
                _replay_logger.error(
                    "driver-dead  record_id=%s  step=%s  aborting replay",
                    record_id, step.step_no,
                )
                if stop_event is not None:
                    stop_event.set()
                break  # exit the for-step loop; finally: driver.quit() still runs

            run_status = RunResult.STATUS_FAIL
            message: str | None = None

            _recorded_delay = _recorded_step_delay(_event_for_replay, max_delay=_cfg["max_step_delay"])
            if _recorded_delay > 0:
                time.sleep(_recorded_delay)

            # ── Outer retry wrapper ──────────────────────────────────────
            # 1 + _step_retries attempts, _retry_delay seconds apart.
            # Inner loop still handles StaleElement retries independently.
            for _outer_attempt in range(_step_retries + 1):
                if _outer_attempt > 0:
                    _replay_logger.info(
                        "step-retry  outer=%d/%d  delay=%ss  record_id=%s  step=%s  action=%s",
                        _outer_attempt, _step_retries, _retry_delay,
                        record_id, step.step_no, step.action,
                    )
                    time.sleep(_retry_delay)
                    # Clear cached locator results for a fresh attempt
                    for _k in ("_used_strategy", "_used_locator", "_is_primary", "_used_rank", "_element_rect"):
                        _event_for_replay.pop(_k, None)

                _stale_retries = 0
                _step_passed = False
                while True:
                    try:
                        _next_step = steps[idx + 1] if (idx + 1) < len(steps) else None
                        message = _perform_action(driver, step, _event_for_replay,
                                                  overlay_timeout=_overlay_timeout,
                                                  next_step=_next_step)
                        _actual_strategy = _event_for_replay.get("_used_strategy")
                        _actual_locator  = _event_for_replay.get("_used_locator")
                        if _actual_strategy:
                            entry["locator_strategy"] = _actual_strategy
                        if _actual_locator:
                            entry["locator_value"] = str(_actual_locator)
                        entry["status"] = message
                        entry["ok"] = True
                        run_status = RunResult.STATUS_PASS
                        _step_passed = True
                        time.sleep(_step_settle)
                        break  # success — exit inner retry loop
                    except StaleElementReferenceException as exc:
                        if _stale_retries < _STEP_MAX_RETRIES:
                            _stale_retries += 1
                            _replay_logger.warning(
                                "stale-retry  attempt=%d/%d  record_id=%s  step=%s  action=%s",
                                _stale_retries, _STEP_MAX_RETRIES,
                                record_id, step.step_no, step.action,
                            )
                            time.sleep(0.4 * _stale_retries)
                            for _k in ("_used_strategy", "_used_locator", "_is_primary", "_used_rank", "_element_rect"):
                                _event_for_replay.pop(_k, None)
                            continue
                        message = (
                            f"Stale element (exhausted {_STEP_MAX_RETRIES} retries): "
                            f"{_clean_wd_msg(exc)}"
                        )
                        entry["status"] = message
                        _replay_logger.error(
                            "FAIL  record_id=%s  step=%s  action=%s  stale-element-exhausted",
                            record_id, step.step_no, step.action,
                        )
                        break
                    except (NoSuchElementException, ElementNotInteractableException) as exc:
                        message = f"Element not found: {exc}"
                        entry["status"] = message
                        _replay_logger.error(
                            "FAIL  record_id=%s  step=%s  action=%s  strategy=%s  locator=%r  error=%s",
                            record_id, step.step_no, step.action,
                            _event_for_replay.get("_used_strategy") or _pri_strat or "?",
                            _event_for_replay.get("_used_locator") or "",
                            message,
                        )
                        break
                    except WebDriverException as exc:
                        message = f"WebDriver error: {_clean_wd_msg(exc)}"
                        entry["status"] = message
                        _replay_logger.error(
                            "FAIL  record_id=%s  step=%s  action=%s  webdriver-error=%s",
                            record_id, step.step_no, step.action, message,
                        )
                        break
                    except (OSError, ConnectionResetError) as exc:
                        message = f"Chrome disconnected: {exc}"
                        entry["status"] = message
                        _replay_logger.error(
                            "chrome-disconnected  record_id=%s  step=%s  action=%s  error=%s",
                            record_id, step.step_no, step.action, exc,
                        )
                        if stop_event is not None:
                            stop_event.set()
                        break
                    except Exception as exc:
                        message = f"Error: {exc}"
                        entry["status"] = message
                        _replay_logger.error(
                            "FAIL  record_id=%s  step=%s  action=%s  error=%s",
                            record_id, step.step_no, step.action, message,
                        )
                        break

                if _step_passed:
                    break  # success — no need for outer retry
                # Fatal errors that shouldn't be retried
                if stop_event and stop_event.is_set():
                    break

            # ── Validation check ─────────────────────────────────────────────
            # If step.validation is set, verify the expected text appears in
            # raw_event['text'].  Only runs when the action itself passed;
            # a failing action already captures a meaningful error message.
            _validation = (getattr(step, "validation", None) or "").strip()
            if _validation and run_status == RunResult.STATUS_PASS:
                _raw_text = str(step.raw_event.get("text") or "")
                _v_passed, _v_detail = _check_validation_rule(_validation, _raw_text)
                if not _v_passed:
                    run_status = RunResult.STATUS_FAIL
                    message = f"Validation failed: {_v_detail}"
                    entry["ok"] = False
                    entry["status"] = message
                    _replay_logger.warning(
                        "VALIDATION-FAIL  record_id=%s  step=%s  expected=%r  actual=%r",
                        record_id, step.step_no, _validation, _raw_text[:120],
                    )

            # Capture a screenshot for every step (pass and fail)
            # Prefer the pre-action screenshot (captured before click/interaction)
            # because actions like click may navigate away from the page.
            _ss_bytes: bytes | None = _event_for_replay.pop("_pre_ss_bytes", None)
            _el_rect = _event_for_replay.get("_viewport_rect") or _event_for_replay.get("_element_rect")
            if not _ss_bytes:
                try:
                    # Fallback: capture post-action screenshot with highlight
                    if _el_rect:
                        try:
                            driver.execute_script("""
                                var d = document.createElement('div');
                                d.id = '__ss_hl__';
                                d.style.cssText =
                                    'position:fixed;border:3px solid red;'
                                    + 'background:rgba(255,0,0,0.15);'
                                    + 'z-index:2147483647;pointer-events:none;box-sizing:border-box;'
                                    + 'left:'  + arguments[0] + 'px;'
                                    + 'top:'   + arguments[1] + 'px;'
                                    + 'width:' + arguments[2] + 'px;'
                                    + 'height:'+ arguments[3] + 'px;';
                                document.documentElement.appendChild(d);
                            """, _el_rect['x'], _el_rect['y'], _el_rect['w'], _el_rect['h'])
                        except Exception:
                            pass
                    _ss_bytes = driver.get_screenshot_as_png()
                    # Remove the highlight overlay immediately after capture
                    if _el_rect:
                        try:
                            driver.execute_script(
                                "var e=document.getElementById('__ss_hl__');if(e)e.remove();")
                        except Exception:
                            pass
                except Exception:
                    _ss_bytes = None
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
                    _replay_logger.info("screenshot-saved  path=%s", _ss_file)
                except Exception:
                    pass

            _used_strat   = _event_for_replay.get("_used_strategy") or _pri_strat or "?"
            _used_locator = _event_for_replay.get("_used_locator") or ""
            _is_primary   = _event_for_replay.get("_is_primary", None)
            _used_rank    = _event_for_replay.get("_used_rank", None)
            _ok_marker    = "PASS" if entry["ok"] else "FAIL"
            print(
                f"[{step.step_no:>4}] {_ok_marker}  {step.action:<12}  {message or ''}",
                flush=True,
            )
            # Log step result to logger.log
            _primary_tag = "YES" if _is_primary else ("NO" if _is_primary is not None else "?")
            _rank_str    = str(_used_rank) if _used_rank is not None else "?"
            _log_level   = logging.INFO if entry["ok"] else logging.WARNING
            _loc_display = (_used_locator[:30] + "...") if _used_locator and len(_used_locator) > 30 else (_used_locator or "")
            _replay_logger.log(
                _log_level,
                "%s  record_id=%s  step=%s  action=%s  is_primary=%s  rank=%s  "
                "strategy=%s  locator=%r  msg=%s",
                _ok_marker, record_id, step.step_no, step.action,
                _primary_tag, _rank_str, _used_strat, _loc_display, message or "",
            )
            # locator_stat.log — strategy + locator used per step
            try:
                _locator_stat_log = os.path.join(
                    os.path.dirname(os.path.dirname(__file__)), "logs", "locator_stat.log"
                )
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
            # Also keep the legacy locator.log for backwards compatibility
            try:
                with open(_LOCATOR_LOG, "a", encoding="utf-8") as _lf:
                    _lf.write(
                        f"{_tz.now().strftime('%Y-%m-%d %H:%M:%S')}  "
                        f"record_id={record_id}  step={step.step_no}  "
                        f"is_primary={_primary_tag}  rank={_rank_str}  "
                        f"strategy={_used_strat}  locator={_loc_display}  "
                        f"{_ok_marker}\n"
                    )
            except Exception:
                pass

            _result_raw_event = dict(step.raw_event or {})
            if _db_value is not None:
                _result_raw_event["value"] = _db_value
                if step.action in ("input", "change"):
                    _result_raw_event["text"] = _db_value
                _selenium_info = _result_raw_event.get("selenium_info")
                if isinstance(_selenium_info, dict):
                    _selenium_info = {**_selenium_info, "value": _db_value}
                    _result_raw_event["selenium_info"] = _selenium_info
            _result_raw_event["_effective_data_id"] = _db_data_id if _db_data_id is not None else step.data_id

            # Persist result to run_table
            try:
                RunResult.objects.create(
                    run_id           = run_id,
                    record_id        = record_id,
                    step_no          = step.step_no,
                    action           = step.action,
                    page_url         = step.page_url,
                    element_tag      = step.element_tag,
                    locator_id       = None,
                    data_id          = _db_data_id if _db_data_id is not None else step.data_id,
                    raw_event        = _result_raw_event,
                    status           = run_status,
                    message          = message,
                    runner           = _runner,
                    author           = step.recorder,
                    folder_name      = folder_name,
                    parent_folder_id = _parent_folder_id,
                    sub_folder_id    = _sub_folder_id,
                    end_folder_id    = _end_folder_id,
                    run_date         = _tz.now(),
                    screenshot       = _ss_bytes,
                    steps_description = getattr(step, 'steps_description', None),
                    page_title = getattr(step, 'page_title', None),
                    engine           = 'selenium',
                )
            except Exception as db_exc:
                print(f"[run_table] Failed to save step {step.step_no}: {db_exc}", flush=True)

            # Persist successful runtime locator usage to locators_stat.
            try:
                if entry["ok"]:
                    _stat_strategy = _event_for_replay.get("_used_strategy") or _pri_strat or ""
                    _stat_locator = _event_for_replay.get("_used_locator") or _pri_loc or ""
                    _stat_rank = _event_for_replay.get("_used_rank")
                    _stat_is_primary = _event_for_replay.get("_is_primary")
                    _stat_rect = _event_for_replay.get("_element_rect") or {}

                    if _stat_strategy and _stat_locator:
                        LocatorStat.objects.create(
                            run_id=run_id,
                            record_id=record_id,
                            step_no=step.step_no,
                            strategy=str(_stat_strategy),
                            locator=str(_stat_locator),
                            is_primary=bool(_stat_is_primary),
                            locator_rank=_stat_rank if isinstance(_stat_rank, int) else None,
                            pos_x=_stat_rect.get("docX", _stat_rect.get("x")) if isinstance(_stat_rect, dict) else None,
                            pos_y=_stat_rect.get("docY", _stat_rect.get("y")) if isinstance(_stat_rect, dict) else None,
                            action=step.action,
                            page_url=step.page_url,
                            runner=_runner,
                            author=step.recorder,
                            folder_name=folder_name,
                            created_at=_tz.now(),
                        )
            except Exception as db_exc:
                print(f"[locators_stat] Failed to save step {step.step_no}: {db_exc}", flush=True)

            # Stamp the runner on the original step record
            try:
                from .models import Step as _Step
                _Step.objects.filter(
                    record_id=record_id, step_no=step.step_no
                ).update(runner=_runner, author=step.recorder)
            except Exception as db_exc:
                print(f"[steps] Failed to update runner for step {step.step_no}: {db_exc}", flush=True)

            executed_step_nos.add(step.step_no)
            results.append(entry)
            if on_step:
                on_step(entry)

            # Stop the run immediately on the first failed step.
            # Remaining steps will be tagged as not_executed by the cleanup below.
            if not entry["ok"]:
                _replay_logger.warning(
                    "run-aborted-on-failure  record_id=%s  step=%s  action=%s",
                    record_id, step.step_no, step.action,
                )
                print(
                    f"[replay] Step {step.step_no} FAILED — stopping run. "
                    f"Remaining steps will be tagged as Not Executed.",
                    flush=True,
                )
                break

    finally:
        if keep_open:
            try:
                driver.service.stop()
            except Exception:
                pass
        else:
            driver.quit()

    # Mark any steps that were not reached (stopped early) as not_executed
    for step in steps:
        if step.step_no not in executed_step_nos:
            try:
                RunResult.objects.create(
                    run_id           = run_id,
                    record_id        = record_id,
                    step_no          = step.step_no,
                    action           = step.action,
                    page_url         = step.page_url,
                    element_tag      = step.element_tag,
                    locator_id       = None,
                    data_id          = None,
                    raw_event        = step.raw_event,
                    status           = RunResult.STATUS_NOT_EXECUTED,
                    message          = None,
                    runner           = _runner,
                    author           = step.recorder,
                    folder_name      = folder_name,
                    parent_folder_id = _parent_folder_id,
                    sub_folder_id    = _sub_folder_id,
                    end_folder_id    = _end_folder_id,
                    run_date         = _tz.now(),
                    steps_description = getattr(step, 'steps_description', None),
                    page_title = getattr(step, 'page_title', None),
                    engine           = 'selenium',
                )
            except Exception as db_exc:
                print(f"[run_table] Failed to save not_executed step {step.step_no}: {db_exc}", flush=True)

    # Recalculate is_primary for each step based on all-time locator_stat hits.
    try:
        from .locator_utils import update_primary_locators_from_stats
        update_primary_locators_from_stats(run_id)
    except Exception as _lpu_exc:
        print(f"[locators] is_primary update failed: {_lpu_exc}", flush=True)

    return results
