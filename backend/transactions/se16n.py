"""
SE16N — General Table Display (extended).

Screen flow:
  1. Navigate to /nSE16N
  2. Enter table name
  3. Optional: set max rows and WHERE clause
  4. Execute (F8)
  5. Read ALV result grid
"""

import time
import logging
from typing import Optional

from sap_connector import sap, _sap_lock
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)


def run(
    table_name: str,
    max_rows: int = 200,
    where_clause: Optional[str] = None,
) -> dict:
    """
    Browse the contents of *table_name*.

    Parameters
    ----------
    table_name  : SAP transparent table (e.g. "BKPF")
    max_rows    : maximum rows to retrieve (SAP default is 200)
    where_clause: optional additional WHERE filter (e.g. "GJAHR = '2023'")
    """
    with _sap_lock:
        try:
            session = sap.get_session()
            sap.navigate_to("SE16N")
            time.sleep(SAP_SCREEN_WAIT)

            # Table name field
            session.findById("wnd[0]/usr/ctxtGD-TAB").text = table_name.upper()
            session.findById("wnd[0]").sendVKey(0)  # Enter to load table fields
            time.sleep(SAP_SCREEN_WAIT)

            # Max rows
            try:
                session.findById("wnd[0]/usr/txtGD-MAX_LINES").text = str(max_rows)
            except Exception:
                pass

            # WHERE clause (free-text additional filter)
            if where_clause:
                try:
                    session.findById("wnd[0]/usr/txtGD-WHERE").text = where_clause
                except Exception:
                    logger.debug("WHERE clause field not found in SE16N")

            # Execute (F8)
            session.findById("wnd[0]").sendVKey(8)
            time.sleep(SAP_SCREEN_WAIT * 2)

            sap.dismiss_popup()

            # Read grid
            rows = _read_se16n_grid(session)
            return {
                "status": "ok",
                "transaction": "SE16N",
                "table_name": table_name.upper(),
                "rows": rows,
            }

        except Exception as exc:
            logger.exception("SE16N failed")
            return {"status": "error", "transaction": "SE16N", "message": str(exc)}


def _read_se16n_grid(session) -> list[dict]:
    for grid_path in [
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
        "wnd[0]/usr/cntlSE16N_GRID/shellcont/shell",
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
