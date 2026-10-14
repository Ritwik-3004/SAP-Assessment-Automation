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

import batching
import llm
import reference_doc
from completeness import Tally

logger = logging.getLogger(__name__)

MAX_CHUNKS = 8                  # parts of one document that are read; what is beyond them is reported, not dropped
MATCH_TOKENS_PER_CONDITION = 45  # one {"doc_index": 3, "status": "conflict", "tool_index": 2, "note": "..."} verdict
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


def _part_size(settings: llm.Settings) -> int:
    """Characters of a document one request reads: what the model takes in, and no more than its answer can plausibly
    carry (a part holding more than that is cut off and has to be split, which costs an extra request)."""
    return min(llm.budget(settings)["reference_chars"], max(2000, llm.max_output_tokens(settings) * 4))


def extract_reference(
    files: list[tuple[bytes, str]],
    object_names: list[str],
    settings: llm.Settings,
    on_progress: Optional[Callable[[str], None]] = None,
) -> dict:
    """What the documents say about archiving objects.

    Returns {"objects": {OBJECT: {"conditions": [{"text", "file"}], "archive_before": [{"object", "file"}]}},
             "used": [filenames], "warnings": [str], "completeness": {...}}. A file that cannot be read is skipped
    with a warning. Each document is read part by part (a part whose answer was cut off is split in two); at most
    MAX_CHUNKS parts of a document are read, and what was not read is in "warnings" and "completeness".
    Raises llm.LLMError if the AI model cannot be used (a rejected key, a rate limit)."""
    size = _part_size(settings)
    max_tokens = min(llm.max_output_tokens(settings), 4000)
    hint = ", ".join(object_names[:60])
    objects: dict[str, dict] = {}
    used: list[str] = []
    warnings: list[str] = []
    tally = Tally()

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

        def ask(piece: str) -> "_RefObjects":
            user = f"Archiving objects of particular interest (include others you find too): {hint}\n\nDOCUMENT TEXT:\n{piece}"
            return llm.structured(_READ_SYSTEM, user, _RefObjects, max_tokens=max_tokens, settings=settings)

        def progress(done: int, total: int, name: str = filename) -> None:
            if on_progress:
                on_progress(f"Analysing {name} ({done} of {total})…")

        read = batching.run_text_parts(
            text, ask, size=size, max_parts=MAX_CHUNKS, what=filename, tally=tally, on_part=progress
        )
        if not read.results and read.missing:
            if read.fatal is not None:
                raise read.fatal
            warnings.append(f"{filename}: the model could not read it ({read.missing[0][1]}).")
            continue
        used.append(filename)
        for result in read.results:
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
    warnings.extend(tally.sentences())
    return {"objects": objects, "used": used, "warnings": warnings, "completeness": tally.summary()}


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
                      "conflicts": [{"id", "document", "tool", "note", "file"}],
                      "not_compared": [{"text", "file", "reason"}]}}
    Items a user can select (gaps and mismatches) carry an "id". Document conditions that could not be compared
    (the model's answer was cut off, a limit was reached, ...) are listed under "not_compared" with the reason, never
    as gaps; the review's "completeness" says what, if anything, could not be done."""
    reviews: list[dict] = []
    totals = {"confirmed": 0, "gaps": 0, "mismatches": 0, "not_compared": 0}
    not_in_document: list[str] = []
    tally = Tally.from_summary(ref.get("completeness"))
    stopped: Optional[BaseException] = None        # a limit stop: nothing more is asked of the model after it
    max_tokens = min(llm.max_output_tokens(settings), 4000)
    batch_size = batching.items_per_request(settings, MATCH_TOKENS_PER_CONDITION, overhead_out_tokens=100, cap=40)

    for number, res in enumerate(results, start=1):
        obj = res["object"]
        entry = ref["objects"].get(obj)
        review = {
            "object": obj,
            "in_document": entry is not None,
            "dependencies": {"stated": False, "confirmed": [], "only_tool": [], "only_document": []},
            "conditions": {"covered": [], "new": [], "conflicts": [], "not_compared": []},
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
        not_compared: dict[int, str] = {}       # document condition number -> why it was not compared
        if tool_conds:
            if stopped is not None:
                not_compared = {i: f"not processed: {batching._reason(stopped)}" for i in range(1, len(doc_conds) + 1)}
            else:
                tool_lines = ["TOOL conditions:"] + [f"{i}. {c['condition']}" for i, c in enumerate(tool_conds, start=1)]

                def ask(batch: list, obj: str = obj, tool_lines: list = tool_lines) -> dict:
                    lines = tool_lines + ["", "DOCUMENT conditions:"] + [f"{i}. {c['text']}" for i, c in batch]
                    answer = llm.structured(
                        _MATCH_SYSTEM, f"Archiving object: {obj}\n\n" + "\n".join(lines), _Matches,
                        max_tokens=max_tokens, settings=settings,
                    )
                    return {m.doc_index: m for m in answer.items}

                compared = batching.run_batched(
                    list(enumerate(doc_conds, start=1)), ask, key=lambda numbered: numbered[0], size=batch_size
                )
                verdicts = compared.results
                stopped = compared.fatal
                for (i, _cond), reason in compared.missing:
                    not_compared[i] = reason
        for i, cond in enumerate(doc_conds, start=1):
            verdict = verdicts.get(i)
            tool_ok = verdict is not None and 1 <= verdict.tool_index <= len(tool_conds)
            if verdict is not None and verdict.status != "new" and not tool_ok:
                not_compared[i] = "the model's verdict did not say which tool condition it refers to"
            if i in not_compared:
                review["conditions"]["not_compared"].append(
                    {"text": cond["text"], "file": cond["file"], "reason": not_compared[i]}
                )
                totals["not_compared"] += 1
                continue
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
            else:   # "new", or nothing to compare with (the tool found no conditions): a gap the user can add
                review["conditions"]["new"].append(
                    {"id": f"{obj}|new|{len(review['conditions']['new'])}", "text": cond["text"], "file": cond["file"]}
                )
                totals["gaps"] += 1
        if review["conditions"]["not_compared"]:
            skipped = review["conditions"]["not_compared"]
            tally.miss(
                f"{obj}: conditions",
                f"{len(skipped)} of {len(doc_conds)} document conditions could not be compared ({skipped[0]['reason']})",
            )

    return {
        "objects": reviews,
        "summary": {**totals, "not_in_document": not_in_document, "files": ref["used"]},
        "completeness": tally.summary(),
    }
