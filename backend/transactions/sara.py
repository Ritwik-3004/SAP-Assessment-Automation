"""
SARA — Archive Administration.

Shows archiving sessions, their status, and statistical data
for a given archiving object.

Screen flow:
  1. Navigate to /nSARA
  2. Enter archiving object name
  3. Click "Information System" or "Archive Management"
  4. Read session list
"""

import time
import logging

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)


def run(archiving_object: str) -> dict:
    """
    Retrieve archiving session information for *archiving_object*.

    Returns session list with: session ID, status, start date,
    records written, file size, etc.
    """
    return sap.run(_run, archiving_object)


def _run(archiving_object: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("SARA")
        time.sleep(SAP_SCREEN_WAIT)

        # Enter archiving object name
        try:
            session.findById("wnd[0]/usr/ctxtARCHIVOBJECT").text = archiving_object.upper()
        except Exception:
            session.findById("wnd[0]/usr/txtARCHIVOBJECT").text = archiving_object.upper()

        # Click "Information System" button to get session statistics
        # The button text / ID varies; try common paths
        _click_info_system(session)
        time.sleep(SAP_SCREEN_WAIT * 2)

        sap.dismiss_popup()

        sessions = _read_sara_sessions(session)
        return {
            "status": "ok",
            "transaction": "SARA",
            "archiving_object": archiving_object.upper(),
            "sessions": sessions,
        }

    except Exception as exc:
        logger.exception("SARA failed")
        return {"status": "error", "transaction": "SARA", "message": str(exc)}


def _click_info_system(session):
    """Click the Information System / Statistics button."""
    candidates = [
        "wnd[0]/usr/btnINFO_SYSTEM",
        "wnd[0]/tbar[1]/btn[33]",  # common toolbar position
        "wnd[0]/usr/btnSTATISTICS",
    ]
    for path in candidates:
        try:
            session.findById(path).press()
            return
        except Exception:
            continue

    # Fallback: F8
    session.findById("wnd[0]").sendVKey(8)


def _read_sara_sessions(session) -> list[dict]:
    for grid_path in [
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
        "wnd[1]/usr/cntlGRID1/shellcont/shell",
    ]:
        try:
            grid = session.findById(grid_path)
            col_ids = list(grid.ColumnOrder)
            headers = {}
            for col_id in col_ids:
                try:
                    headers[col_id] = grid.GetColumnTitles(col_id) or col_id
                except Exception:
                    headers[col_id] = col_id

            rows = []
            for row_idx in range(grid.RowCount):
                row = {}
                for col_id in col_ids:
                    try:
                        row[headers[col_id]] = grid.GetCellValue(row_idx, col_id)
                    except Exception:
                        row[headers[col_id]] = ""
                rows.append(row)
            return rows
        except Exception:
            continue

    return [{"line": ln} for ln in sap.read_list_output()]
