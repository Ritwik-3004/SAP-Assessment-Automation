"""
TAANA ad hoc table analysis -- the SAP GUI steps behind the "Table Analysis" task.

Every screen and element ID below was observed live (not guessed):

  /nTAANA opens "Table Analysis: Administration": a tree of analysed tables (left), a message
  grid, and -- after Display -- a header (status, run time) with the result grid.

  Start an analysis   menu Table Analysis > Perform  ->  popup "Start Table Analyses"
                      (table name, analysis variant, "In the Background" selected by default)
                      F4 on the variant  ->  "Analysis Variants: Selection"  ->  button Ad Hoc Variant (F5)
                      ->  "Analysis Variant: Create Ad Hoc Variant": the RIGHT grid lists every field of
                      the table, the LEFT grid holds the fields chosen for the variant. Select rows on the
                      right, press the move-left button. A date can be grouped by year by giving its left-grid
                      row offset 0 / length 4 (the tree then shows e.g. WI_CD(4)).
                      Continue, Continue (variant name becomes AD-HOC), Continue on the popup, then in
                      "Start Time" press Immediate and Save. The job is scheduled.
  Read the result     the new run appears in the tree as  TABLE > AD-HOC > field list. Selecting a field
                      node and pressing Table Analysis > Display shows the status ("Completed") and ONE
                      combined result grid: a column per chosen field plus "No. Entr.". Back returns.

Several runs of the same variant name pile up under the table, newest first, so a run is identified by
its start date/time not having existed before it was started.
"""

import logging
import time
from typing import Optional

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)

TREE_ID = "wnd[0]/shellcont[0]/shell/shellcont[1]/shell[1]"
HEADER_ID = "wnd[0]/usr/ssubD0100_S_HI:SAPLARCH_ANA_ADMIN:0110/"
RESULT_GRID_ID = "wnd[0]/usr/cntlCUSTOM_CONTROL/shellcont/shell"
ADHOC_NAME = "AD-HOC"

MENU_PERFORM = "wnd[0]/mbar/menu[0]/menu[0]"
MENU_DISPLAY = "wnd[0]/mbar/menu[0]/menu[1]"
BACK_BUTTON = "wnd[0]/tbar[0]/btn[3]"

FAILED_WORDS = ("error", "abort", "cancel", "termin", "fail")


class TaanaError(RuntimeError):
    """A TAANA step did not behave as expected (message is user-readable)."""


# ---------------------------------------------------------------------------
# Small helpers (all run on the SAP COM thread)
# ---------------------------------------------------------------------------

def _exists(session, element_id: str) -> bool:
    try:
        session.findById(element_id)
        return True
    except Exception:
        return False


def _title(session, window: str) -> str:
    try:
        return session.findById(window).Text
    except Exception:
        return ""


