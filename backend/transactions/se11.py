"""
SE11 — ABAP Dictionary: retrieve table field definitions.

Screen flow:
  1. Navigate to /nSE11
  2. Select "Database table" radio, enter table name
  3. Click Display
  4. Parse the field list from the screen
"""

import time
import logging

from sap_connector import sap, _sap_lock
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)


def run(table_name: str) -> dict:
    """
    Return field definitions for *table_name* from the ABAP Dictionary.

    Each row contains: Field, Key, Data Element, Data Type, Length, Description.
    """
    with _sap_lock:
        try:
            session = sap.get_session()
            sap.navigate_to("SE11")
            time.sleep(SAP_SCREEN_WAIT)

            # Select "Database table" radio button
            try:
                session.findById("wnd[0]/usr/radDISP_OBJECT-D").select()
            except Exception:
                pass  # may already be selected by default

            # Enter table name
            session.findById("wnd[0]/usr/ctxtDISP_OBJECT-TYPENAME").text = table_name.upper()

            # Click Display button (F7)
            session.findById("wnd[0]").sendVKey(7)
            time.sleep(SAP_SCREEN_WAIT * 1.5)

            sap.dismiss_popup()

            fields = _read_field_list(session)
            return {
                "status": "ok",
                "transaction": "SE11",
                "table_name": table_name.upper(),
                "fields": fields,
            }

        except Exception as exc:
            logger.exception("SE11 failed")
            return {"status": "error", "transaction": "SE11", "message": str(exc)}


def _read_field_list(session) -> list[dict]:
    # SE11 shows a table control or ALV grid with field definitions
    for grid_path in [
        "wnd[0]/usr/tabsTS_MAIN/tabpFIELDS/ssubSUBSCREEN_MAIN:SAPLSD_WBT:0110/cntlSLEAK/shellcont/shell",
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "wnd[0]/usr/cntlFIELD_LIST/shellcont/shell",
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

    # Fallback: try reading the table control (older SAP UI)
    return _read_field_table_control(session)


def _read_field_table_control(session) -> list[dict]:
    """Read the classic SE11 table control (non-ALV)."""
    try:
        tc = session.findById("wnd[0]/usr/tabsTS_MAIN/tabpFIELDS/ssubSUBSCREEN_MAIN:SAPLSD_WBT:0110/tblSAPLSD_WBTFIELDS_CTRL")
        rows = []
        for row_idx in range(tc.RowCount):
            try:
                row = {
                    "Field": _tc_cell(session, tc, row_idx, "FIELDNAME"),
                    "Key": _tc_cell(session, tc, row_idx, "KEYFLAG"),
                    "Data Element": _tc_cell(session, tc, row_idx, "ROLLNAME"),
                    "Data Type": _tc_cell(session, tc, row_idx, "DATATYPE"),
                    "Length": _tc_cell(session, tc, row_idx, "LENG"),
                    "Decimals": _tc_cell(session, tc, row_idx, "DECIMALS"),
                    "Short Description": _tc_cell(session, tc, row_idx, "FIELDTEXT"),
                }
                rows.append(row)
            except Exception:
                continue
        return rows
    except Exception:
        return [{"line": ln} for ln in sap.read_list_output()]


def _tc_cell(session, tc, row: int, col_name: str) -> str:
    try:
        return tc.GetCell(row, col_name).text
    except Exception:
        return ""
