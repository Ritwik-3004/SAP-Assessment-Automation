"""
SARA -- the information (`i`, "Documentation") button of an archiving object.

Verified live: in SARA (Archive Administration) the object goes into `ARCH_TXT-OBJECT`; Enter fills in the
description (`ARCH_TXT-OBJTEXT`); the toolbar button `wnd[0]/tbar[1]/btn[6]` (tooltip "Documentation (F6)",
the `i` icon) makes SAP GUI start the machine's browser on the object's SAP Help Portal page ("Browser
starting ...."). Nothing is shown inside SAP GUI, so the page address is read from the browser (see
help_browser.py) and the page itself is read headlessly (helpportal.py).

This module only does the SAP side: select the object and press the button.
(The *Network Graphic* button next to it is deliberately NOT used: on this SAP GUI it raises "Frontend
version is older than R/3 version" from the SAPnetz control and freezes the session. The same data is read
from table ARCH_NET instead -- see transactions/db02.py.)
"""

import logging
import time

from sap_connector import sap
from config import SAP_SCREEN_WAIT

logger = logging.getLogger(__name__)

OBJECT_FIELD = "wnd[0]/usr/ctxtARCH_TXT-OBJECT"
OBJECT_TEXT_FIELD = "wnd[0]/usr/txtARCH_TXT-OBJTEXT"
INFO_BUTTON = "wnd[0]/tbar[1]/btn[6]"


def open_information_page(archiving_object: str) -> dict:
    """Select *archiving_object* in SARA and press the information button. SAP GUI then opens the browser.

    Returns {"status": "ok", "description": str} or {"status": "error", "message": str}."""
    return sap.run(_open_information_page, archiving_object)


def _open_information_page(archiving_object: str) -> dict:
    try:
        session = sap.get_session()
        sap.navigate_to("SARA")
        time.sleep(SAP_SCREEN_WAIT)
        session.findById(OBJECT_FIELD).text = archiving_object.upper()
        session.findById("wnd[0]").sendVKey(0)
        time.sleep(SAP_SCREEN_WAIT)
        sap.dismiss_all_popups()

        description = session.findById(OBJECT_TEXT_FIELD).Text.strip()
        if not description:
            message = session.findById("wnd[0]/sbar").Text.strip()
            return {
                "status": "error",
                "message": f"SARA does not know archiving object {archiving_object}." + (f" SAP says: {message}" if message else ""),
            }
        session.findById(INFO_BUTTON).press()
        time.sleep(1.0)
        return {"status": "ok", "description": description}
    except Exception as exc:
        logger.exception("SARA information button failed for %s", archiving_object)
        return {"status": "error", "message": str(exc)}
