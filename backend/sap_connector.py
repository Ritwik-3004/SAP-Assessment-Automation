"""
SAP GUI Scripting connector.

Requires:
  - SAP GUI for Windows installed
  - Scripting enabled: SAP GUI Options > Accessibility & Scripting > Scripting tab
      [x] Enable Scripting
      [ ] Notify when a script attaches to SAP GUI  (uncheck to suppress popup)
  - pywin32 installed

Element IDs used here are standard for SAP NetWeaver 7.x / 8.0 login screens.
If your system uses a custom logon screen, record a GUI script (Help > Scripting)
to discover the correct element paths and update _login() accordingly.
"""

import queue
import subprocess
import threading
import time
import logging
import os
import xml.etree.ElementTree as ET
from typing import Optional

import pythoncom
import win32com.client

from config import SAPLOGON_EXE, SAP_SCREEN_WAIT, SAP_LANDSCAPE_PATHS

logger = logging.getLogger(__name__)


class _ComWorker:
    """Runs all SAP GUI Scripting calls on one dedicated, COM-initialized thread.

    SAP GUI Scripting objects are apartment-threaded (STA): they can only be
    used from the OS thread that first bound to them. FastAPI dispatches sync
    endpoint functions onto whichever thread-pool worker is free, which is a
    different thread on every request and never has COM initialized — so
    calling into SAP GUI directly from an endpoint fails (or is unsafe even
    when it happens not to). Every SAP operation is funneled through this
    single thread instead.
    """

    def __init__(self):
        self._jobs: "queue.Queue[tuple]" = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sap-com-worker")
        self._thread.start()

    def _loop(self):
        pythoncom.CoInitialize()
        try:
            while True:
                func, args, kwargs, done, box = self._jobs.get()
                try:
                    box["result"] = func(*args, **kwargs)
                except BaseException as exc:
                    box["error"] = exc
                done.set()
        finally:
            pythoncom.CoUninitialize()

    def run(self, func, *args, **kwargs):
        done = threading.Event()
        box: dict = {}
        self._jobs.put((func, args, kwargs, done, box))
        done.wait()
        if "error" in box:
            raise box["error"]
        return box["result"]


