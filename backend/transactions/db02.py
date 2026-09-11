"""
DB02 (DBACOCKPIT) — Generate a starting list of tables to check, for users who
don't already have one and don't know which tcode would give them one.

Screen flow (per the DB02 "Database Administration" overview):
  1. Navigate to /nDB02
  2. Drill into the navigation tree: Diagnostics > SQL Editor
  3. Paste a SQL query into the "Input Query" editor and press Execute
  4. Switch to the "Result" tab and read the grid (TABLENAME / DESCRIPTION)

DB02/DBACOCKPIT's tree and SQL Editor turned out to be classic dynpro screens
(not Web Dynpro, as originally suspected) — confirmed live on 2026-09-10 via
the debug_* functions below, which discovered the tree node key and control
IDs hardcoded as constants further down. Those debug_* functions are kept
around for future systems where the IDs might differ, the same way DB15 keeps
its debug-screen/debug-grid endpoints.
"""

import time
import logging

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)

TOP_TABLES_QUERY = """SELECT TOP {limit}
    M_CS_TABLES.TABLE_NAME "Table Name",
    DD02T.DDTEXT "Description",
    ROUND(M_CS_TABLES.MEMORY_SIZE_IN_TOTAL / 1073741824.0, 2) VOLUME_GB
FROM M_CS_TABLES
JOIN DD02T ON
DD02T.TABNAME = M_CS_TABLES.TABLE_NAME
AND DD02T.DDLANGUAGE = 'E'
WHERE SCHEMA_NAME IN (
    SELECT SCHEMA_NAME FROM "SYS"."TABLES" WHERE TABLE_NAME = 'T000'
)
ORDER BY M_CS_TABLES.MEMORY_SIZE_IN_TOTAL DESC"""

# Confirmed live on 2026-09-10 via the debug_* functions below (see
# backend/transactions/db02.py's module docstring and project memory for the
# discovery trail). DB02's tree and SQL Editor are classic dynpro screens,
# NOT Web Dynpro as originally suspected.
NAV_TREE_ID = "wnd[0]/shellcont[1]/shell/shellcont[1]/shell"
SQL_EDITOR_NODE_KEY = "        106"
SQL_INPUT_SHELL_ID = (
    "wnd[0]/usr/tabsSQL/tabpINPUT/ssubINPUT_REF1:SAPLSHDBCCMS:0109/"
    "cntlSQL_INPUT_CONT_HDB/shellcont/shell"
)
RESULT_TAB_ID = "wnd[0]/usr/tabsSQL/tabpOUTPUT"
RESULT_GRID_ID = (
    "wnd[0]/usr/tabsSQL/tabpOUTPUT/ssubOUTPUT_REF1:SAPLSHDBCCMS:0110/"
    "cntlSQL_OUTPUT_CONT_HDB/shellcont/shell"
)

# Confirmed live column IDs on the SQL Editor's result grid.
# VOLUME_GB is the unquoted alias for the computed size column; HANA uppercases
# it so the grid column ID is VOLUME_GB.  A few spellings are mapped defensively
# in case a future system normalises the alias differently.
DB02_COLUMN_LABELS = {
    "TABLENAME": "Table Name",
    "DESCRIPTION": "Description",
    "VOLUME_GB": "Volume (GB)",
    "VOLUMEGB": "Volume (GB)",
    "Volume_GB": "Volume (GB)",
}


def run_get_top_tables(limit: int = 300) -> dict:
    """
    Run the "top tables by size" SQL query via DB02's SQL Editor and return
    the resulting (Table Name, Description) rows.
    """
    return sap.run(_run_get_top_tables, limit)


