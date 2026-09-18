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

from sap_connector import sap
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
    return sap.run(_run, table_name, max_rows, where_clause)


def _run(table_name: str, max_rows: int, where_clause: Optional[str]) -> dict:
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


# ---------------------------------------------------------------------------
# ARCH_DEF header-table lookup (SE16N against the control table ARCH_DEF)
#
# Screen flow:
#   1. Navigate to /nSE16N
#   2. Table: ARCH_DEF, Enter -- loads the "Selection Criteria" field list
#   3. Set the "Arch. Object" (technical name OBJECT) filter to the
#      archiving object code
#   4. Execute (F8) -- result grid lists every segment/table for that object
#   5. The row whose "Parent Segment" (FATHER) is blank names the header
#      table in its "Segment" (SON) column
#
# The Selection Criteria screen (step 3) is a dynamic per-field table
# control -- confirmed live via debug_dump_arch_def_screen() on 2026-09-18.
# It's a GuiTableControl named SAPLSE16NSELFIELDS_TC, whose cells are
# addressed findById(f"{base}/<field>[col,row]") -- one row per selection
# field (row 0 = Arch. Object/OBJECT ... row 5 = Do Not Delete/DELETE_FLG
# for ARCH_DEF specifically), with columns:
#   0 = txtGS_SELFIELDS-SCRTEXT_M   (field label, e.g. "Arch. Object")
#   1 = btnOPTION                   (the "O..." operator button)
#   2 = ctxtGS_SELFIELDS-LOW        (Frm-Val -- what we write into)
#   3 = ctxtGS_SELFIELDS-HIGH       (To-Value)
#   4 = btnPUSH                     (the "More" arrow button)
#   5 = chkGS_SELFIELDS-MARK        (Output checkbox)
#   6 = txtGS_SELFIELDS-FIELDNAME   (Technical Name, e.g. "OBJECT")
# _set_object_filter() below searches column 6 for the row whose technical
# name is "OBJECT" rather than hardcoding row 0, so it keeps working even
# if a future system orders ARCH_DEF's fields differently.
# ---------------------------------------------------------------------------

# Same "Table:" field as run() above -- SE16N's initial screen is a
# standard SAP-delivered dynpro, so this ID is shared regardless of which
# table is being browsed.
TABLE_NAME_FIELD_ID = "wnd[0]/usr/ctxtGD-TAB"

SELECTION_TABLE_CONTROL_ID = "wnd[0]/usr/subTAB_SUB:SAPLSE16N:0121/tblSAPLSE16NSELFIELDS_TC"

# Confirmed live via debug_query_arch_def() on 2026-09-18: unlike DB15's
# grid (nested under wnd[0]/usr/cntl...), SE16N's "Display of Entries
# Found" result grid is a direct child of the window itself --
# wnd[0]/shellcont/shell (GuiShell, subtype SAPGUI.GridViewCtrl.1) -- not
# nested under wnd[0]/usr at all. The other two paths are kept as fallbacks
# in case a future system differs. ARCH_DEF's raw field names double as the
# ALV grid's column IDs, matching the pattern already confirmed for DB15
# (ARCHOBJ/ARCHOBJTXT) and DB02 (TABLENAME/DESCRIPTION/...) -- to be
# confirmed once the grid itself is readable.
ARCH_DEF_GRID_PATHS = [
    "wnd[0]/shellcont/shell",
    "wnd[0]/usr/cntlGRID1/shellcont/shell",
    "wnd[0]/usr/cntlCONTAINER/shellcont/shell",
    "wnd[0]/usr/cntlSE16N_GRID/shellcont/shell",
]
ARCH_DEF_COLUMN_LABELS = {
    "OBJECT": "Archiving Object",
    "SEQUENCE": "Rec. No.",
    "FATHER": "Parent Segment",
    "SON": "Segment",
    "STRUCTURE": "Structure",
    "DELETE_FLG": "No Delete",
}


def find_header_table(archiving_object: str) -> dict:
    """
    Look up the header table for *archiving_object* via SE16N/ARCH_DEF: the
    row whose Parent Segment (FATHER) is blank names the header table in its
    Segment (SON) column.
    """
    return sap.run(_find_header_table, archiving_object)


def _find_header_table(archiving_object: str) -> dict:
    try:
        session = sap.get_session()
        _open_arch_def_selection(session)
        rows = _query_arch_def(session, archiving_object)
        header_table = _pick_header_table(rows)
        return {
            "status": "ok",
            "archiving_object": archiving_object.upper(),
            "header_table": header_table,
            "row_count": len(rows),
        }
    except Exception as exc:
        logger.exception("ARCH_DEF header-table lookup failed for %s", archiving_object)
        return {"status": "error", "archiving_object": archiving_object.upper(), "message": str(exc)}


