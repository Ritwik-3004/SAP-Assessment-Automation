"""
DB15 — Find archiving objects for a database table.

Screen flow (per the DB15 "Data Archiving: Object List" screen):
  1. Navigate to /nDB15
  2. Select the "Archiving Objects" radio button (selects by table)
  3. Enter the table name in the "Objects for Table" field and press Enter
  4. Read the resulting list (archiving objects that reference the table)

Element IDs confirmed via GET /api/transactions/db15/debug-screen against a
live system. If a future SAP GUI version uses different IDs, re-run that
endpoint and update the lists below.
"""

import time
import logging

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)

RADIO_ARCHIVING_OBJECTS_IDS = [
    "wnd[0]/usr/radRADIO_TABLE",
]

TABLE_FILTER_FIELD_IDS = [
    "wnd[0]/usr/txtARCH_CCMS-TABLE",
]


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

        _click_archiving_objects_radio(session)
        _set_table_filter(session, table_name)

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


def run_batch(tables: list[dict], on_progress=None) -> dict:
    """
    Run DB15 once per table in *tables* (each a dict with "table_name" and
    optional "description"), reusing a single DB15 screen for all of them.

    *on_progress*, if given, is called as on_progress(completed_count,
    table_name) after each table is processed, so a caller can report
    progress on a long-running batch.

    Returns {"status", "transaction", "rows": [...], "errors": [...]}, where
    each row is {"Table Name", "Table Description", "Archiving Object",
    "Object Description"}.
    """
    return sap.run(_run_batch, tables, on_progress)


def _run_batch(tables: list[dict], on_progress=None) -> dict:
    session = sap.get_session()
    sap.navigate_to("DB15")
    time.sleep(SAP_SCREEN_WAIT)
    _click_archiving_objects_radio(session)

    rows = []
    errors = []

    completed = 0
    for entry in tables:
        table_name = (entry.get("table_name") or "").strip()
        description = entry.get("description") or ""
        if not table_name:
            continue

        try:
            _set_table_filter(session, table_name)
            objects = _read_results(session)

            if objects:
                for obj in objects:
                    obj_code, obj_desc = _row_object_and_description(obj)
                    rows.append({
                        "Table Name": table_name.upper(),
                        "Table Description": description,
                        "Archiving Object": obj_code,
                        "Object Description": obj_desc,
                    })
            else:
                rows.append({
                    "Table Name": table_name.upper(),
                    "Table Description": description,
                    "Archiving Object": "",
                    "Object Description": "(no archiving objects found)",
                })
        except Exception as exc:
            logger.exception("DB15 batch failed for table %s", table_name)
            errors.append({"table_name": table_name.upper(), "message": str(exc)})
        finally:
            completed += 1
            if on_progress:
                on_progress(completed, table_name.upper())

    return {"status": "ok", "transaction": "DB15", "rows": rows, "errors": errors}


def debug_dump_screen() -> dict:
    """Navigate to DB15 and list every element on the selection screen, so
    the real IDs for the radio button / table filter field can be read off
    directly instead of guessed."""
    return sap.run(_debug_dump_screen)


def _debug_dump_screen() -> dict:
    try:
        sap.get_session()
        sap.navigate_to("DB15")
        time.sleep(SAP_SCREEN_WAIT)
        elements = sap.dump_screen_elements()
        return {"status": "ok", "elements": elements}
    except Exception as exc:
        logger.exception("DB15 debug dump failed")
        return {"status": "error", "message": str(exc)}


def debug_read_grid(table_name: str) -> dict:
    """Navigate to DB15, filter by *table_name*, and report exactly what
    happens at each step of reading the results grid (found? row count?
    column order? first cell?) instead of silently falling back on error."""
    return sap.run(_debug_read_grid, table_name)