def _run_get_top_tables(limit: int) -> dict:
    try:
        session = sap.get_session()

        # Two-step navigation: go to main menu first, then DB02.
        # Jumping to /nDB02 while already inside DB02 doesn't always reset
        # the navigation tree, leaving the SQL Editor in whatever state it
        # was in after the previous run — which can make doubleClickNode
        # toggle the editor *closed* instead of open (error 619).
        try:
            session.findById("wnd[0]/tbar[0]/okcd").text = "/n"
            session.findById("wnd[0]").sendVKey(0)
            time.sleep(SAP_SCREEN_WAIT)
            sap.dismiss_popup()
        except Exception:
            pass

        sap.navigate_to("DB02")
        time.sleep(SAP_SCREEN_WAIT)
        sap.dismiss_popup()

        # Open the SQL Editor via the navigation tree — but only if it isn't
        # already visible.  doubleClickNode is a toggle: calling it on an open
        # node closes the editor and leaves SQL_INPUT_SHELL_ID absent.
        try:
            session.findById(SQL_INPUT_SHELL_ID)
        except Exception:
            tree = session.findById(NAV_TREE_ID)
            tree.doubleClickNode(SQL_EDITOR_NODE_KEY)
            sap.wait_until_ready()
            time.sleep(SAP_SCREEN_WAIT)
            sap.dismiss_popup()

        session.findById(SQL_INPUT_SHELL_ID).text = TOP_TABLES_QUERY.format(limit=limit)

        session.findById("wnd[0]").sendVKey(8)  # F8 — Execute
        sap.wait_until_ready()
        time.sleep(SAP_SCREEN_WAIT)
        sap.dismiss_popup()

        # Log the status bar message (contains row count or SQL error text)
        try:
            status_text = session.findById("wnd[0]/sbar/pane[0]").text
            if status_text:
                logger.info("DB02 SQL status bar: %s", status_text)
        except Exception:
            status_text = ""

        session.findById(RESULT_TAB_ID).select()
        sap.wait_until_ready()
        time.sleep(SAP_SCREEN_WAIT)

        rows = _read_result_grid(session)
        return {"status": "ok", "transaction": "DB02", "rows": rows}
    except Exception as exc:
        logger.exception("DB02 top-tables failed")
        return {"status": "error", "transaction": "DB02", "message": str(exc)}


def _read_result_grid(session, retries: int = 3, retry_wait: float = 0.4) -> list[dict]:
    """Read the SQL Editor result grid, retrying briefly in case it's still
    repainting right after Execute. Mirrors db15.py's _try_read_grid: never
    calls GetColumnTitles (not valid on this ActiveX grid control), and sets
    FirstVisibleRow per row so rows outside the grid's rendered window (e.g.
    a 300-row result) don't silently read back blank."""
    last_exc: Exception | None = None
    for _ in range(retries):
        try:
            grid = session.findById(RESULT_GRID_ID)
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
                    label = DB02_COLUMN_LABELS.get(col_id, col_id)
                    try:
                        value = grid.GetCellValue(row_idx, col_id)
                        if label == "Volume (GB)":
                            try:
                                value = f"{float(value):.2f}"
                            except (ValueError, TypeError):
                                pass
                        row[label] = value
                    except Exception:
                        row[label] = ""
                rows.append(row)
            return rows
        except Exception as exc:
            last_exc = exc
            time.sleep(retry_wait)

    logger.warning("Could not read DB02 result grid: %s", last_exc)
    return []


# ---------------------------------------------------------------------------
# Discovery helpers — call these against a live, connected session to find
# the real element IDs / tree node keys before finishing run_get_top_tables.
# ---------------------------------------------------------------------------

def debug_dump_screen(container_id: str = "wnd[0]/usr") -> dict:
    """Navigate to DB02 and recursively list every scripting element under
    *container_id* (id, type, name, text). Run this first against the
    default container to see whether the left-hand tree is a classic
    GuiTree/GuiShell (scriptable) or an opaque WDA container — then re-run
    with a deeper container_id (e.g. a tree control's own id) to look inside
    it once you've spotted its path in the first dump."""
    return sap.run(_debug_dump_screen, container_id)


def _debug_dump_screen(container_id: str) -> dict:
    try:
        sap.get_session()
        sap.navigate_to("DB02")
        time.sleep(SAP_SCREEN_WAIT)
        elements = sap.dump_screen_elements(container_id)
        return {"status": "ok", "container_id": container_id, "elements": elements}
    except Exception as exc:
        logger.exception("DB02 debug dump failed")
        return {"status": "error", "message": str(exc)}


def debug_dump_tree(tree_id: str) -> dict:
    """Navigate to DB02, find the tree control at *tree_id*, and list every
    node's key + display text (via GetAllNodeKeys/GetNodeTextByKey), so the
    real node keys for "Diagnostics" and "SQL Editor" can be read off
    directly instead of guessed. Find tree_id itself from debug_dump_screen's
    output first (look for a GuiShell/GuiTree-typed element)."""
    return sap.run(_debug_dump_tree, tree_id)


