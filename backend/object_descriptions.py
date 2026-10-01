"""
Descriptions of archiving objects ("FI_DOCUMNT" -> "Financial Accounting Documents"), so that no
row in an exported sheet is left with an object but no description.

A description belongs to the OBJECT, not to a table, so it can be reused wherever the object
appears. resolve() looks one up through four layers and never returns a blank:

  1. known      -- descriptions already on rows of the current run (DB15 results);
  2. remembered -- a local list of every description seen before (DB15 and SAP), kept in
                   backend/resources/archiving_object_descriptions.json (git-ignored);
  3. SAP        -- the archiving-object text table read through SE16N, only when SAP is connected;
  4. AI         -- the selected AI model's best guess, always prefixed "(AI-suggested)" and never
                   remembered, because it may not match SAP's wording; or "(description not found)"
                   if no model is available.

Placeholders such as "(no archiving objects found)" are never treated as descriptions.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

import llm

logger = logging.getLogger(__name__)

CACHE_PATH = Path(__file__).parent / "resources" / "archiving_object_descriptions.json"
AI_PREFIX = "(AI-suggested) "
NOT_FOUND = "(description not found)"
MAX_AI_OBJECTS = 40

_lock = threading.Lock()


def is_real(description) -> bool:
    """True for an actual description: not blank and not a "(...)" placeholder or AI label."""
    d = (description or "").strip()
    return bool(d) and not d.startswith("(")


def _missing(description) -> bool:
    """Blank, or the "(no archiving object found)" placeholder -- but an AI-suggested or
    not-found marker counts as filled, so it is not looked up again."""
    d = (description or "").strip()
    return not d or d.lower().startswith("(no archiving object")


# ---------------------------------------------------------------------------
# Layer 2: the remembered list
# ---------------------------------------------------------------------------

def load_cache() -> dict[str, str]:
    with _lock:
        try:
            if CACHE_PATH.exists():
                data = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
                return {str(k).upper(): str(v) for k, v in data.items() if is_real(v)}
        except Exception:
            logger.warning("Could not read %s; starting with an empty list.", CACHE_PATH)
    return {}


def remember(descriptions: dict[str, str]) -> None:
    """Add real descriptions to the remembered list (existing entries are kept)."""
    fresh = {str(k).strip().upper(): str(v).strip() for k, v in descriptions.items() if k and is_real(v)}
    if not fresh:
        return
    current = load_cache()
    merged = {**fresh, **current}   # what was remembered first wins; new objects are added
    if merged == current:
        return
    with _lock:
        try:
            CACHE_PATH.write_text(json.dumps(dict(sorted(merged.items())), indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            logger.warning("Could not write %s", CACHE_PATH)


def known_from_rows(rows: list[dict]) -> dict[str, str]:
    """{OBJECT: description} from rows that carry both, first real description per object."""
    out: dict[str, str] = {}
    for row in rows:
        obj = (row.get("Archiving Object") or "").strip().upper()
        desc = (row.get("Object Description") or "").strip()
        if obj and is_real(desc) and obj not in out:
            out[obj] = desc
    return out


def remember_from_rows(rows: list[dict]) -> None:
    remember(known_from_rows(rows))


# ---------------------------------------------------------------------------
# Resolving
# ---------------------------------------------------------------------------

def resolve(
    objects: list[str],
    known: Optional[dict[str, str]] = None,
    settings: Optional[llm.Settings] = None,
    allow_sap: bool = True,
    allow_ai: bool = True,
) -> dict[str, str]:
    """{OBJECT: description} for every name in *objects*; never blank (see module docstring)."""
    wanted = list(dict.fromkeys((o or "").strip().upper() for o in objects if (o or "").strip()))
    known = {k.strip().upper(): v for k, v in (known or {}).items() if is_real(v)}
    cache = load_cache()

    out: dict[str, str] = {}
    authoritative: dict[str, str] = {}
    for obj in wanted:
        if obj in known:
            out[obj] = authoritative[obj] = known[obj]
        elif obj in cache:
            out[obj] = cache[obj]

    missing = [o for o in wanted if o not in out]
    if missing and allow_sap:
        for obj, text in _from_sap(missing).items():
            out[obj] = authoritative[obj] = text
        missing = [o for o in wanted if o not in out]

    if missing and allow_ai:
        for obj, text in _from_ai(missing, settings).items():
            out[obj] = AI_PREFIX + text

    for obj in wanted:
        out.setdefault(obj, NOT_FOUND)

    remember(authoritative)
    return out


def fill_missing(rows: list[dict]) -> list[dict]:
    """Copy of *rows* where every row with an archiving object but no description gets one from
    the same object elsewhere in the list or from the remembered list. Cheap layers only: no SAP
    and no AI call, so it is safe to run when saving or exporting."""
    known = {**load_cache(), **known_from_rows(rows)}
    out = []
    for row in rows:
        row = dict(row)
        obj = (row.get("Archiving Object") or "").strip().upper()
        if obj and _missing(row.get("Object Description")) and obj in known:
            row["Object Description"] = known[obj]
        out.append(row)
    return out


# ---------------------------------------------------------------------------
# Layers 3 and 4
# ---------------------------------------------------------------------------

def _from_sap(objects: list[str]) -> dict[str, str]:
    try:
        from sap_connector import sap
        from transactions import se16n

        if not sap.is_connected:
            return {}
        result = se16n.get_archiving_object_texts(objects)
        return {k.upper(): v for k, v in result.get("texts", {}).items() if is_real(v)}
    except Exception as exc:
        logger.warning("SAP description lookup failed: %s", exc, exc_info=True)
        return {}


class _ObjectDescription(BaseModel):
    archiving_object: str
    description: str


class _ObjectDescriptions(BaseModel):
    items: list[_ObjectDescription]


_AI_SYSTEM = (
    "You are an SAP data archiving expert. For each SAP archiving object name given, reply with "
    "SAP's short standard description of what the object archives, as shown in transactions "
    "SARA or DB15 (for example FI_DOCUMNT -> 'Financial Accounting Documents', SD_VBAK -> "
    "'Sales Documents'). Use at most 8 words. If you do not recognise an object, give an empty "
    "description for it -- never invent one."
)


def _from_ai(objects: list[str], settings: Optional[llm.Settings]) -> dict[str, str]:
    settings = settings or llm.load_settings()
    if llm.not_configured_message(settings):
        return {}
    try:
        names = objects[:MAX_AI_OBJECTS]
        result: _ObjectDescriptions = llm.structured(
            _AI_SYSTEM, "Archiving objects:\n" + "\n".join(f"- {n}" for n in names),
            _ObjectDescriptions, max_tokens=1200, settings=settings,
        )
        asked = set(names)
        return {
            item.archiving_object.strip().upper(): item.description.strip()
            for item in result.items
            if item.archiving_object.strip().upper() in asked and item.description.strip()
        }
    except Exception as exc:
        logger.warning("AI description lookup failed: %s", exc, exc_info=True)
        return {}