def _wait_for_window(session, window: str, title: str, timeout: float = 15.0) -> None:
    """Wait until *window* is open with *title*; otherwise raise with SAP's own status message."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _title(session, window) == title:
            return
        time.sleep(0.3)
    message = ""
    try:
        message = session.findById("wnd[0]/sbar").Text
    except Exception:
        pass
    raise TaanaError(
        f"Expected the '{title}' screen but it did not open." + (f" SAP says: {message}" if message else "")
    )


def _close_popups(session) -> None:
    """Cancel every open popup (highest first), leaving the main window."""
    for _ in range(6):
        for window in ("wnd[3]", "wnd[2]", "wnd[1]"):
            if _exists(session, window):
                try:
                    session.findById(window).sendVKey(12)  # Cancel
                except Exception:
                    pass
                time.sleep(0.4)
                break
        else:
            return


def _go_to_taana(session) -> None:
    _close_popups(session)
    sap.navigate_to("TAANA")
    time.sleep(SAP_SCREEN_WAIT)
    if not _exists(session, TREE_ID):
        raise TaanaError("TAANA did not open (its analysis tree was not found).")


def _tree_column(tree, title: str) -> Optional[str]:
    for name in tree.GetColumnNames():
        try:
            if tree.GetColumnTitleFromName(name) == title:
                return name
        except Exception:
            continue
    return None


def _node_path(tree, key) -> str:
    return tree.GetNodePathByKey(key)


def _find_table_node(tree, table: str):
    for key in tree.GetAllNodeKeys():
        text = tree.GetNodeTextByKey(key).strip()
        if _node_path(tree, key).count("\\") == 0 and text.split(" ")[0].upper() == table:
            return key
    return None


def _adhoc_children(tree, table_key) -> list:
    """AD-HOC variant nodes directly under *table_key*, in tree order (newest first)."""
    prefix = _node_path(tree, table_key) + "\\"
    out = []
    for key in tree.GetAllNodeKeys():
        path = _node_path(tree, key)
        if path.startswith(prefix) and path.count("\\") == prefix.count("\\"):
            if tree.GetNodeTextByKey(key).strip().upper().startswith(ADHOC_NAME):
                out.append(key)
    return out


def _stamp(tree, key, date_col: Optional[str], time_col: Optional[str]) -> str:
    try:
        return f"{tree.GetItemText(key, date_col)} {tree.GetItemText(key, time_col)}".strip()
    except Exception:
        return ""


def _adhoc_stamps(session, table: str) -> list[str]:
    """Start date/time of every AD-HOC run already present under *table* (screen must be TAANA)."""
    tree = session.findById(TREE_ID)
    key = _find_table_node(tree, table)
    if key is None:
        return []
    try:
        tree.expandNode(key)
    except Exception:
        pass
    date_col, time_col = _tree_column(tree, "Start date"), _tree_column(tree, "Start time")
    return [_stamp(tree, k, date_col, time_col) for k in _adhoc_children(tree, key)]


def _open_adhoc_dialog(session, table: str) -> None:
    """From the TAANA administration screen to the open 'Create Ad Hoc Variant' dialog for *table*."""
    session.findById(MENU_PERFORM).select()
    _wait_for_window(session, "wnd[1]", "Start Table Analyses")
    session.findById("wnd[1]/usr/ctxtTAAN_HEAD-TABNAME").text = table
    session.findById("wnd[1]/usr/txtTAAN_HEAD-ANA_NAME").setFocus()
    session.findById("wnd[1]").sendVKey(4)  # F4 on the analysis variant
    try:
        _wait_for_window(session, "wnd[2]", "Analysis Variants: Selection")
    except TaanaError as exc:
        raise TaanaError(f"TAANA does not accept table {table}. {exc}") from exc
    session.findById("wnd[2]/tbar[0]/btn[5]").press()  # Ad Hoc Variant (create)
    _wait_for_window(session, "wnd[3]", "Analysis Variant: Create Ad Hoc Variant")


def _right_grid(session):
    return session.findById("wnd[3]/usr/cntlD0200_CC_ALV_RIGHT/shellcont/shell")


def _left_grid(session):
    return session.findById("wnd[3]/usr/cntlD0200_CC_ALV_LEFT/shellcont/shell")


def _read_grid_rows(grid, columns: list[str]) -> list[list[str]]:
    """Every row of an ALV grid. FirstVisibleRow is set per row because cells outside the
    grid's rendered window otherwise read back blank."""
    rows = []
    for r in range(grid.RowCount):
        try:
            grid.FirstVisibleRow = r
        except Exception:
            pass
        rows.append([grid.GetCellValue(r, c) for c in columns])
    return rows


# ---------------------------------------------------------------------------
# 1. The fields of a table
# ---------------------------------------------------------------------------

def list_fields(table: str) -> dict:
    """The fields TAANA offers for *table* (what the ad hoc variant dialog lists), without
    changing anything: {"status": "ok", "fields": [{"name", "label"}]}."""
    return sap.run(_list_fields, table)


