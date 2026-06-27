"""
Robot Framework Library + Listener that records successful locator hits to ``locators_stat``.

Usage — add to the generated .robot *** Settings *** section:
    Library    C:/web__automation/LocatorStatListener.py
    ...        ${RECORD_ID}    record_name=${RECORD_NAME}
    ...        folder_name=${PROJECT_FOLDER}    db_url=${DB_URL}

The class's ROBOT_LISTENER_API_VERSION = 2 attribute makes Robot Framework
also treat it as a listener automatically when imported as a Library.
"""

import os
import sys
import uuid as _uuid

# SeleniumLibrary keywords whose first argument is always a locator string.
_LOCATOR_KWS = frozenset({
    "Click Element",
    "Double Click Element",
    "Input Text",
    "Input Password",
    "Clear Element Text",
    "Click Link",
    "Click Button",
    "Click Image",
    "Submit Form",
    "Select From List By Value",
    "Select From List By Label",
    "Select From List By Index",
    "Select Checkbox",
    "Unselect Checkbox",
    "Select Radio Button",
    "Mouse Over",
    "Drag And Drop",
    "Focus",
    "Set Focus To Element",
    "Scroll Element Into View",
    "Wait Until Element Is Visible",
    "Wait Until Element Is Enabled",
    "Wait Until Element Is Not Visible",
    "Element Should Be Visible",
    "Element Should Not Be Visible",
    "Element Should Be Enabled",
    "Element Should Be Disabled",
    "Get Element Attribute",
    "Get Text",
    "Get Value",
})


def _parse_locator(locator_str: str) -> tuple:
    """Parse a SeleniumLibrary locator string into (strategy, value).

    Examples:
        'xpath://*[@id="x"]'  -> ('xpath', '//*[@id="x"]')
        '//*[@id="x"]'        -> ('xpath', '//*[@id="x"]')
        'id:submit'           -> ('id', 'submit')
        '#submit'             -> ('css', '#submit')
    """
    s = str(locator_str).strip()
    if ":" in s:
        prefix, val = s.split(":", 1)
        canon = prefix.strip().lower().replace(" ", "")
        if canon in {
            "xpath", "id", "css", "name", "link", "partiallink",
            "tag", "class", "classname",
        }:
            return canon, val.strip()
    if s.startswith("//") or s.startswith("(//"):
        return "xpath", s
    return "css", s


class LocatorStatListener:
    """Acts as both a Robot Framework Library and a Listener (API v2)."""

    ROBOT_LISTENER_API_VERSION = 2

    def __init__(self, record_id: str, record_name: str = "",
                 folder_name: str = "", db_url: str = ""):
        self.record_id = str(record_id)
        self.record_name = str(record_name)
        self.folder_name = str(folder_name)
        self.runner = (
            os.environ.get("USERNAME")
            or os.environ.get("USER")
            or ""
        )
        self.run_id = str(_uuid.uuid4())
        self._db_url = str(db_url).strip() or self._resolve_db_url()
        self._conn = None
        self._step_counter = 0   # incremented at start of each locator keyword

    @staticmethod
    def _resolve_db_url() -> str:
        """Build a DB URL from environment variables (same keys as python-decouple uses)."""
        user = os.environ.get("DB_USER", "postgres")
        password = (os.environ.get("DB_PASSWORD", "") or "").replace("@", "%40").replace(":", "%3A")
        host = os.environ.get("DB_HOST", "localhost")
        port = os.environ.get("DB_PORT", "5432")
        name = os.environ.get("DB_NAME", "automation_db")
        return f"postgresql://{user}:{password}@{host}:{port}/{name}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self):
        import psycopg2
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(self._db_url)
        return self._conn

    @staticmethod
    def _kw_name(name: str, attrs: dict) -> str:
        return " ".join((attrs.get("kwname") or name).strip().split())

    # ------------------------------------------------------------------
    # Listener callbacks
    # ------------------------------------------------------------------

    def start_keyword(self, name: str, attrs: dict):
        if self._kw_name(name, attrs) in _LOCATOR_KWS:
            self._step_counter += 1

    def end_keyword(self, name: str, attrs: dict):
        if attrs.get("status") != "PASS":
            return
        kw = self._kw_name(name, attrs)
        if kw not in _LOCATOR_KWS:
            return

        args = attrs.get("args", [])
        if not args:
            return

        locator_str = str(args[0])
        strategy, locator = _parse_locator(locator_str)

        if not self._db_url:
            return  # no DB configured — skip silently

        try:
            conn = self._connect()
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO locators_stat
                        (run_id, record_id, step_no, strategy, locator,
                         is_primary, locator_rank, action,
                         runner, author, folder_name, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())
                    """,
                    (
                        self.run_id,
                        self.record_id,
                        self._step_counter,
                        strategy,
                        locator,
                        True,   # is_primary
                        1,      # locator_rank
                        kw,
                        self.runner,
                        self.runner,
                        self.folder_name,
                    ),
                )
            conn.commit()
        except Exception as exc:
            print(f"[LocatorStatListener] Failed to write stat: {exc}", file=sys.stderr)

    def close(self):
        if self._conn and not self._conn.closed:
            try:
                self._conn.close()
            except Exception:
                pass