class SAPConnector:
    def __init__(self):
        self._gui = None
        self._engine = None
        self._connection = None
        self._session = None
        self._connected = False
        self._worker = _ComWorker()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, func, *args, **kwargs):
        """Run *func* on the dedicated SAP COM thread and return its result.

        Used by the transaction modules so their entire findById(...) sequence
        executes on the same thread as the session that created it.
        """
        return self._worker.run(func, *args, **kwargs)

    def connect(
        self,
        system: str,
        client: str,
        username: str,
        password: str,
        language: str = "EN",
    ) -> dict:
        """Launch / attach to SAP GUI and log in to *system*."""
        return self._worker.run(self._connect_impl, system, client, username, password, language)

    def _connect_impl(
        self,
        system: str,
        client: str,
        username: str,
        password: str,
        language: str,
    ) -> dict:
        try:
            self._attach_or_launch_gui()
            self._open_connection(system)
            self._login(client, username, password, language)
            self._connected = True
            logger.info("Connected to SAP system '%s' as '%s'", system, username)
            return {"status": "connected", "system": system, "user": username}
        except Exception as exc:
            logger.exception("SAP connect failed")
            self._connected = False
            return {"status": "error", "message": str(exc)}

    def disconnect(self) -> dict:
        return self._worker.run(self._disconnect_impl)

    def _disconnect_impl(self) -> dict:
        try:
            if self._session:
                self._session.findById("wnd[0]/tbar[0]/okcd").text = "/nex"
                self._session.findById("wnd[0]").sendVKey(0)
            self._connected = False
            self._session = None
            self._connection = None
            return {"status": "disconnected"}
        except Exception as exc:
            logger.exception("SAP disconnect failed")
            return {"status": "error", "message": str(exc)}

    @property
    def is_connected(self) -> bool:
        return self._connected

    def get_session(self):
        if not self._connected or self._session is None:
            raise RuntimeError("Not connected to SAP. Call /api/sap/connect first.")
        return self._session

    def navigate_to(self, transaction: str):
        """Enter /n<transaction> in the command field and press Enter."""
        session = self.get_session()
        session.findById("wnd[0]/tbar[0]/okcd").text = f"/n{transaction.upper()}"
        session.findById("wnd[0]").sendVKey(0)
        time.sleep(SAP_SCREEN_WAIT)

    def dismiss_popup(self):
        """Dismiss a modal popup (wnd[1]) by pressing Enter, if present."""
        try:
            self._session.findById("wnd[1]").sendVKey(0)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Grid / list helpers
    # ------------------------------------------------------------------

    def read_alv_grid(self, grid_id: str) -> list[dict]:
        """
        Read all rows from an ALV grid control.

        *grid_id* is the path to the GuiGridView element, e.g.:
            "wnd[0]/usr/cntlGRID1/shellcont/shell"
        """
        session = self.get_session()
        grid = session.findById(grid_id)

        row_count = grid.RowCount
        col_ids: list[str] = list(grid.ColumnOrder)

        # Build header map  col_id -> display title
        headers = {}
        for col_id in col_ids:
            try:
                headers[col_id] = grid.GetColumnTitles(col_id) or col_id
            except Exception:
                headers[col_id] = col_id

        rows = []
        for row_idx in range(row_count):
            row: dict = {}
            for col_id in col_ids:
                try:
                    row[headers[col_id]] = grid.GetCellValue(row_idx, col_id)
                except Exception:
                    row[headers[col_id]] = ""
            rows.append(row)

        return rows

    def read_list_output(self) -> list[str]:
        """
        Read plain list/report output (non-ALV) as raw text lines.
        Works for any screen showing a GuiSimpleContainer list.
        """
        session = self.get_session()
        try:
            lst = session.findById("wnd[0]/usr")
            lines = []
            for i in range(lst.Children.Count):
                child = lst.Children.ElementAt(i)
                try:
                    lines.append(child.Text.strip())
                except Exception:
                    pass
            return [ln for ln in lines if ln]
        except Exception as exc:
            return [f"(could not read list output: {exc})"]

    # ------------------------------------------------------------------
    # SAP system discovery
    # ------------------------------------------------------------------

    @staticmethod
    def list_systems() -> list[dict]:
        """Parse SAP Logon landscape XML and return configured systems."""
        for path in SAP_LANDSCAPE_PATHS:
            if os.path.exists(path):
                try:
                    return _parse_landscape(path)
                except Exception as exc:
                    logger.warning("Could not parse landscape at %s: %s", path, exc)
        return []

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _attach_or_launch_gui(self):
        try:
            self._gui = win32com.client.GetObject("SAPGUI")
        except Exception:
            logger.info("SAP GUI not running — launching %s", SAPLOGON_EXE)
            subprocess.Popen([SAPLOGON_EXE])
            self._gui = self._wait_for_sapgui()
        self._engine = self._gui.GetScriptingEngine

    @staticmethod
    def _wait_for_sapgui(timeout: float = 30.0, interval: float = 1.0):
        """Poll GetObject('SAPGUI') until the freshly-launched SAP GUI
        registers its scripting moniker, instead of guessing a fixed delay."""
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                return win32com.client.GetObject("SAPGUI")
            except Exception as exc:
                last_exc = exc
                time.sleep(interval)
        raise RuntimeError(
            f"SAP GUI did not become scriptable within {timeout}s. "
            "Check that SAP GUI Scripting is enabled "
            "(Options > Accessibility & Scripting > Scripting > Enable Scripting)."
        ) from last_exc

    def _open_connection(self, system: str):
        # If already connected to this system, reuse the session
        try:
            for i in range(self._engine.Connections.Count):
                conn = self._engine.Connections.ElementAt(i)
                if system.upper() in conn.Description.upper():
                    self._connection = conn
                    self._session = conn.Children.ElementAt(0)
                    return
        except Exception:
            pass

        self._connection = self._engine.OpenConnection(system, True)
        self._session = self._wait_for_session(self._connection, system)

    @staticmethod
    def _wait_for_session(connection, system: str, timeout: float = 30.0, interval: float = 0.5):
        """Poll for the connection's first session window instead of guessing
        a fixed delay — OpenConnection returns before the GUI window exists."""
        deadline = time.monotonic() + timeout
        last_exc: Exception | None = None
        while time.monotonic() < deadline:
            try:
                if connection.Children.Count > 0:
                    return connection.Children.ElementAt(0)
            except Exception as exc:
                last_exc = exc
            time.sleep(interval)
        raise RuntimeError(
            f"No session window appeared for connection to '{system}' within {timeout}s. "
            "Check that 'system' matches a valid entry in your SAP Logon pad."
        ) from last_exc

    def _login(self, client: str, username: str, password: str, language: str):
        session = self._session
        # Wait for login screen
        time.sleep(SAP_SCREEN_WAIT)

        # Some systems show a welcome/info screen first — dismiss it
        try:
            session.findById("wnd[1]").sendVKey(0)
            time.sleep(0.5)
        except Exception:
            pass

        # Fill standard login fields (NetWeaver 7.x)
        try:
            session.findById("wnd[0]/usr/txtRSYST-MANDT").text = client
        except Exception:
            pass  # client field absent on some systems

        session.findById("wnd[0]/usr/txtRSYST-BNAME").text = username
        session.findById("wnd[0]/usr/pwdRSYST-BCODE").text = password

        try:
            session.findById("wnd[0]/usr/txtRSYST-LANGU").text = language
        except Exception:
            pass

        session.findById("wnd[0]").sendVKey(0)  # Enter
        time.sleep(SAP_SCREEN_WAIT)

        # Handle "already logged in on another terminal" popup
        try:
            popup = session.findById("wnd[1]")
            # Press Enter (option: continue without logging off other session)
            popup.sendVKey(0)
            time.sleep(0.5)
        except Exception:
            pass


def _parse_landscape(path: str) -> list[dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    systems = []

    # SAP UI Landscape XML uses <Service> elements
    for service in root.iter("Service"):
        sid = service.get("systemid") or service.get("sid") or ""
        desc = service.get("name") or service.get("description") or sid
        systems.append({"id": sid, "description": desc, "name": service.get("name", desc)})

    return systems


# Singleton used across the app
sap = SAPConnector()