def _list_fields(table: str) -> dict:
    try:
        session = sap.get_session()
        _go_to_taana(session)
        try:
            _open_adhoc_dialog(session, table)
            grid = _right_grid(session)
            rows = _read_grid_rows(grid, ["FIELDNAME", "FIELDDESCR"])
        finally:
            _close_popups(session)
        fields = [{"name": n.strip(), "label": d.strip()} for n, d in rows if n.strip()]
        if not fields:
            raise TaanaError(f"TAANA listed no fields for {table}.")
        return {"status": "ok", "fields": fields}
    except Exception as exc:
        logger.exception("TAANA field list failed for %s", table)
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# 2. Create the ad hoc variant and start it in the background, immediately
# ---------------------------------------------------------------------------

def start_analysis(table: str, fields: list[dict]) -> dict:
    """Create an ad hoc variant of *fields* for *table* and schedule the analysis as a background
    job starting immediately. *fields* are {"name": str, "year": bool}; year=True groups a date by its
    first four characters. Returns {"status": "ok", "before": [stamps of earlier runs]}."""
    return sap.run(_start_analysis, table, fields)


def _start_analysis(table: str, fields: list[dict]) -> dict:
    try:
        session = sap.get_session()
        _go_to_taana(session)
        before = _adhoc_stamps(session, table)
        _open_adhoc_dialog(session, table)

        right = _right_grid(session)
        available = [row[0].strip() for row in _read_grid_rows(right, ["FIELDNAME"])]
        for spec in fields:
            name = spec["name"].strip().upper()
            if name not in [a.upper() for a in available]:
                raise TaanaError(f"Field {name} is not offered by TAANA for {table}.")
            index = [a.upper() for a in available].index(name)
            right.selectedRows = str(index)
            session.findById("wnd[3]/usr/btnD0200_D_MOVE_LEFT").press()
            time.sleep(0.6)
            available.pop(index)

        left = _left_grid(session)
        chosen = [row[0].strip().upper() for row in _read_grid_rows(left, ["FIELDNAME"])]
        if sorted(chosen) != sorted(s["name"].strip().upper() for s in fields):
            raise TaanaError(f"The variant holds {chosen}, not the fields that were chosen.")

        for spec in fields:
            if not spec.get("year"):
                continue
            row = chosen.index(spec["name"].strip().upper())
            left.ModifyCell(row, "PARTOFFSET", "0")
            left.ModifyCell(row, "PARTLENGTH", "4")
            left.SetCurrentCell(row, "PARTLENGTH")
            try:
                left.PressEnter()
            except Exception:
                pass
            time.sleep(0.4)
            if left.GetCellValue(row, "PARTLENGTH").strip() != "4":
                raise TaanaError(f"Could not set the year grouping on {spec['name']}.")

        session.findById("wnd[3]/tbar[0]/btn[0]").press()  # Continue
        _wait_for_window(session, "wnd[2]", "Analysis Variants: Selection")
        session.findById("wnd[2]/tbar[0]/btn[0]").press()  # Continue with the ad hoc variant
        _wait_for_window(session, "wnd[1]", "Start Table Analyses")

        variant = session.findById("wnd[1]/usr/txtTAAN_HEAD-ANA_NAME").Text.strip()
        if variant != ADHOC_NAME:
            raise TaanaError(f"Expected the ad hoc variant ({ADHOC_NAME}) but the variant is '{variant}'.")
        session.findById("wnd[1]/usr/radD0020_CH_BATCH").select()  # In the Background

        session.findById("wnd[1]/tbar[0]/btn[0]").press()  # Continue -> Start Time
        _wait_for_window(session, "wnd[2]", "Start Time")
        session.findById("wnd[2]/usr/btnSOFORT_PUSH").press()  # Immediate
        time.sleep(0.8)
        session.findById("wnd[2]/tbar[0]/btn[11]").press()  # Save -> job scheduled
        time.sleep(SAP_SCREEN_WAIT)
        if _exists(session, "wnd[1]") or _exists(session, "wnd[2]"):
            raise TaanaError("SAP is still showing a dialog after scheduling the job; it may not have been scheduled.")
        return {"status": "ok", "before": before}
    except Exception as exc:
        logger.exception("TAANA start failed for %s", table)
        try:
            _close_popups(sap.get_session())
        except Exception:
            pass
        return {"status": "error", "message": str(exc)}