def run_batch_find_header_tables(archiving_objects: list[str], on_progress=None) -> dict:
    """
    Look up the header table for each object in *archiving_objects*, reusing
    a single SE16N/ARCH_DEF selection screen for all of them (same
    reuse-one-screen approach as db15.run_batch()).

    *on_progress*, if given, is called as on_progress(completed_count,
    archiving_object) after each object is processed.

    Returns {"status": "ok", "rows": [{"Archiving Object", "Header Table"}],
    "errors": [...]} -- a lookup failure for one object doesn't stop the
    rest of the batch.
    """
    return sap.run(_run_batch_find_header_tables, archiving_objects, on_progress)


def _run_batch_find_header_tables(archiving_objects: list[str], on_progress=None) -> dict:
    session = sap.get_session()
    _open_arch_def_selection(session)

    rows = []
    errors = []
    completed = 0

    for obj in archiving_objects:
        obj = (obj or "").strip()
        if not obj:
            continue

        try:
            result_rows = _query_arch_def(session, obj)
            header_table = _pick_header_table(result_rows)
            rows.append({"Archiving Object": obj.upper(), "Header Table": header_table or ""})
        except Exception as exc:
            logger.exception("ARCH_DEF header-table lookup failed for %s", obj)
            errors.append({"archiving_object": obj.upper(), "message": str(exc)})
            rows.append({"Archiving Object": obj.upper(), "Header Table": ""})
        finally:
            completed += 1
            if on_progress:
                on_progress(completed, obj.upper())

        # Back to the ARCH_DEF selection screen for the next object, rather
        # than re-navigating from scratch each time.
        try:
            session.findById("wnd[0]").sendVKey(3)  # F3 -- Back
            sap.wait_until_ready()
            time.sleep(SAP_SCREEN_WAIT)
            sap.dismiss_popup()
            session.findById(SELECTION_TABLE_CONTROL_ID)  # sanity check we're back
        except Exception:
            # Fell off the selection screen somehow -- re-open it fresh
            # rather than fail the rest of the batch.
            _open_arch_def_selection(session)

    return {"status": "ok", "rows": rows, "errors": errors}


def _open_arch_def_selection(session):
    """Navigate to SE16N and load ARCH_DEF's Selection Criteria screen."""
    sap.navigate_to("SE16N")
    time.sleep(SAP_SCREEN_WAIT)

    session.findById(TABLE_NAME_FIELD_ID).text = "ARCH_DEF"
    session.findById("wnd[0]").sendVKey(0)  # Enter -- loads the selection screen
    sap.wait_until_ready()
    time.sleep(SAP_SCREEN_WAIT)
    sap.dismiss_popup()


def _query_arch_def(session, archiving_object: str) -> list[dict]:
    _set_object_filter(session, archiving_object)

    session.findById("wnd[0]").sendVKey(8)  # Execute
    sap.wait_until_ready()
    time.sleep(SAP_SCREEN_WAIT)
    sap.dismiss_popup()

    return _read_arch_def_grid(session)


def _set_object_filter(session, archiving_object: str):
    """Find the Selection Criteria table control's row whose Technical Name
    (column 6) is "OBJECT" and set that row's Frm-Val (column 2, LOW)
    cell -- see SELECTION_TABLE_CONTROL_ID's comment above for the
    confirmed column layout."""
    base = SELECTION_TABLE_CONTROL_ID
    row = 0
    while True:
        try:
            fieldname = session.findById(f"{base}/txtGS_SELFIELDS-FIELDNAME[6,{row}]").text
        except Exception:
            break
        if fieldname.strip().upper() == "OBJECT":
            session.findById(f"{base}/ctxtGS_SELFIELDS-LOW[2,{row}]").text = archiving_object.upper()
            return
        row += 1

    raise RuntimeError(
        "Could not find the 'OBJECT' row in the ARCH_DEF Selection Criteria table "
        "control. Call debug_dump_arch_def_screen() (or the matching "
        "/api/transactions/se16n/debug-arch-def-screen endpoint) while on that "
        "screen to inspect the current layout and update _set_object_filter() in "
        "backend/transactions/se16n.py."
    )


def _pick_header_table(rows: list[dict]) -> "str | None":
    """The header table is the segment whose Parent Segment (FATHER) is
    blank. ARCH_DEF should have exactly one such row per archiving object;
    if it ever has more than one, the first is used and a warning logged
    rather than silently picking one with no trace of the ambiguity."""
    blank_father_rows = [r for r in rows if not (r.get("Parent Segment") or "").strip()]
    if not blank_father_rows:
        return None
    if len(blank_father_rows) > 1:
        logger.warning(
            "ARCH_DEF returned %d rows with a blank Parent Segment (expected "
            "exactly one header table) -- using the first: %s",
            len(blank_father_rows), blank_father_rows,
        )
    return blank_father_rows[0].get("Segment") or None


