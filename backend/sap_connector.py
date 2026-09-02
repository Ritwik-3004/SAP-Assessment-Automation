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

# Global lock: SAP GUI is single-threaded — only one operation at a time
_sap_lock = threading.Lock()


def _com_init():
    """Initialize COM for the calling thread (STA). Safe to call multiple times."""
    try:
        pythoncom.CoInitialize()
    except pythoncom.com_error:
        pass  # already initialized in this thread


def _iter_rot():
    """Yield (display_name, moniker) pairs from the COM Running Object Table."""
    _com_init()
    ctx = pythoncom.CreateBindCtx(0)
    rot = pythoncom.GetRunningObjectTable()
    enum = rot.EnumRunning()
    while True:
        batch = enum.Next(20)   # returns a tuple; empty tuple = end of table
        if not batch:
            break
        for moniker in batch:
            try:
                yield moniker.GetDisplayName(ctx, None), moniker
            except Exception:
                continue


def _rot_list_names() -> list[str]:
    """Return all display names currently registered in the COM Running Object Table."""
    try:
        return [name for name, _ in _iter_rot()]
    except Exception:
        return []


def _rot_find_sap_gui():
    """
    Scan the COM ROT for the SAP GUI scripting object and return it as a
    win32com Dispatch wrapper, or None if not found.

    win32com.client.GetObject("SAPGUI") calls MkParseDisplayName which fails
    with MK_E_SYNTAX on Python/pywin32; direct ROT enumeration avoids this.
    COM must be initialised in the calling thread before the ROT is readable.
    """
    try:
        ctx = pythoncom.CreateBindCtx(0)
        for name, moniker in _iter_rot():
            if "SAPGUI" in name.upper():
                obj = moniker.BindToObject(ctx, None, pythoncom.IID_IDispatch)
                return win32com.client.Dispatch(obj)
    except Exception as exc:
        logger.debug("ROT scan error: %s", exc)
    return None


class SAPConnector:
    def __init__(self):
        self._gui = None
        self._engine = None
        self._connection = None
        self._session = None
        self._connected = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def connect(
        self,
        system: str,
        client: str,
        username: str,
        password: str,
        language: str = "EN",
    ) -> dict:
        """Launch / attach to SAP GUI and log in to *system*."""
        with _sap_lock:
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
        with _sap_lock:
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
        _com_init()  # COM must be initialised per-thread before ROT access
        gui = _rot_find_sap_gui()
        if gui is not None:
            self._gui = gui
            self._engine = self._gui.GetScriptingEngine
            return

        logger.info("SAP GUI not in ROT — launching %s", SAPLOGON_EXE)
        subprocess.Popen([SAPLOGON_EXE])

        # Poll the ROT every 3 s for up to 45 s.
        deadline = time.time() + 45
        while time.time() < deadline:
            time.sleep(3)
            gui = _rot_find_sap_gui()
            if gui is not None:
                self._gui = gui
                self._engine = self._gui.GetScriptingEngine
                return
            logger.debug("Still waiting for SAP GUI in ROT …")

        rot_names = _rot_list_names()
        if not rot_names:
            hint = (
                "ROT is completely empty — COM may not be initialised in this process, "
                "or SAP Logon failed to start. "
                "Try opening SAP Logon manually before connecting."
            )
        else:
            hint = (
                "SAP Logon is running but did not register the scripting object. "
                "Enable scripting: SAP GUI Options > Accessibility & Scripting > "
                "Scripting > [x] Enable Scripting."
            )
        raise RuntimeError(
            f"SAP GUI did not appear in the COM ROT within 45 s. {hint} "
            f"ROT contents: {rot_names}"
        )

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
        time.sleep(2)
        self._session = self._connection.Children.ElementAt(0)

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