# ---------------------------------------------------------------------------
# 3. Has the run finished? If so, read its result
# ---------------------------------------------------------------------------

def check_analysis(table: str, before: list[str]) -> dict:
    """Look for the run started after *before* and read it if it has completed.

    Returns {"state": "waiting" | "completed" | "failed" | "error", "status": str, ...}; a completed run
    also has "columns" (titles), "column_ids", "rows" (list of lists) and "started" ("date / time")."""
    return check_analyses([{"table": table, "before": before}]).get(table, {"state": "error", "status": "No answer."})


def check_analyses(items: list[dict]) -> dict:
    """Check several scheduled runs in ONE pass through TAANA (one navigation, one fresh tree).

    *items* are {"table", "before"}; returns {table: result} with the shape of check_analysis()."""
    return sap.run(_check_analyses, items)


def _check_analyses(items: list[dict]) -> dict:
    out: dict = {}
    try:
        session = sap.get_session()
        _go_to_taana(session)
    except Exception as exc:
        logger.exception("TAANA check could not open TAANA")
        return {i["table"]: {"state": "error", "status": str(exc)} for i in items}
    for item in items:
        table = item["table"]
        try:
            out[table] = _check_one(session, table, item["before"])
        except Exception as exc:
            logger.exception("TAANA check failed for %s", table)
            out[table] = {"state": "error", "status": str(exc)}
            try:  # get back to the tree for the next table
                _go_to_taana(session)
            except Exception:
                pass
    return out


def _check_one(session, table: str, before: list[str]) -> dict:
    """One run's state; the TAANA tree must be open and current. Leaves the tree open."""
    tree = session.findById(TREE_ID)
    table_key = _find_table_node(tree, table)
    if table_key is None:
        return {"state": "waiting", "status": "The analysis is not listed in TAANA yet."}
    tree.expandNode(table_key)
    date_col, time_col = _tree_column(tree, "Start date"), _tree_column(tree, "Start time")

    new_run = next(
        (k for k in _adhoc_children(tree, table_key) if _stamp(tree, k, date_col, time_col) not in before),
        None,
    )
    if new_run is None:
        return {"state": "waiting", "status": "The job has not produced its analysis yet."}
    tree.expandNode(new_run)
    field_nodes = [
        k for k in tree.GetAllNodeKeys()
        if _node_path(tree, k).startswith(_node_path(tree, new_run) + "\\")
    ]
    if not field_nodes:
        return {"state": "waiting", "status": "The analysis has no field list yet."}

    tree.selectNode(field_nodes[0])
    time.sleep(0.4)
    session.findById(MENU_DISPLAY).select()
    time.sleep(SAP_SCREEN_WAIT * 1.5)
    if not _exists(session, HEADER_ID + "txtD0100_O_STATUS"):
        return {"state": "waiting", "status": "The analysis result is not available yet."}

    status = session.findById(HEADER_ID + "txtD0100_O_STATUS").Text.strip()
    lowered = status.lower()
    started = session.findById(HEADER_ID + "txtD0100_O_DATE_AND_TIME").Text.strip()
    try:
        if any(w in lowered for w in FAILED_WORDS):
            return {"state": "failed", "status": status, "started": started}
        if not lowered.startswith("complet"):
            return {"state": "waiting", "status": status or "running", "started": started}
        grid = session.findById(RESULT_GRID_ID)
        column_ids = list(grid.ColumnOrder)
        titles = [grid.GetDisplayedColumnTitle(c) or c for c in column_ids]
        rows = _read_grid_rows(grid, column_ids)
        return {
            "state": "completed",
            "status": status,
            "started": started,
            "columns": titles,
            "column_ids": column_ids,
            "rows": rows,
        }
    finally:
        try:
            session.findById(BACK_BUTTON).press()  # back to the tree for the next run
            time.sleep(0.8)
        except Exception:
            pass
