"""
Settle the header table of every archiving object SE16N/ARCH_DEF couldn't settle alone.

transactions/se16n.py resolves an archiving object by itself when ARCH_DEF has exactly
one top-level segment (blank Parent Segment). Every other object comes here as an
"ambiguous" entry:
  * several top-level segments, or none -> a list of candidate tables;
  * no ARCH_DEF entries at all, or the lookup failed -> NO candidates.
The goal is that no object is left without a header table. It is settled in stages that
mirror housekeeping.py (cheap evidence first, slow web search only if needed):

  1. DVM Guide pass -- the Guide section of each candidate table, plus the few sections
     that mention the archiving object, are given to the AI model. The Guide is organised
     by table, not by object, so this is supporting evidence rather than a lookup. With
     nothing to read, the model answers from its own SAP knowledge, which is never
     reported as more than Low confidence. A "high" answer backed by evidence stops here.
  2. SAP for Me pass -- for objects still not high-confidence: search
     "<object> header table" (then "<object> archiving object tables"), read the top SAP
     Notes / Knowledge Base Articles (one browser session shared by all objects), and ask
     the model again with that plus the Guide evidence.

With candidates, the model may only choose one of them; with none, it names the table
itself (checked to look like a table name). When the evidence is inconclusive the best
guess is still returned, flagged "Low" confidence with the other candidates (if any) in
Comments, so a reviewer or the reference-document step can correct it. A header table is
left blank only if no AI model can be used at all and ARCH_DEF gave no candidates.
"""

import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

import dvm_guide
import llm
import sap_for_me

logger = logging.getLogger(__name__)

MAX_CANDIDATES_SHOWN = 10
TABLE_NAME = re.compile(r"^[A-Z0-9_/]{2,30}$")

SYSTEM_PROMPT = (
    "You are an SAP data archiving expert. An SAP archiving object stores its data in "
    "several tables arranged as a tree of segments (table ARCH_DEF). Decide which table "
    "is the object's HEADER table: the main/root table of the business document or entity "
    "being archived (for example EKKO for purchasing documents, BKPF for accounting "
    "documents), as opposed to an item, text, change-log or helper table. Base the answer "
    "on the evidence provided (excerpts from SAP's Data Management Guide, SAP Notes and "
    "Knowledge Base Articles) and on the segment structure. Set confidence to 'high' only "
    "if the evidence states or clearly implies the header table, 'medium' for a sound "
    "inference, and 'low' if you are guessing or relying on memory alone. Always answer "
    "with a single table name."
)
WITH_CANDIDATES = (
    " You may ONLY answer with one of the candidate tables listed, spelled exactly as listed."
)
NO_CANDIDATES = (
    " ARCH_DEF gave no candidates for this object, so name the header table yourself from "
    "the evidence; if there is no useful evidence, give your best answer from your own SAP "
    "knowledge with confidence 'low'. Answer with the bare table name (no description). "
    "Never invent a table you do not believe exists."
)


class HeaderTableChoice(BaseModel):
    header_table: str = Field(description="The header table name, exactly as listed when candidates are given.")
    confidence: Literal["high", "medium", "low"]
    rationale: str = Field(default="", description="One short sentence citing the evidence.")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def resolve(
    rows: list[dict],
    ambiguous: list[dict],
    on_progress: Optional[Callable[[str], None]] = None,
    settings: Optional[llm.Settings] = None,
) -> list[dict]:
    """Return *rows* with every object in *ambiguous* filled in (input is not modified).

    *ambiguous* entries are {"archiving_object", "candidates", "segments", "reason"} as
    produced by se16n.run_batch_find_header_tables(); "candidates" may be empty.
    *on_progress*, if given, is called with short status messages (also used for Groq
    rate-limit waits). Never raises for a single object's failure: that object gets a
    best-effort row with the reason in Comments."""
    out = [dict(r) for r in rows]
    if not ambiguous:
        return out

    settings = settings or llm.load_settings()
    index = {r.get("Archiving Object", ""): r for r in out}

    if on_progress:
        llm.set_wait_hook(on_progress)
    try:
        _resolve_items(ambiguous, index, on_progress, settings)
    finally:
        if on_progress:
            llm.set_wait_hook(None)
    return out