def _read_arch_def_grid(session, retries: int = 3, retry_wait: float = 0.4) -> list[dict]:
    """Read the ARCH_DEF result grid. Mirrors db15.py's _try_read_grid: never
    calls GetColumnTitles (not valid on this ActiveX grid control), and sets
    FirstVisibleRow per row so rows outside the grid's rendered window don't
    silently read back blank."""
    last_exc: Exception | None = None
    for grid_path in ARCH_DEF_GRID_PATHS:
        for _ in range(retries):
            try:
                grid = session.findById(grid_path)
                col_ids = list(grid.ColumnOrder)
                row_count = grid.RowCount

                rows = []
                for row_idx in range(row_count):
                    try:
                        grid.FirstVisibleRow = row_idx
                    except Exception:
                        pass

                    row = {}
                    for col_id in col_ids:
                        label = ARCH_DEF_COLUMN_LABELS.get(col_id, col_id)
                        try:
                            row[label] = grid.GetCellValue(row_idx, col_id)
                        except Exception:
                            row[label] = ""
                    rows.append(row)
                return rows
            except Exception as exc:
                last_exc = exc
                time.sleep(retry_wait)

    logger.warning("Could not read ARCH_DEF result grid: %s", last_exc)
    return []


def debug_dump_arch_def_screen() -> dict:
    """Navigate to SE16N, load ARCH_DEF's Selection Criteria screen, and
    dump every element under wnd[0]/usr -- run this if _set_object_filter()
    can't find the 'Arch. Object' field, to discover the real element ID
    directly instead of guessing."""
    return sap.run(_debug_dump_arch_def_screen)


def _debug_dump_arch_def_screen() -> dict:
    try:
        session = sap.get_session()
        _open_arch_def_selection(session)
        elements = sap.dump_screen_elements()
        return {"status": "ok", "elements": elements}
    except Exception as exc:
        logger.exception("ARCH_DEF selection-screen debug dump failed")
        return {"status": "error", "message": str(exc)}


def debug_query_arch_def(archiving_object: str) -> dict:
    """Navigate to SE16N, query ARCH_DEF for *archiving_object*, and report
    every step's outcome independently -- filter readback, the status bar
    text after Execute, and each candidate grid path's found/row_count/
    column_order/first_row -- instead of only the final (possibly empty)
    result, so a failure partway through doesn't hide whether the earlier
    steps worked. Mirrors db15.py's debug_read_grid."""
    return sap.run(_debug_query_arch_def, archiving_object)


def _debug_query_arch_def(archiving_object: str) -> dict:
    steps: dict = {}
    try:
        session = sap.get_session()
        _open_arch_def_selection(session)
        steps["opened_selection_screen"] = "ok"

        try:
            _set_object_filter(session, archiving_object)
            steps["set_object_filter"] = "ok"
        except Exception as exc:
            steps["set_object_filter"] = f"error: {exc}"
            return {"status": "ok", "steps": steps}

        # Confirm the value actually stuck in the table control cell,
        # independent of whether _set_object_filter() thinks it worked.
        try:
            base = SELECTION_TABLE_CONTROL_ID
            row = 0
            while session.findById(f"{base}/txtGS_SELFIELDS-FIELDNAME[6,{row}]").text.strip().upper() != "OBJECT":
                row += 1
            steps["filter_readback"] = session.findById(f"{base}/ctxtGS_SELFIELDS-LOW[2,{row}]").text
        except Exception as exc:
            steps["filter_readback"] = f"error: {exc}"

        try:
            session.findById("wnd[0]").sendVKey(8)  # Execute
            sap.wait_until_ready()
            time.sleep(SAP_SCREEN_WAIT)
            sap.dismiss_popup()
            steps["execute_f8"] = "ok"
        except Exception as exc:
            steps["execute_f8"] = f"error: {exc}"

        try:
            steps["status_bar"] = session.findById("wnd[0]/sbar/pane[0]").text
        except Exception as exc:
            steps["status_bar"] = f"error: {exc}"

        attempts = []
        for grid_path in ARCH_DEF_GRID_PATHS:
            attempt = {"grid_path": grid_path}
            try:
                grid = session.findById(grid_path)
                attempt["found"] = True
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

            if attempt.get("row_count", 0) > 0 and "column_order" in attempt:
                try:
                    attempt["first_row"] = {
                        col_id: grid.GetCellValue(0, col_id) for col_id in attempt["column_order"]
                    }
                except Exception as exc:
                    attempt["first_row_error"] = str(exc)
            attempts.append(attempt)
        steps["grid_attempts"] = attempts

        # Widened to the whole window, not just wnd[0]/usr: the result grid
        # may live in a sibling container instead (confirmed on 2026-09-18
        # -- a dump of wnd[0]/usr alone showed only the header labels/counts,
        # no grid or container at all, the same pattern DB02's tree turned
        # out to have -- it lived under wnd[0]/shellcont[...], not usr).
        try:
            steps["elements_after"] = sap.dump_screen_elements("wnd[0]")
        except Exception as exc:
            steps["elements_after_error"] = str(exc)

        return {"status": "ok", "archiving_object": archiving_object.upper(), "steps": steps}
    except Exception as exc:
        logger.exception("ARCH_DEF debug query failed")
        return {"status": "error", "message": str(exc), "steps": steps}
