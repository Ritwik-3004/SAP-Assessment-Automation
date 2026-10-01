"""
Housekeeping/cleanup program lookup for tables that have NO archiving object
(zero DB15 candidates). Not every SAP table is covered by a formal archiving
object -- some (e.g. log, temporary, or staging tables) are instead cleaned
up via a standalone housekeeping/cleanup program, report, or transaction.
For those tables, this module looks for such a recommendation instead of
just leaving the table with nothing.

Two-stage lookup:
  1. SAP's official DVM Guide (dvm_guide.get_reference()) -- the same
     per-table excerpt already used to ground archiving-object scoring in
     scoring.py, read by an LLM specifically for a housekeeping/cleanup
     recommendation rather than an archiving object.
  2. SAP for Me portal search + article scraping + LLM extraction
     (sap_for_me.py), for a table the DVM Guide doesn't cover, or covers but
     names no program for. This stage runs as a single batched pass over
     every such table, sharing one logged-in browser session
     (sap_for_me.SapForMeSession) -- opening/logging in fresh per table
     would make a lookup involving several uncovered tables prohibitively
     slow.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Optional

from pydantic import BaseModel, Field

import dvm_guide
import llm
import sap_for_me

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an SAP data archiving expert. The database table you are given "
    "has NO SAP archiving object available in this system to remove its "
    "data. Some such tables can still be cleaned up through a standalone "
    "housekeeping or cleanup program, report, or transaction instead of a "
    "formal archiving object -- for example a deletion report for log, "
    "temporary, or staging data. You are given an excerpt from SAP's "
    "official Data Management Guide for SAP Business Suite covering this "
    "table. Read it carefully and decide: does it name a SPECIFIC "
    "housekeeping/cleanup program, report, or transaction for this table? "
    "This must be distinct from an archiving object recommendation -- if "
    "the excerpt only names an archiving object (for use with SARA), that "
    "does NOT count; set found=false. If a specific program, report, or "
    "transaction is named, return its name/ID and a one-sentence rationale "
    "citing what the guide says. If none is named, or the excerpt doesn't "
    "clearly identify one, set found=false -- never guess or invent a "
    "program name."
)


class HousekeepingResult(BaseModel):
    found: bool = Field(
        description="True only if the excerpt names a specific housekeeping/"
        "cleanup program, report, or transaction for this table (not an "
        "archiving object)."
    )
    program: str = Field(default="", description="The program/report/transaction name or ID, if found.")
    rationale: str = Field(default="", description="One short sentence explaining the finding.")


def find_housekeeping_programs(
    table_names: list[str],
    on_progress: Optional[Callable[[str], None]] = None,
    settings: Optional[llm.Settings] = None,
) -> dict[str, dict]:
    """
    For each table in *table_names*, look for a housekeeping/cleanup program
    grounded in the DVM Guide, falling back to a single batched SAP for Me
    pass (see module docstring) for tables the guide doesn't cover.

    *on_progress*, if given, is called with a short status message once per
    table during the SAP for Me pass (that stage can be slow -- real browser
    navigation per table -- so callers driving a progress bar should surface
    this rather than sitting on a stale message).

    Returns {table_name: {"program": str, "rationale": str}}. "program" is
    empty when nothing was found -- "rationale" still explains why so the
    caller never has to show a blank cell with no context.

    *settings* pins which AI model (Claude or Groq, see llm.py) is used for the whole
    lookup; it defaults to the model currently chosen in the app.
    """
    settings = settings or llm.load_settings()
    problem = llm.not_configured_message(settings)
    if problem:
        logger.warning("%s -- skipping housekeeping-program lookup.", problem)
        return {}

    ref_chars = llm.budget(settings)["dvm_excerpt_chars"]
    references = {name: dvm_guide.get_reference(name) for name in table_names}
    with_reference = {name: ref[:ref_chars] for name, ref in references.items() if ref}

    results: dict[str, dict] = {}
    needs_fallback: dict[str, str] = {name: "" for name in table_names if name not in with_reference}

    if with_reference:
        with ThreadPoolExecutor(max_workers=llm.max_workers(settings)) as pool:
            future_to_table = {
                pool.submit(_find_one, settings, name, reference): name
                for name, reference in with_reference.items()
            }
            for future in as_completed(future_to_table):
                name = future_to_table[future]
                try:
                    found = future.result()
                except Exception as exc:
                    logger.warning("Housekeeping lookup failed for table %s: %s", name, exc, exc_info=True)
                    results[name] = {"program": "", "rationale": f"Housekeeping lookup failed: {exc}"}
                    continue

                if found["program"]:
                    results[name] = found
                else:
                    # DVM Guide didn't name one -- fall through to the SAP
                    # for Me batch below rather than reporting "not found"
                    # purely off the guide.
                    needs_fallback[name] = found["rationale"]

    if needs_fallback:
        results.update(_search_sap_for_me_batch(needs_fallback, on_progress, settings))

    return results


def _find_one(settings: llm.Settings, table_name: str, reference: str) -> dict:
    user_content = (
        f"Table: {table_name}\n\n"
        f"DVM Guide excerpt:\n---\n{reference}\n---\n\n"
        "Does this excerpt name a housekeeping/cleanup program (not an archiving object) for this table?"
    )
    parsed: HousekeepingResult = llm.structured(
        SYSTEM_PROMPT, user_content, HousekeepingResult, max_tokens=500, settings=settings
    )
    if parsed.found and parsed.program.strip():
        return {"program": parsed.program.strip(), "rationale": parsed.rationale}
    return {"program": "", "rationale": parsed.rationale or "The DVM Guide does not name a housekeeping program for this table."}


def _search_sap_for_me_batch(
    pending: dict[str, str],
    on_progress: Optional[Callable[[str], None]],
    settings: llm.Settings,
) -> dict[str, dict]:
    """One batched SAP for Me pass over every table in *pending* (table_name
    -> DVM-guide rationale, "" if the guide had no entry at all for it),
    sharing a single logged-in browser session opened once for the whole
    pass."""
    results: dict[str, dict] = {}

    try:
        session = sap_for_me.open_session()
    except sap_for_me.SapForMeLoginError as exc:
        logger.warning("SAP for Me login failed: %s", exc)
        for name, guide_rationale in pending.items():
            results[name] = _fallback_result(guide_rationale, f"SAP for Me sign-in failed: {exc}")
        return results

    if session is None:
        for name, guide_rationale in pending.items():
            results[name] = _fallback_result(
                guide_rationale,
                "SAP for Me credentials are not configured -- set them in the app to enable this lookup.",
            )
        return results

    try:
        for i, (name, guide_rationale) in enumerate(pending.items(), start=1):
            if on_progress:
                on_progress(f"Searching SAP for Me for {name} ({i} of {len(pending)})…")
            try:
                found = session.lookup(name, settings)
            except Exception as exc:
                logger.warning("SAP for Me lookup failed for table %s: %s", name, exc, exc_info=True)
                found = {"program": "", "rationale": f"SAP for Me lookup failed: {exc}"}
            if not found.get("program") and not found.get("rationale"):
                found["rationale"] = guide_rationale or "No DVM Guide entry found for this table."
            results[name] = found
    finally:
        session.close()

    return results


def _fallback_result(guide_rationale: str, reason: str) -> dict:
    rationale = guide_rationale or "No DVM Guide entry found for this table."
    return {"program": "", "rationale": f"{rationale} {reason}"}
