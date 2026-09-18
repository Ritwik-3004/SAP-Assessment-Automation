"""
Housekeeping/cleanup program lookup for tables that have NO archiving object
(zero DB15 candidates). Not every SAP table is covered by a formal archiving
object -- some (e.g. log, temporary, or staging tables) are instead cleaned
up via a standalone housekeeping/cleanup program, report, or transaction.
For those tables, this module looks for such a recommendation instead of
just leaving the table with nothing.

Two-stage lookup, per the user's plan:
  1. SAP's official DVM Guide (implemented here) -- the same per-table
     excerpt already used to ground archiving-object scoring in scoring.py
     (dvm_guide.get_reference()), read by an LLM specifically for a
     housekeeping/cleanup recommendation rather than an archiving object.
  2. SAP for Me portal search ("<table name> housekeeping programs") +
     article scraping + LLM extraction -- NOT YET IMPLEMENTED, pending SAP
     for Me portal access (currently requested, not yet granted).
     search_sap_for_me() below is a stub that always returns None until
     that access lands. find_housekeeping_programs() already treats it as
     a fallback after the DVM Guide, so wiring in the real scraper later
     needs no changes to scoring.py or main.py.
"""

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from pydantic import BaseModel, Field

import dvm_guide
from config import ANTHROPIC_API_KEY, SCORING_MODEL

logger = logging.getLogger(__name__)

MAX_WORKERS = 8

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


def find_housekeeping_programs(table_names: list[str]) -> dict[str, dict]:
    """
    For each table in *table_names*, look for a housekeeping/cleanup program
    grounded in the DVM Guide, falling back to SAP for Me (once available)
    for tables the guide doesn't cover.

    Returns {table_name: {"program": str, "rationale": str}}. "program" is
    empty when nothing was found -- "rationale" still explains why so the
    caller never has to show a blank cell with no context.
    """
    if not ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not set -- skipping housekeeping-program lookup.")
        return {}

    references = {name: dvm_guide.get_reference(name) for name in table_names}
    with_reference = {name: ref for name, ref in references.items() if ref}

    results: dict[str, dict] = {}
    for name in table_names:
        if name not in with_reference:
            results[name] = _search_beyond_dvm_guide(name)

    if not with_reference:
        return results

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_table = {
            pool.submit(_find_one, client, name, reference): name
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
                # DVM Guide didn't name one -- fall through to SAP for Me
                # (a no-op today) rather than reporting "not found" purely
                # off the guide.
                results[name] = _search_beyond_dvm_guide(name, guide_rationale=found["rationale"])

    return results


def _find_one(client: anthropic.Anthropic, table_name: str, reference: str) -> dict:
    user_content = (
        f"Table: {table_name}\n\n"
        f"DVM Guide excerpt:\n---\n{reference}\n---\n\n"
        "Does this excerpt name a housekeeping/cleanup program (not an archiving object) for this table?"
    )
    response = client.messages.parse(
        model=SCORING_MODEL,
        max_tokens=500,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_format=HousekeepingResult,
    )
    parsed: HousekeepingResult = response.parsed_output
    if parsed.found and parsed.program.strip():
        return {"program": parsed.program.strip(), "rationale": parsed.rationale}
    return {"program": "", "rationale": parsed.rationale or "The DVM Guide does not name a housekeeping program for this table."}


def _search_beyond_dvm_guide(table_name: str, guide_rationale: str = "") -> dict:
    """Fallback for a table the DVM Guide has no entry for (or no
    housekeeping recommendation in). Tries SAP for Me next; until that
    access lands, search_sap_for_me() always returns None, so this reports
    the honest current state rather than a false negative."""
    sap_for_me = search_sap_for_me(table_name)
    if sap_for_me:
        return sap_for_me

    rationale = guide_rationale or "No DVM Guide entry found for this table."
    return {
        "program": "",
        "rationale": f"{rationale} SAP for Me lookup not yet available (pending portal access).",
    }


def search_sap_for_me(table_name: str) -> "dict | None":
    """
    STUB -- pending SAP for Me portal access approval. Once granted, this
    should search SAP for Me for "<table_name> housekeeping programs",
    scrape the relevant articles, and use an LLM to extract the correct
    housekeeping program from them, returning {"program": str, "rationale":
    str} in the same shape find_housekeeping_programs() uses elsewhere.
    Always returns None for now.
    """
    return None