def _debug_read_grid(table_name: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("DB15")
        time.sleep(SAP_SCREEN_WAIT)
        _click_archiving_objects_radio(session)
        _set_table_filter(session, table_name)

        attempts = []
        for grid_path in [
            "wnd[0]/usr/cntlOBJECT_GRID_CONT/shellcont/shell",
            "wnd[0]/usr/cntlGRID1/shellcont/shell",
            "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
        ]:
            attempt = {"grid_path": grid_path}
            try:
                grid = session.findById(grid_path)
                attempt["found"] = True
                attempt["type"] = getattr(grid, "Type", "")
            except Exception as exc:
                attempt["found"] = False
                attempt["error"] = str(exc)
                attempts.append(attempt)
                continue

            try:
                attempt["row_count"] = grid.RowCount
            except Exception as exc:
                attempt["row_count_error"] = str(exc)

            try:
                attempt["column_order"] = list(grid.ColumnOrder)
            except Exception as exc:
                attempt["column_order_error"] = str(exc)

            if "row_count" in attempt and "column_order" in attempt and attempt["row_count"] > 0:
                try:
                    attempt["first_cell"] = grid.GetCellValue(0, attempt["column_order"][0])
                except Exception as exc:
                    attempt["first_cell_error"] = str(exc)

            attempts.append(attempt)

        return {"status": "ok", "table_name": table_name.upper(), "attempts": attempts}
    except Exception as exc:
        logger.exception("DB15 grid debug failed")
        return {"status": "error", "message": str(exc)}


def _click_archiving_objects_radio(session):
    for radio_id in RADIO_ARCHIVING_OBJECTS_IDS:
        try:
            session.findById(radio_id).select()
            return
        except Exception:
            continue
    logger.warning(
        "Could not find the 'Archiving Objects' radio button by any known ID; "
        "assuming it is already selected (this is DB15's default)."
    )


def _set_table_filter(session, table_name: str):
    for field_id in TABLE_FILTER_FIELD_IDS:
        try:
            field = session.findById(field_id)
        except Exception:
            continue

        field.text = table_name.upper()
        session.findById("wnd[0]").sendVKey(0)  # Enter — refreshes the object list
        sap.wait_until_ready()
        time.sleep(SAP_SCREEN_WAIT)
        sap.dismiss_popup()
        return

    raise RuntimeError(
        "Could not find the 'Objects for Table' field on the DB15 screen by any "
        "known element ID. Record a SAP GUI script while typing into that field "
        "and update TABLE_FILTER_FIELD_IDS in backend/transactions/db15.py."
    )


def _row_object_and_description(row: dict) -> tuple[str, str]:
    """Pick the object code and description positionally, since ALV column
    header text (used as dict keys by _read_results) can vary by system."""
    values = list(row.values())
    obj = values[0] if len(values) > 0 else ""
    desc = values[1] if len(values) > 1 else ""
    return obj, desc


def _read_results(session) -> list[dict]:
    # DB15 result can be either an ALV grid or a plain list report
    for grid_path in [
        "wnd[0]/usr/cntlOBJECT_GRID_CONT/shellcont/shell",
        "wnd[0]/usr/cntlGRID1/shellcont/shell",
        "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
    ]:
        rows = _try_read_grid(session, grid_path)
        if rows is not None:
            return rows

    # Plain list fallback
    lines = sap.read_list_output()
    return [{"line": ln} for ln in lines]



# Confirmed column IDs for the DB15 object-list grid (from a live debug-grid
# dump: ColumnOrder == ["ARCHOBJ", "ARCHOBJTXT"]). Hardcoded instead of using
# grid.GetColumnTitles(), which is not a valid method on this ActiveX grid
# control and left the grid unreadable for the rest of that findById() call
# (RowCount/GetCellValue then failed too) even though the bad call itself was
# individually caught.
DB15_COLUMN_LABELS = {
    "ARCHOBJ": "Archiving Object",
    "ARCHOBJTXT": "Description",
}


def _try_read_grid(session, grid_path: str, retries: int = 3, retry_wait: float = 0.4) -> list[dict] | None:
    """Read an ALV grid at *grid_path*, retrying briefly in case the grid is
    still repainting right after a live-filter refresh. Returns None (not
    []) if the grid could never be read, so callers can fall back instead of
    reporting an empty result."""
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            grid = session.findById(grid_path)
            col_ids = list(grid.ColumnOrder)
            row_count = grid.RowCount

            rows = []
            for row_idx in range(row_count):
                # The grid only renders rows currently scrolled into view;
                # GetCellValue silently returns "" for rows outside that
                # window on longer lists (e.g. CDHDR's ~24 archiving
                # objects), so force each row into view before reading it.
                try:
                    grid.FirstVisibleRow = row_idx
                except Exception:
                    pass

                row = {}
                for col_id in col_ids:
                    label = DB15_COLUMN_LABELS.get(col_id, col_id)
                    try:
                        row[label] = grid.GetCellValue(row_idx, col_id)
                    except Exception:
                        row[label] = ""
                rows.append(row)
            return rows
        except Exception as exc:
            last_exc = exc
            time.sleep(retry_wait)

    logger.warning("Could not read grid at %s: %s", grid_path, last_exc)
    return None
