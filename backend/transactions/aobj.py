"""
AOBJ — Archiving Object Customizing.

Displays all configured archiving objects with their properties
(write program, delete program, reload program, residence time, etc.).

Screen flow:
  1. Navigate to /nAOBJ
  2. Optional: enter filter in the archiving object name field
  3. Execute / Display list
  4. Read ALV result grid
"""

import time
import logging
from typing import Optional

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)


def run(object_filter: Optional[str] = None) -> dict:
    """
    List archiving objects, optionally filtered by *object_filter* (e.g. "FI_*").
    """
    return sap.run(_run, object_filter)


def _run(object_filter: Optional[str]) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("AOBJ")
        time.sleep(SAP_SCREEN_WAIT)

        # Enter filter if provided
        if object_filter:
            try:
                session.findById("wnd[0]/usr/ctxtARCHIVOBJECT").text = object_filter.upper()
            except Exception:
                try:
                    session.findById("wnd[0]/usr/txtARCHIVOBJECT").text = object_filter.upper()
                except Exception:
                    logger.debug("AOBJ: could not set filter field")

        # Execute F8 or Enter
        session.findById("wnd[0]").sendVKey(8)
        time.sleep(SAP_SCREEN_WAIT * 2)

        sap.dismiss_popup()

        rows = _read_aobj_list(session)
        return {
            "status": "ok",
            "transaction": "AOBJ",
            "filter": object_filter,
            "rows": rows,
        }

    except Exception as exc:
        logger.exception("AOBJ failed")
        return {"status": "error", "transaction": "AOBJ", "message": str(exc)}


def _read_aobj_list(session) -> list[dict]:
    for grid_path in [
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
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
