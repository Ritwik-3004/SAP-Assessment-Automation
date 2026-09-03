"""
DB15 — Find archiving objects for a database table.

Screen flow:
  1. Navigate to /nDB15
  2. Enter the table name in the selection field
  3. Execute (F8)
  4. Read the result list (archiving objects that reference the table)
"""

import time
import logging

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)


def run(table_name: str) -> dict:
    """
    Find all archiving objects that archive data from *table_name*.

    Returns a list with columns: Archiving Object, Description, etc.
    """
    return sap.run(_run, table_name)


def _run(table_name: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("DB15")
        time.sleep(SAP_SCREEN_WAIT)

        # Enter table name
        try:
            session.findById("wnd[0]/usr/ctxtP_TABNAME").text = table_name.upper()
        except Exception:
            # Alternative element ID on some SAP versions
            session.findById("wnd[0]/usr/txtP_TABNAME").text = table_name.upper()

        # Execute (F8)
        session.findById("wnd[0]").sendVKey(8)
        time.sleep(SAP_SCREEN_WAIT * 2)

        sap.dismiss_popup()

        # Try ALV grid first; fall back to plain list
        rows = _read_results(session)
        return {
            "status": "ok",
            "transaction": "DB15",
            "table_name": table_name.upper(),
            "rows": rows,
        }

    except Exception as exc:
        logger.exception("DB15 failed")
        return {"status": "error", "transaction": "DB15", "message": str(exc)}


def _read_results(session) -> list[dict]:
    # DB15 result can be either an ALV grid or a plain list report
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

    # Plain list fallback
    lines = sap.read_list_output()
    return [{"line": ln} for ln in lines]
