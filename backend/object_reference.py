"""
Reference-document check for the Archiving Object Analysis.

A reference document (past project analysis, SME notes, a deck, a spreadsheet -- any type reference_doc can
read) says things about archiving objects. This extracts, per object, what it says about

  * ARCHIVING CONDITIONS, and
  * the archiving objects that must be ARCHIVED BEFORE it,

and compares that with what the analysis found, so the user can fill gaps and see mismatches:

  dependencies   confirmed (both)  |  only in the document (a gap: candidate to add)  |  only in the tool
  conditions     covered (the tool already has it)  |  new (a gap)  |  conflict (same subject, different
                 requirement, e.g. another residence period: a mismatch)

Nothing is changed here; compare() returns a review whose gap/mismatch items carry ids the user can select,
and the job applies only those. Conditions are matched by the AI model, which sees only the two lists.
"""

import logging
import re
from typing import Callable, Literal, Optional

from pydantic import BaseModel, Field

import llm
import reference_doc

logger = logging.getLogger(__name__)

MAX_CHUNKS = 8
_OBJECT_NAME = re.compile(r"^[A-Z0-9_/]{2,30}$")


def norm_object(value) -> str:
    return re.sub(r"\s+", "", str(value or "")).upper()


def _norm_text(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


# ---------------------------------------------------------------------------
# Reading the document
# ---------------------------------------------------------------------------

class _RefObject(BaseModel):
    object: str = Field(description="SAP archiving object name in capitals, e.g. FI_DOCUMNT.")
    conditions: list[str] = Field(
        default_factory=list,
        description="Each archiving condition the document states for this object, as a short self-contained sentence.",
    )
    archive_before: list[str] = Field(
        default_factory=list,
        description="Names of archiving objects the document says must be archived BEFORE this one.",
    )


class _RefObjects(BaseModel):
    objects: list[_RefObject] = Field(default_factory=list)


_READ_SYSTEM = (
    "You read a reference document from an SAP archiving project (it may be a spreadsheet, slides, a PDF or notes) "
    "and extract what it says about SAP ARCHIVING OBJECTS. For each archiving object named in the text give (a) its "
    "ARCHIVING CONDITIONS - requirements that must hold or be done before data can be archived or deleted with it: "
    "checks, required status, minimum residence/retention periods, required Customizing, blockers - each as one short "
    "self-contained sentence, and (b) the archiving objects the document says must be archived BEFORE it (prerequisite "
    "or predecessor objects). Use ONLY the text; never add anything from your own knowledge. Object names are SAP "
    "names such as FI_DOCUMNT or MM_EKKO, in capitals. Skip objects the text says nothing about. If the text holds "
    "nothing of this kind, return an empty list."
)


def _chunks(text: str, size: int) -> list[str]:
    """Split *text* into pieces of about *size* characters on line breaks."""
    out, current, length = [], [], 0
    for line in text.splitlines():
        if length + len(line) + 1 > size and current:
            out.append("\n".join(current))
            current, length = [], 0
        current.append(line)
        length += len(line) + 1
    if current:
        out.append("\n".join(current))
    return out


def extract_reference(
    files: list[tuple[bytes, str]],
    object_names: list[str],
    settings: llm.Settings,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """What the documents say about archiving objects.

    Returns {"objects": {OBJECT: {"conditions": [{"text", "file"}], "archive_before": [{"object", "file"}]}},
             "used": [filenames], "warnings": [str]}. A file that cannot be read is skipped with a warning.
    Raises llm.LLMError if the AI model cannot be used."""
    size = llm.budget(settings)["reference_chars"]
    hint = ", ".join(object_names[:60])
    objects: dict[str, dict] = {}
    used: list[str] = []
    warnings: list[str] = []

    for contents, filename in files:
        if on_progress:
            on_progress(f"Reading {filename}…")
        try:
            text = reference_doc._extract_text(contents, filename)
        except ValueError as exc:
            warnings.append(str(exc))
            continue
        except Exception as exc:
            warnings.append(f"{filename}: could not be read ({exc})")
            continue
        if not text.strip():
            warnings.append(f"{filename}: no text could be extracted (a scanned or image-only file?)")
            continue
        used.append(filename)

        pieces = _chunks(text, size)
        if len(pieces) > MAX_CHUNKS:
            warnings.append(
                f"{filename}: only the first {MAX_CHUNKS * size:,} characters were read (the document is longer)."
            )
            pieces = pieces[:MAX_CHUNKS]
        for n, piece in enumerate(pieces, start=1):
            if on_progress:
                on_progress(f"Analysing {filename} ({n} of {len(pieces)})…")
            user = f"Archiving objects of particular interest (include others you find too): {hint}\n\nDOCUMENT TEXT:\n{piece}"
            result = llm.structured(_READ_SYSTEM, user, _RefObjects, max_tokens=2500, settings=settings)
            for item in result.objects:
                name = norm_object(item.object)
                if not _OBJECT_NAME.match(name):
                    continue
                slot = objects.setdefault(name, {"conditions": [], "archive_before": []})
                have_c = {_norm_text(c["text"]) for c in slot["conditions"]}
                for cond in item.conditions[:30]:
                    key = _norm_text(cond)
                    if key and key not in have_c:
                        have_c.add(key)
                        slot["conditions"].append({"text": cond.strip(), "file": filename})
                have_b = {b["object"] for b in slot["archive_before"]}
                for dep in item.archive_before:
                    dep_name = norm_object(dep)
                    if _OBJECT_NAME.match(dep_name) and dep_name != name and dep_name not in have_b:
                        have_b.add(dep_name)
                        slot["archive_before"].append({"object": dep_name, "file": filename})
    return {"objects": objects, "used": used, "warnings": warnings}


# ---------------------------------------------------------------------------
# Comparing it with the analysis
# ---------------------------------------------------------------------------

class _Match(BaseModel):
    doc_index: int = Field(description="Number of the document condition this verdict is about.")
    status: Literal["covered", "conflict", "new"]
    tool_index: int = Field(default=0, description="Number of the matching tool condition for covered/conflict, else 0.")
    note: str = Field(default="", description="For a conflict: one short sentence on how they differ.")


class _Matches(BaseModel):
    items: list[_Match] = Field(default_factory=list)


_MATCH_SYSTEM = (
    "You compare two numbered lists of SAP archiving conditions for the same archiving object: the TOOL list (what "
    "an analysis found) and the DOCUMENT list (from a reference document). For EACH document condition decide: "
    "'covered' - the tool list already states the same requirement (give the tool number); 'conflict' - the tool list "
    "has a condition about the same thing that CANNOT also be true, because it demands something different or "
    "opposite: another threshold or period, another required status, or an exception to the tool's rule (give the "
    "tool number and say briefly how they differ); 'new' - the tool list says nothing about it, OR the document only "
    "adds detail to a general tool condition (a concrete value, an example, more specific wording) - that is "
    "information the tool lacks, not a contradiction (tool number 0). Judge meaning, not wording."
)


def compare(
    settings: llm.Settings,
    results: list[dict],
    ref: dict,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """The review of *ref* (from extract_reference) against the analysis *results* (object_analysis result dicts).

    Returns {"objects": [per-object review], "summary": {...}}. Per-object review:
      {"object", "in_document": bool,
       "dependencies": {"stated": bool, "confirmed": [names], "only_tool": [names],
                        "only_document": [{"id", "object", "file"}]},
       "conditions": {"covered": [{"document", "tool", "file"}],
                      "new": [{"id", "text", "file"}],
                      "conflicts": [{"id", "document", "tool", "note", "file"}]}}
    Items a user can select (gaps and mismatches) carry an "id"."""
    reviews: list[dict] = []
    totals = {"confirmed": 0, "gaps": 0, "mismatches": 0}
    not_in_document: list[str] = []

    for number, res in enumerate(results, start=1):
        obj = res["object"]
        entry = ref["objects"].get(obj)
        review = {
            "object": obj,
            "in_document": entry is not None,
            "dependencies": {"stated": False, "confirmed": [], "only_tool": [], "only_document": []},
            "conditions": {"covered": [], "new": [], "conflicts": []},
        }
        reviews.append(review)
        if entry is None:
            not_in_document.append(obj)
            continue
        if on_progress:
            on_progress(f"Comparing {obj} ({number} of {len(results)})…")

        # --- dependencies: by name
        tool_prereqs = [p["object"] for p in res["prerequisites"] if not p.get("from_reference")]
        doc_prereqs = entry["archive_before"]
        review["dependencies"]["stated"] = bool(doc_prereqs)
        doc_names = {d["object"] for d in doc_prereqs}
        if doc_prereqs:
            review["dependencies"]["confirmed"] = [n for n in tool_prereqs if n in doc_names]
            review["dependencies"]["only_tool"] = [n for n in tool_prereqs if n not in doc_names]
            for d in doc_prereqs:
                if d["object"] not in tool_prereqs:
                    review["dependencies"]["only_document"].append(
                        {"id": f"{obj}|dep|{d['object']}", "object": d["object"], "file": d["file"]}
                    )
        totals["confirmed"] += len(review["dependencies"]["confirmed"])
        totals["gaps"] += len(review["dependencies"]["only_document"])

        # --- conditions: by meaning
        doc_conds = entry["conditions"]
        tool_conds = [c for c in res["conditions"] if not c["source"].startswith("Reference document")]
        if not doc_conds:
            continue
        verdicts: dict[int, _Match] = {}
        if tool_conds:
            lines = ["TOOL conditions:"] + [f"{i}. {c['condition']}" for i, c in enumerate(tool_conds, start=1)]
            lines += ["", "DOCUMENT conditions:"] + [f"{i}. {c['text']}" for i, c in enumerate(doc_conds, start=1)]
            try:
                answer = llm.structured(
                    _MATCH_SYSTEM, f"Archiving object: {obj}\n\n" + "\n".join(lines), _Matches,
                    max_tokens=1800, settings=settings,
                )
                for m in answer.items:
                    if 1 <= m.doc_index <= len(doc_conds):
                        verdicts[m.doc_index] = m
            except llm.LLMError:
                raise
            except Exception as exc:
                logger.warning("Could not compare conditions of %s: %s", obj, exc)
        for i, cond in enumerate(doc_conds, start=1):
            verdict = verdicts.get(i)
            tool_ok = verdict is not None and 1 <= verdict.tool_index <= len(tool_conds)
            if verdict is not None and verdict.status == "covered" and tool_ok:
                review["conditions"]["covered"].append(
                    {"document": cond["text"], "tool": tool_conds[verdict.tool_index - 1]["condition"], "file": cond["file"]}
                )
                totals["confirmed"] += 1
            elif verdict is not None and verdict.status == "conflict" and tool_ok:
                review["conditions"]["conflicts"].append({
                    "id": f"{obj}|conflict|{len(review['conditions']['conflicts'])}",
                    "document": cond["text"], "tool": tool_conds[verdict.tool_index - 1]["condition"],
                    "note": verdict.note.strip(), "file": cond["file"],
                })
                totals["mismatches"] += 1
            else:   # "new", or no usable verdict: treated as a gap the user can add
                review["conditions"]["new"].append(
                    {"id": f"{obj}|new|{len(review['conditions']['new'])}", "text": cond["text"], "file": cond["file"]}
                )
                totals["gaps"] += 1

    return {
        "objects": reviews,
        "summary": {**totals, "not_in_document": not_in_document, "files": ref["used"]},
    }