# ---------------------------------------------------------------------------
# The two stages
# ---------------------------------------------------------------------------

def _resolve_items(items: list[dict], index: dict, on_progress, settings: llm.Settings) -> None:
    # object -> {"choice": (table, confidence, rationale) | None, "dvm": str, "note": str, "web": bool}
    state: dict[str, dict] = {}

    problem = llm.not_configured_message(settings)
    if problem:
        for item in items:
            state[item["archiving_object"]] = {
                "choice": None, "dvm": "", "web": False, "note": f"AI model not available: {problem}",
            }
        _write_rows(items, index, state)
        return

    # Stage 1: DVM Guide evidence (AI only, so a few can run at once).
    def stage1(item: dict) -> tuple[str, dict]:
        obj = item["archiving_object"]
        evidence = _dvm_evidence(obj, item["candidates"], settings)
        entry = {"choice": None, "dvm": evidence, "note": "", "web": False}
        try:
            table, confidence, rationale = _decide(item, evidence, "", settings)
            if not evidence:
                # Nothing was read: this is the model's memory, so it can't be called "high"
                # (which would also skip the SAP for Me check below).
                confidence = "low"
            entry["choice"] = (table, confidence, rationale)
        except Exception as exc:
            logger.warning("Header-table first pass failed for %s: %s", obj, exc, exc_info=True)
            entry["note"] = f"AI model could not be used: {exc}"
        return obj, entry

    if on_progress:
        on_progress(f"Checking the DVM Guide for {len(items)} archiving object(s) without a clear header table…")
    with ThreadPoolExecutor(max_workers=llm.max_workers(settings)) as pool:
        for obj, entry in pool.map(stage1, items):
            state[obj] = entry

    # Stage 2: SAP for Me for anything not already high-confidence.
    pending = [
        i for i in items
        if not (state[i["archiving_object"]]["choice"] and state[i["archiving_object"]]["choice"][1] == "high")
    ]
    if pending:
        _sap_for_me_pass(pending, state, on_progress, settings)

    _write_rows(items, index, state)


def _sap_for_me_pass(pending: list[dict], state: dict, on_progress, settings: llm.Settings) -> None:
    def note_all(text: str) -> None:
        for item in pending:
            s = state[item["archiving_object"]]
            s["note"] = (s["note"] + " " + text).strip()

    try:
        session = sap_for_me.open_session()
    except sap_for_me.SapForMeLoginError as exc:
        logger.warning("SAP for Me sign-in failed for header-table lookup: %s", exc)
        note_all(f"SAP for Me was not used: {exc}")
        return
    if session is None:
        note_all("SAP for Me was not used: credentials are not configured.")
        return

    try:
        for i, item in enumerate(pending, start=1):
            obj = item["archiving_object"]
            s = state[obj]
            if on_progress:
                on_progress(f"Searching SAP for Me for {obj} header table ({i} of {len(pending)})…")
            try:
                articles, problems = [], []
                for query in (f"{obj} header table", f"{obj} archiving object tables"):
                    articles, problem = session.fetch_articles(query, obj, settings, subject=obj)
                    if articles:
                        break
                    problems.append(problem)
                if not articles:
                    s["note"] = (s["note"] + " " + (problems[0] if problems else "")).strip()
                    continue
                article_text = "\n\n".join(
                    f"Article {n + 1} ({kind}) - {title}:\n---\n{text}\n---"
                    for n, (title, kind, text) in enumerate(articles)
                )
                s["choice"] = _decide(item, s["dvm"], article_text, settings)
                s["web"] = True
            except Exception as exc:
                logger.warning("SAP for Me header-table lookup failed for %s: %s", obj, exc, exc_info=True)
                s["note"] = (s["note"] + f" SAP for Me lookup failed: {sap_for_me._describe(exc)}").strip()
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Evidence, the model call, and output rows
# ---------------------------------------------------------------------------