def _debug_dump_tree(tree_id: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("DB02")
        time.sleep(SAP_SCREEN_WAIT)

        tree = session.findById(tree_id)
        keys = list(tree.GetAllNodeKeys())

        nodes = []
        for key in keys:
            node = {"key": key}
            try:
                node["text"] = tree.GetNodeTextByKey(key)
            except Exception as exc:
                node["text_error"] = str(exc)
            nodes.append(node)

        return {"status": "ok", "tree_id": tree_id, "nodes": nodes}
    except Exception as exc:
        logger.exception("DB02 tree dump failed")
        return {"status": "error", "message": str(exc)}


def debug_probe_run(limit: int = 20) -> dict:
    """Navigate to DB02, drill into SQL Editor (using the now-known
    NAV_TREE_ID/SQL_EDITOR_NODE_KEY), and attempt each remaining unknown step
    in turn — set the query text, press F8 to execute, switch to the Result
    tab, then dump wnd[0]/usr to find the result grid — reporting success or
    the raw error for every step independently, so a failure partway through
    doesn't hide whether the earlier steps worked."""
    return sap.run(_debug_probe_run, limit)


def _debug_probe_run(limit: int) -> dict:
    steps: dict = {}
    try:
        session = sap.get_session()
        sap.navigate_to("DB02")
        time.sleep(SAP_SCREEN_WAIT)

        try:
            tree = session.findById(NAV_TREE_ID)
            tree.doubleClickNode(SQL_EDITOR_NODE_KEY)
            sap.wait_until_ready()
            time.sleep(SAP_SCREEN_WAIT)
            sap.dismiss_popup()
            steps["open_sql_editor"] = "ok"
        except Exception as exc:
            steps["open_sql_editor"] = f"error: {exc}"
            return {"status": "ok", "steps": steps}

        query = TOP_TABLES_QUERY.format(limit=limit)
        try:
            input_shell = session.findById(SQL_INPUT_SHELL_ID)
            input_shell.text = query
            steps["set_query_text"] = "ok"
            steps["query_text_readback"] = input_shell.text
        except Exception as exc:
            steps["set_query_text"] = f"error: {exc}"

        try:
            session.findById("wnd[0]").sendVKey(8)  # F8 — Execute
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

        try:
            session.findById(RESULT_TAB_ID).select()
            sap.wait_until_ready()
            time.sleep(SAP_SCREEN_WAIT)
            steps["select_result_tab"] = "ok"
        except Exception as exc:
            steps["select_result_tab"] = f"error: {exc}"

        try:
            steps["elements_after"] = sap.dump_screen_elements("wnd[0]/usr")
        except Exception as exc:
            steps["elements_after"] = f"error: {exc}"

        try:
            grid = session.findById(RESULT_GRID_ID)
            row_count = grid.RowCount
            col_ids = list(grid.ColumnOrder)
            steps["grid_row_count"] = row_count
            steps["grid_column_order"] = col_ids
            if row_count > 0:
                try:
                    grid.FirstVisibleRow = 0
                except Exception:
                    pass
                steps["grid_first_row"] = {
                    col_id: grid.GetCellValue(0, col_id) for col_id in col_ids
                }
        except Exception as exc:
            steps["read_grid"] = f"error: {exc}"

        return {"status": "ok", "steps": steps}
    except Exception as exc:
        logger.exception("DB02 probe run failed")
        return {"status": "error", "message": str(exc), "steps": steps}


def debug_click_tree_node(tree_id: str, node_key: str, action: str = "select") -> dict:
    """Navigate to DB02, find the tree control at *tree_id*, then perform
    *action* ("select", "expand", or "doubleclick") on *node_key*, and
    re-dump wnd[0]/usr afterwards so the effect of the click (e.g. the SQL
    Editor's query box appearing) is visible in the response."""
    return sap.run(_debug_click_tree_node, tree_id, node_key, action)


def _debug_click_tree_node(tree_id: str, node_key: str, action: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("DB02")
        time.sleep(SAP_SCREEN_WAIT)

        tree = session.findById(tree_id)

        if action == "select":
            tree.selectedNode = node_key
        elif action == "expand":
            tree.expandNode(node_key)
        elif action == "doubleclick":
            tree.doubleClickNode(node_key)
        else:
            return {"status": "error", "message": f"Unknown action '{action}'"}

        sap.wait_until_ready()
        time.sleep(SAP_SCREEN_WAIT)
        sap.dismiss_popup()

        elements = sap.dump_screen_elements("wnd[0]/usr")
        return {
            "status": "ok",
            "tree_id": tree_id,
            "node_key": node_key,
            "action": action,
            "elements_after": elements,
        }
    except Exception as exc:
        logger.exception("DB02 tree click failed")
        return {"status": "error", "message": str(exc)}
