"""
TAANA — Table ANAlysis transaction.

Displays statistical information about database tables that are relevant
for data archiving: row counts, table sizes, last archiving run, etc.

Screen flow:
  1. Navigate to /nTAANA
  2. Optional: enter table name pattern in selection screen
  3. F8 (Execute) → ALV result grid
"""

import time
import logging
from typing import Optional

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)

GRID_ID = "wnd[0]/usr/cntlGRID1/shellcont/shell"


def run(table_name: Optional[str] = None, max_rows: int = 500) -> dict:
    """
    Execute TAANA and return the analysis results.

    Parameters
    ----------
    table_name : optional table name / pattern (e.g. "BKPF", "BKPF*")
    max_rows   : safety cap on returned rows
    """
    return sap.run(_run, table_name, max_rows)


def _run(table_name: Optional[str], max_rows: int) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("TAANA")

        # --- Selection screen ---
        # Row: Table Name field (may vary — update path if needed)
        if table_name:
            try:
                session.findById(
                    "wnd[0]/usr/tabsTABSTRIP_TABBER/tabpTAB1/ssub%_SUBSCREEN_TABBER:SAPLSDB_TAANA:0110"
                    "/ctxtP_TNAME"
                ).text = table_name.upper()
            except Exception:
                # Fallback: try the simple text field directly
                try:
                    session.findById("wnd[0]/usr/ctxtP_TNAME").text = table_name.upper()
                except Exception as inner:
                    logger.debug("Could not set TAANA table field: %s", inner)

        # Set max rows (variant field, may not exist on all versions)
        try:
            session.findById("wnd[0]/usr/txtP_MAXROW").text = str(max_rows)
        except Exception:
            pass

        # F8 — Execute
        session.findById("wnd[0]").sendVKey(8)
        time.sleep(SAP_SCREEN_WAIT * 2)

        # Dismiss any info popup
        sap.dismiss_popup()

        # --- Read result grid ---
        rows = sap.read_alv_grid(GRID_ID)
        return {"status": "ok", "transaction": "TAANA", "rows": rows[:max_rows]}

    except Exception as exc:
        logger.exception("TAANA failed")
        return {"status": "error", "transaction": "TAANA", "message": str(exc)}