def _dvm_evidence(obj: str, candidates: list[str], settings: llm.Settings) -> str:
    """DVM Guide text relevant to *obj*: each candidate table's own section (if any), plus
    passages that mention the object. Sized from the active model's budget."""
    total = llm.budget(settings)["dvm_excerpt_chars"] * 2
    per_part = max(500, total // (min(len(candidates), 6) + 2))

    parts: list[str] = []
    for table in candidates[:6]:
        ref = dvm_guide.get_reference(table)
        if ref:
            parts.append(f"DVM Guide section for candidate table {table}:\n{ref[:per_part]}")
    for hit in dvm_guide.sections_naming(obj, limit=2 if candidates else 3, window=per_part // 2):
        parts.append(
            f"DVM Guide passage mentioning {obj} (section covering {', '.join(hit['tables'][:5])}):\n{hit['excerpt']}"
        )
    return "\n\n".join(parts)[:total]


def _decide(item: dict, dvm_text: str, article_text: str, settings: llm.Settings) -> tuple[str, str, str]:
    """One model call. Returns (table, confidence, rationale). With candidates the table is
    always one of them (an answer outside the list is replaced by the first, low); without,
    it must at least look like a table name."""
    candidates = item["candidates"][:MAX_CANDIDATES_SHOWN]
    structure = "\n".join(
        f"  {s.get('Segment', '')}  (parent: {s.get('Parent Segment') or 'none - top level'})"
        for s in item.get("segments", [])[:30]
    )
    if candidates:
        header = f"Candidate header tables (answer with exactly one of these): {', '.join(candidates)}\n\n"
        header += f"ARCH_DEF segments:\n{structure or '  (not available)'}\n\n"
    else:
        header = f"Note: {item.get('reason') or 'ARCH_DEF gave no candidate tables for this object.'}\n\n"
    user = (
        f"Archiving object: {item['archiving_object']}\n{header}"
        f"Evidence from the DVM Guide:\n{dvm_text or '(none found)'}\n\n"
        f"Evidence from SAP for Me:\n{article_text or '(not searched)'}\n"
    )
    system = SYSTEM_PROMPT + (WITH_CANDIDATES if candidates else NO_CANDIDATES)
    result: HeaderTableChoice = llm.structured(system, user, HeaderTableChoice, max_tokens=400, settings=settings)
    return _validate(result, candidates)


def _validate(result: HeaderTableChoice, candidates: list[str]) -> tuple[str, str, str]:
    chosen = (result.header_table or "").strip().upper()
    if candidates:
        by_upper = {c.upper(): c for c in candidates}
        if chosen in by_upper:
            return by_upper[chosen], result.confidence, result.rationale.strip()
        return (
            candidates[0],
            "low",
            f"The model answered '{result.header_table}', which is not one of the candidates; using the first candidate.",
        )
    if TABLE_NAME.match(chosen):
        return chosen, result.confidence, result.rationale.strip()
    raise ValueError(f"the model's answer '{result.header_table}' is not a table name")


def _write_rows(items: list[dict], index: dict, state: dict) -> None:
    for item in items:
        obj = item["archiving_object"]
        row = index.get(obj)
        if row is None:
            continue
        s = state[obj]
        candidates = item["candidates"]
        reason = item.get("reason", "")

        if s["choice"]:
            table, confidence, rationale = s["choice"]
            had_dvm = bool(s["dvm"])
            if s["web"]:
                source = "DVM Guide + SAP for Me" if had_dvm else "SAP for Me"
            else:
                source = "DVM Guide" if had_dvm else "AI model knowledge"
            confidence_label = confidence.capitalize()
        elif candidates:
            table, rationale = candidates[0], ""
            source, confidence_label = "ARCH_DEF (first candidate)", "Low"
        else:
            table, rationale, source, confidence_label = "", "", "", ""

        comments = [reason] if reason else []
        if rationale:
            comments.append(rationale)
        if table and confidence_label != "High":
            others = [c for c in candidates if c != table]
            comments.append(
                "Best guess - please verify." + (f" Other candidates: {', '.join(others)}." if others else "")
            )
        if not table:
            comments.append("No header table could be determined.")
        if s["note"]:
            comments.append(s["note"])

        row["Header Table"] = table
        row["Source"] = source
        row["Confidence"] = confidence_label
        row["Comments"] = " ".join(c for c in comments if c).strip()
