"""
hotkey_daemon.py
================
Background process — listens for global hotkeys and forwards them to the
running Django app via HTTP. Works even when the browser is not focused.

Hotkeys
-------
  F1      -> POST /api/hotkey/play/   - start replay of the last session
  F2      -> POST /api/hotkey/pause/  - pause the active run
  F3      -> POST /api/hotkey/resume/ - resume the paused run
  Escape  -> POST /api/hotkey/stop/   - stop all active runs immediately

Usage
-----
  python hotkey_daemon.py [--host 127.0.0.1] [--port 8000]

Dependencies:
  keyboard - pip install keyboard
  requests - pip install requests

Run as administrator on Windows so the keyboard hook has system-wide access.
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys
import tempfile
import threading
import time

try:
    import keyboard
except ImportError:
    sys.exit(
        "ERROR: 'keyboard' package not found.\n"
        "Install it with: pip install keyboard\n"
        "Then re-run this script as Administrator."
    )

try:
    import requests
except ImportError:
    sys.exit("ERROR: 'requests' package not found.\nInstall it with: pip install requests")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("hotkey_daemon")

COOLDOWN = 1.0
_last_fire: dict[str, float] = {}
_lock = threading.Lock()
_held_keys: set[str] = set()
_held_lock = threading.Lock()


def _is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _can_fire(name: str) -> bool:
    now = time.monotonic()
    with _lock:
        if now - _last_fire.get(name, 0.0) < COOLDOWN:
            return False
        _last_fire[name] = now
    return True


def _post(base_url: str, path: str, label: str) -> dict:
    url = f"{base_url}{path}"
    try:
        resp = requests.post(url, timeout=5)
        data = resp.json()
        log.info("[%s] -> %s  %s", label, url, data)
        return data
    except requests.exceptions.ConnectionError:
        log.warning("[%s] Connection refused - is the Django server running at %s?", label, base_url)
        return {}
    except Exception as exc:
        log.error("[%s] Error: %s", label, exc)
        return {}


def _minimize_all_browsers() -> None:
    """Minimize visible Chrome / Edge / Firefox windows except the monitor."""
    try:
        import psutil
        import win32con
        import win32gui
        import win32process

        browser_exes = {"chrome.exe", "msedge.exe", "firefox.exe"}

        def _cb(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if not title or "Active Runs Monitor" in title:
                return
            try:
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                pname = psutil.Process(pid).name().lower()
            except Exception:
                return
            if pname in browser_exes:
                win32gui.ShowWindow(hwnd, win32con.SW_MINIMIZE)

        win32gui.EnumWindows(_cb, None)
        log.info("[Minimize] All browser windows minimized")
    except Exception as exc:
        log.warning("[Minimize] Failed: %s", exc)


def _is_monitor_open() -> bool:
    try:
        import win32gui

        found = {"open": False}

        def _find(hwnd, _):
            if found["open"]:
                return
            if not win32gui.IsWindowVisible(hwnd):
                return
            title = win32gui.GetWindowText(hwnd)
            if title and "Active Runs Monitor" in title:
                found["open"] = True

        win32gui.EnumWindows(_find, None)
        return found["open"]
    except Exception as exc:
        log.warning("[Monitor] Open-state check failed: %s", exc)
        return False


def _foreground_webconx_page_handles_f1() -> bool:
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        if not title:
            return False

        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = psutil.Process(pid).name().lower()
        if proc_name not in {"chrome.exe", "msedge.exe", "firefox.exe"}:
            return False

        return (
            title.startswith("Projects")
            or title.startswith("Steps")
            or title.startswith("Last Run")
            or title.startswith("Replay")
        )
    except Exception:
        return False


def _open_monitor(monitor_url: str) -> None:
    """Open the Active Runs Monitor as a bottom-right popup."""
    _minimize_all_browsers()

    profile_dir = os.path.join(tempfile.gettempdir(), "webconx_monitor_profile")

    try:
        import psutil

        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                cmdline = proc.info.get("cmdline") or []
                if any(profile_dir in (arg or "") for arg in cmdline):
                    proc.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        time.sleep(0.8)
    except Exception:
        pass

    prefs_file = os.path.join(profile_dir, "Default", "Preferences")
    try:
        if os.path.isfile(prefs_file):
            os.remove(prefs_file)
            log.info("[Monitor] Removed stale Preferences to reset window state")
    except Exception:
        pass

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", ctypes.c_long),
            ("top", ctypes.c_long),
            ("right", ctypes.c_long),
            ("bottom", ctypes.c_long),
        ]

    wa = _RECT()
    ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(wa), 0)
    win_w, win_h, margin = 540, 250, 16
    x = wa.right - win_w - margin
    y = wa.bottom - win_h - margin

    chromium_paths = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    launched = False
    for exe in chromium_paths:
        if not os.path.isfile(exe):
            continue
        args = (
            f'--app="{monitor_url}" '
            f'--user-data-dir="{profile_dir}" '
            f'--window-size={win_w},{win_h} '
            f'--window-position={x},{y} '
            f'--no-first-run '
            f'--no-default-browser-check '
            f'--disable-session-crashed-bubble '
            f'--disable-infobars'
        )
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", exe, args, None, 1)
        if ret > 32:
            log.info(
                "[Monitor] Launched %s at (%s,%s) size %sx%s",
                os.path.basename(exe),
                x,
                y,
                win_w,
                win_h,
            )
            launched = True
            break
        log.warning("[Monitor] ShellExecuteW ret=%s for %s - trying next", ret, exe)

    if not launched:
        ret = ctypes.windll.shell32.ShellExecuteW(None, "open", monitor_url, None, None, 1)
        if ret <= 32:
            log.error("[Monitor] All open attempts failed (last ret=%s)", ret)
            return

    time.sleep(2.0)
    try:
        import win32con
        import win32gui

        def _size_monitor(hwnd, _):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title and "Active Runs Monitor" in title:
                    win32gui.SetWindowPos(
                        hwnd,
                        win32con.HWND_TOPMOST,
                        x,
                        y,
                        win_w,
                        win_h,
                        0x0040,
                    )
                    win32gui.SetForegroundWindow(hwnd)
                    win32gui.BringWindowToTop(hwnd)
                    log.info("[Monitor] Forced window to %sx%s at (%s,%s) TOPMOST", win_w, win_h, x, y)

        win32gui.EnumWindows(_size_monitor, None)
    except Exception as exc:
        log.warning("[Monitor] win32gui force-size failed: %s", exc)


def _f1_play(base_url: str) -> None:
    if not _can_fire("F1-Play"):
        return
    if _foreground_webconx_page_handles_f1():
        log.info("[F1] Foreground WebConX page will handle F1 - daemon skipping")
        return
    if _is_monitor_open():
        log.info("[F1] Active Runs Monitor already open - ignoring hotkey")
        return
    log.info("Hotkey fired: F1-Play")

    def _run():
        monitor_url = f"{base_url}/api/active-runs/local-login/?minimized=1"
        _open_monitor(monitor_url)

        data = _post(base_url, "/api/hotkey/play/", "F1-Play")
        if not data.get("ok"):
            log.warning("[F1] Replay did not start after opening monitor: %s", data)

    threading.Thread(target=_run, daemon=True).start()


def _foreground_is_webconx_app(base_url: str) -> bool:
    """Return True if the foreground browser window appears to be on the WebConX app itself
    (e.g. the AI Databank page), so the in-page Shift+F6 handler can take over instead."""
    try:
        import psutil
        import win32gui
        import win32process

        hwnd = win32gui.GetForegroundWindow()
        if not hwnd:
            return False
        title = (win32gui.GetWindowText(hwnd) or "").strip()
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        proc_name = psutil.Process(pid).name().lower()
        if proc_name not in {"chrome.exe", "msedge.exe", "firefox.exe"}:
            return False
        # Heuristic: WebConX app pages carry the app domain/port in the title
        host_hint = base_url.replace("http://", "").replace("https://", "")
        return host_hint in title or "WebConX" in title or "AI Databank" in title
    except Exception:
        return False


def _f6_scrape(base_url: str) -> None:
    if not _can_fire("F6-Scrape"):
        return
    if _foreground_is_webconx_app(base_url):
        # The in-page Shift+F6 keydown listener will handle it — daemon stays silent.
        log.info("[Shift+F6] WebConX app is in foreground — in-page handler takes over")
        return
    log.info("Hotkey fired: Shift+F6-Scrape (global)")
    threading.Thread(
        target=_post, args=(base_url, "/api/hotkey/scrape/", "F6-Scrape"), daemon=True
    ).start()


def make_handler(base_url: str, path: str, label: str):
    def _handler():
        if not _can_fire(label):
            return
        log.info("Hotkey fired: %s", label)
        threading.Thread(target=_post, args=(base_url, path, label), daemon=True).start()

    return _handler


def bind_single_key(key_name: str, callback, *, suppress: bool = True) -> None:
    """Bind a single key with reliable suppression and repeat-guarding."""

    def _on_press(_event):
        with _held_lock:
            if key_name in _held_keys:
                return
            _held_keys.add(key_name)
        try:
            callback()
        except Exception as exc:
            log.error("[%s] Handler failed: %s", key_name.upper(), exc)

    def _on_release(_event):
        with _held_lock:
            _held_keys.discard(key_name)

    keyboard.on_press_key(key_name, _on_press, suppress=suppress)
    keyboard.on_release_key(key_name, _on_release, suppress=suppress)


def main() -> None:
    parser = argparse.ArgumentParser(description="Global hotkey daemon for the web automation app")
    parser.add_argument("--host", default="127.0.0.1", help="Django host (default: 127.0.0.1)")
    parser.add_argument("--port", default=8000, type=int, help="Django port (default: 8000)")
    args = parser.parse_args()

    base_url = f"http://{args.host}:{args.port}"
    admin_mode = _is_admin()
    suppress_keys = admin_mode

    log.info("Hotkey daemon starting - connecting to %s", base_url)
    if admin_mode:
        log.info("Keyboard hook mode: elevated (key suppression enabled)")
    else:
        log.warning("Keyboard hook mode: standard user (key suppression disabled, hotkeys still active)")
    log.info("  F1      -> Play (start replay of last session)")
    log.info("  F2      -> Pause active run")
    log.info("  F3      -> Resume paused run")
    log.info("  Ctrl+Shift+F6 -> Scrape current Chrome page into AI Databank")
    log.info("  Escape  -> Stop all active runs")
    log.info("Press Ctrl+C to quit.\n")

    bind_single_key("f1", lambda: _f1_play(base_url), suppress=suppress_keys)
    bind_single_key("f2", make_handler(base_url, "/api/hotkey/pause/", "F2-Pause"), suppress=suppress_keys)
    bind_single_key("f3", make_handler(base_url, "/api/hotkey/resume/", "F3-Resume"), suppress=suppress_keys)
    keyboard.add_hotkey("ctrl+shift+f6", lambda: _f6_scrape(base_url), suppress=suppress_keys)
    keyboard.add_hotkey("escape", make_handler(base_url, "/api/hotkey/stop/", "Esc-Stop"), suppress=False)

    try:
        keyboard.wait()
    except KeyboardInterrupt:
        log.info("Shutting down hotkey daemon.")
        keyboard.unhook_all()


if __name__ == "__main__":
    main()