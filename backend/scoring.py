"""
Score archiving objects for relevance per table, using the AI model selected in
the app (Claude or Groq, see llm.py), and derive a recommended (highest-scoring)
object per table.

Takes the output of db15.run_batch() -- rows shaped
{"Table Name", "Table Description", "Volume (GB)", "Volume (MB)",
"Archiving Object", "Object Description"}, one row per (table, candidate
archiving object) pair -- and adds a Score (0-100) to every row, plus a
separate "recommended" list with one row per table (the max-score
candidate) and a Rationale column. Volume (GB)/Volume (MB) are carried
through unchanged (they're per-table facts, not something to score).

One model call per table (not per object): all of a table's candidates
are scored together in a single structured response, which is both cheaper
and lets the model reason comparatively across that table's own candidates.
Tables with a single candidate skip the API call entirely (nothing to
compare against).

A table with more candidates than one answer can hold (CDHDR and CDPOS have 374
each) is scored in two phases: the candidates in chunks (scores only), then a
"playoff" of the best ones together, so the winners are compared with each other
(scores from different chunks are not comparable). Only the top pick gets a
rationale: it is the only one anything shows, and output tokens are what free-tier
models are short of. A request is also never allowed to ask for more output than
the model can give (llm.max_output_tokens).

Nothing is thrown away or guessed: when a limit stops the run, the tables that are
done keep their scores and the others are listed under "unscored" with the reason
(a table where no candidate could be scored gets no recommended object, not the
first candidate). scoring.merge_results() puts a re-run of just those tables back
into the earlier result.

A table with zero archiving-object candidates is not just dropped: it's
looked up in housekeeping.py for a housekeeping/cleanup program instead (see
that module for the DVM-Guide-first / SAP-for-Me-later lookup order), and
still gets a row in both "rows" and "recommended" either way -- with the
result in a separate "Housekeeping Program" column, never mixed into
"Archiving Object", and a Rationale explaining what (if anything) was found.
"""

import logging
import re
from collections import OrderedDict
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from typing import Optional

from pydantic import BaseModel, Field

import dvm_guide
import housekeeping
import llm
from completeness import Tally

logger = logging.getLogger(__name__)

BASE_PROMPT = (
    "You are an SAP data archiving expert helping decide which SAP archiving "
    "object is the most appropriate one for archiving a given database table. "
    "For each candidate archiving object listed, assign a relevance score from "
    "0 (not relevant) to 100 (highly relevant -- very likely the correct "
    "primary archiving object for this table), based on typical SAP archiving "
    "practice and how closely the object's purpose matches the table. "
    "Approximate scores are fine; exact precision is not required. Score every "
    "candidate you are given, using the archiving_object names exactly as listed.\n\n"
    "You may also be given a reference excerpt from SAP's official Data "
    "Management Guide for SAP Business Suite, specific to the table being "
    "scored. Treat it as the authoritative source when present: if it names "
    "a specific recommended archiving object for this table, that candidate "
    "should score much higher than unrelated ones. If no excerpt is given, "
    "or it doesn't clearly resolve the choice, fall back on general SAP "
    "archiving knowledge."
)

# Used when the answer holds the best pick and its reason (one call per table, or the playoff of a big table)
SYSTEM_PROMPT = BASE_PROMPT + (
    "\n\nAlso name the single best candidate in top_choice (exactly as listed) and give "
    "top_rationale: ONE short sentence, ideally under 20 words, saying why. If the guide "
    "recommends it, say so. Only mention a specific SAP Note, KBA or documentation "
    "reference if you are genuinely aware of one -- do not invent one. Write no "
    "rationale for the other candidates."
)

# Used for the parts of a big table: scores only
CHUNK_PROMPT = BASE_PROMPT + (
    "\n\nThis is one part of a longer list that is scored part by part. Reply with the scores only: "
    "no explanations."
)

TOKENS_PER_CANDIDATE = 18    # one {"archiving_object": "...", "score": 95} entry of the answer, with room to spare
ANSWER_OVERHEAD = 250        # the answer's wrapper and, for the final call, the top pick and its rationale
PLAYOFF_SIZE = 8             # candidates compared together at the end for a table scored in parts
PLAYOFF_NAMED = 4            # ... of which up to this many are first the ones the DVM Guide excerpt names


class ScoredCandidate(BaseModel):
    archiving_object: str
    score: int = Field(ge=0, le=100)


class ScoreList(BaseModel):
    candidates: list[ScoredCandidate]


class ScoringResponse(BaseModel):
    candidates: list[ScoredCandidate]
    top_choice: str
    top_rationale: str


# Errors that mean "stop asking": the model cannot be used, or a limit was reached. Anything else (an answer that
# was cut off, a malformed answer) is handled by asking for fewer candidates at a time.
_FATAL = (llm.LLMAuthError, llm.LLMNotConfiguredError, llm.LLMRateLimitError)


def score_archiving_objects(rows: list[dict], on_progress=None) -> dict:
    """
    Score every (table, archiving object) row for relevance, and derive the
    recommended (highest-scoring) object per table, using the AI model currently
    chosen in the app (Claude, Groq or Ollama, see llm.py). The model is read once here,
    so switching it mid-run can't mix two models in one result set.

    While a Groq rate-limit wait is in progress, *on_progress* also receives a
    "Waiting for the Groq rate limit…" message.

    *on_progress*, if given, is called as on_progress(completed_count,
    message) once per distinct table as its scoring resolves (including
    the zero/single-candidate shortcuts) and, without moving the count, with a
    short message for each step of a big table, so a caller can report progress on
    a long-running batch. If any table has zero archiving-object candidates,
    one extra call reports a status message while the (separate,
    non-incremental) housekeeping-program lookup for those tables runs.

    Returns {"status": "ok", "rows": [...with Score...], "recommended": [...],
    "unscored": [{"table", "reason"}], "message": str}. Every table appears in both "rows" and
    "recommended" -- a table with neither an archiving object nor a housekeeping program still
    gets a row, with both columns blank and a Rationale explaining nothing was found. Tables a
    limit stopped are in "unscored" (the rest keep their scores). Only a model that cannot be
    used at all, or a limit reached before a single table was scored, returns
    {"status": "error", "message": ...}.
    """
    settings = llm.load_settings()
    problem = llm.not_configured_message(settings)
    if problem:
        return {"status": "error", "message": problem}

    # Progress callback shared by scoring, the housekeeping stage and Groq rate-limit
    # waits; "state" remembers the latest completed-count so wait messages don't
    # move the progress bar.
    state = {"completed": 0}

    def report(completed: int, message: str) -> None:
        state["completed"] = completed
        if on_progress:
            on_progress(completed, message)

    def note(message: str) -> None:
        if on_progress:
            on_progress(state["completed"], message)

    llm.set_wait_hook(note)
    try:
        return _score(rows, report, note, settings)
    finally:
        llm.set_wait_hook(None)


def _score(rows: list[dict], on_progress, note, settings: "llm.Settings") -> dict:
    tables: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        table_name = row.get("Table Name", "")
        entry = tables.setdefault(
            table_name,
            {
                "description": row.get("Table Description", ""),
                "volume_gb": row.get("Volume (GB)", ""),
                "volume_mb": row.get("Volume (MB)", ""),
                "candidates": [],
            },
        )
        obj = row.get("Archiving Object", "")
        if obj:
            entry["candidates"].append(
                {"object": obj, "description": row.get("Object Description", "")}
            )

    try:
        scores_by_table, unscored, stopped = _score_all_tables(settings, tables, on_progress, note)
    except llm.LLMError as exc:
        logger.warning("Scoring stopped: %s", exc)
        return {"status": "error", "message": f"Scoring stopped: {exc}"}

    needed = [name for name, entry in tables.items() if len(entry["candidates"]) >= 2]
    if stopped and needed and len(unscored) == len(needed):
        # a limit was reached before a single table could be scored: nothing useful to keep
        return {"status": "error", "message": f"Scoring stopped: {stopped}"}

    zero_candidate_tables = [name for name, entry in tables.items() if not entry["candidates"]]
    housekeeping_by_table: dict[str, dict] = {}
    if zero_candidate_tables:
        if on_progress:
            on_progress(
                len(tables),
                f"Looking up housekeeping programs for {len(zero_candidate_tables)} "
                "table(s) without an archiving object…",
            )
        try:
            hk_progress = (lambda msg: on_progress(len(tables), msg)) if on_progress else None
            housekeeping_by_table = housekeeping.find_housekeeping_programs(
                zero_candidate_tables, on_progress=hk_progress, settings=settings
            )
        except Exception as exc:
            logger.warning("Housekeeping-program lookup failed: %s", exc, exc_info=True)

    scored_rows = []
    recommended_rows = []
    for table_name, entry in tables.items():
        description = entry["description"]
        volume_gb = entry["volume_gb"]
        volume_mb = entry["volume_mb"]
        candidate_scores = scores_by_table.get(table_name, {})

        if not entry["candidates"]:
            hk = housekeeping_by_table.get(table_name, {})
            hk_program = hk.get("program", "")
            row = {
                "Table Name": table_name,
                "Table Description": description,
                "Volume (GB)": volume_gb,
                "Volume (MB)": volume_mb,
                "Archiving Object": "",
                "Object Description": "(no archiving object found)",
                "Housekeeping Program": hk_program,
                "Score": "",
            }
            rationale = hk.get("rationale") or (
                "No suitable archiving object or housekeeping program found."
            )
            scored_rows.append(row)
            recommended_rows.append({**row, "Rationale": rationale})
            continue

        best = None
        best_score = -1
        for cand in entry["candidates"]:
            score_info = candidate_scores.get(cand["object"], {})
            raw_score = score_info.get("score", "")
            # Keep Score as a string everywhere it crosses the API/JSON
            # boundary, matching the rest of the app's row shape
            # (Record<string,string> on the frontend); only the Excel
            # export helper converts it back to a real number.
            score_str = str(raw_score) if isinstance(raw_score, int) else ""
            scored_rows.append({
                "Table Name": table_name,
                "Table Description": description,
                "Volume (GB)": volume_gb,
                "Volume (MB)": volume_mb,
                "Archiving Object": cand["object"],
                "Object Description": cand["description"],
                "Housekeeping Program": "",
                "Score": score_str,
            })
            numeric_score = raw_score if isinstance(raw_score, int) else -1
            if numeric_score > best_score:
                best_score = numeric_score
                best = {
                    "Table Name": table_name,
                    "Table Description": description,
                    "Volume (GB)": volume_gb,
                    "Volume (MB)": volume_mb,
                    "Archiving Object": cand["object"],
                    "Object Description": cand["description"],
                    "Housekeeping Program": "",
                    "Score": score_str,
                    "Rationale": score_info.get("rationale", ""),
                }
        if best is not None:
            recommended_rows.append(best)
        else:
            # No candidate could be scored: say so instead of recommending the first one by accident
            recommended_rows.append({
                "Table Name": table_name,
                "Table Description": description,
                "Volume (GB)": volume_gb,
                "Volume (MB)": volume_mb,
                "Archiving Object": "",
                "Object Description": "(not scored)",
                "Housekeeping Program": "",
                "Score": "",
                "Rationale": unscored.get(table_name) or "None of the candidates could be scored.",
            })

    tally = Tally()
    for table_name, reason in unscored.items():
        tally.miss(table_name, reason)
    cut = [t for t, e in tables.items() if e.get("excerpt_cut")]
    if cut:
        shown = ", ".join(cut[:5]) + (", ..." if len(cut) > 5 else "")
        tally.note(f"The DVM Guide excerpt was shortened to fit the model's input limit for {len(cut)} table(s): {shown}.")
    result = {
        "status": "ok",
        "rows": scored_rows,
        "recommended": recommended_rows,
        "unscored": [{"table": t, "reason": r} for t, r in unscored.items()],
        "message": "",
        "completeness": tally.summary(),
    }
    if unscored:
        first_reason = next(iter(unscored.values()))
        result["message"] = (
            f"{len(unscored)} of {len(needed)} table{'s' if len(needed) != 1 else ''} that needed the AI model "
            f"could not be scored ({first_reason}). The other tables are scored."
        )
    return result


def merge_results(previous: dict, new: dict) -> dict:
    """Put a re-run of some tables (*new*, from score_archiving_objects on just those tables) back into the
    earlier result (*previous*): the re-run tables take their rows from *new*, every other table keeps its
    rows, and the tables stay in the order they had. "unscored" and "message" come from *new*."""
    order: list[str] = []
    for row in previous.get("rows", []):
        if row.get("Table Name") not in order:
            order.append(row.get("Table Name"))
    redone = {row.get("Table Name") for row in new.get("rows", [])}

    def combine(key: str) -> list[dict]:
        out: list[dict] = []
        for table in order:
            source = new if table in redone else previous
            out.extend(r for r in source.get(key, []) if r.get("Table Name") == table)
        return out

    tally = Tally()
    for n in [*(previous.get("completeness") or {}).get("notes", []), *(new.get("completeness") or {}).get("notes", [])]:
        tally.note(n)
    for u in new.get("unscored", []):
        tally.miss(u["table"], u["reason"])
    return {
        **previous,
        "status": "ok",
        "rows": combine("rows"),
        "recommended": combine("recommended"),
        "unscored": new.get("unscored", []),
        "message": new.get("message", ""),
        "completeness": tally.summary(),
    }


def _score_all_tables(
    settings: "llm.Settings", tables: "OrderedDict[str, dict]", on_progress=None, note=None
) -> tuple[dict, dict, str]:
    """Returns (scores, unscored, stopped): scores is {table: {archiving_object: {"score": int | "", "rationale": str}}},
    unscored is {table: reason} for tables that could not be scored, stopped is the reason a limit ended the run ("" if none).
    Reports progress once per distinct table in *tables* (zero-candidate tables included), so the
    completed count always reaches len(tables).

    Raises llm.LLMError only if the model can't be used at all (bad/missing key): a rate or daily limit
    keeps the tables already scored and lists the rest as unscored."""
    results: dict[str, dict] = {}
    unscored: dict[str, str] = {}
    stopped = ""
    completed = 0

    def _report(table_name: str):
        nonlocal completed
        completed += 1
        if on_progress:
            on_progress(completed, table_name)

    def step(message: str) -> None:
        if note:
            note(message)

    def blank(table_name: str) -> dict:
        return {cand["object"]: {"score": "", "rationale": ""} for cand in tables[table_name]["candidates"]}

    zero_candidate_tables = []
    single_candidate_tables = []
    multi_candidate_tables = []
    for table_name, entry in tables.items():
        n = len(entry["candidates"])
        if n == 0:
            zero_candidate_tables.append(table_name)
        elif n == 1:
            single_candidate_tables.append(table_name)
        else:
            multi_candidate_tables.append(table_name)

    for table_name in zero_candidate_tables:
        _report(table_name)

    for table_name in single_candidate_tables:
        obj = tables[table_name]["candidates"][0]["object"]
        results[table_name] = {
            obj: {"score": 100, "rationale": "Only archiving object found for this table."}
        }
        _report(table_name)

    if not multi_candidate_tables:
        return results, unscored, stopped

    with ThreadPoolExecutor(max_workers=llm.max_workers(settings)) as pool:
        future_to_table = {
            pool.submit(_score_one_table, settings, table_name, tables[table_name], step): table_name
            for table_name in multi_candidate_tables
        }
        for future in as_completed(future_to_table):
            table_name = future_to_table[future]
            try:
                results[table_name] = future.result()
            except CancelledError:
                results[table_name] = blank(table_name)
                unscored[table_name] = stopped or "Scoring was stopped before this table."
            except (llm.LLMAuthError, llm.LLMNotConfiguredError):
                for pending in future_to_table:
                    pending.cancel()
                raise
            except llm.LLMRateLimitError as exc:
                # A limit ends the run, but the tables that are done keep their scores
                if not stopped:
                    stopped = str(exc)
                    for pending in future_to_table:
                        pending.cancel()
                results[table_name] = blank(table_name)
                unscored[table_name] = str(exc)
            except Exception as exc:
                logger.warning("Scoring failed for table %s: %s", table_name, exc, exc_info=True)
                results[table_name] = blank(table_name)
                unscored[table_name] = f"Scoring failed: {exc}"
            _report(table_name)

    return results, unscored, stopped


def _chunk_size(settings: "llm.Settings") -> int:
    """How many candidates one scoring request holds: the model's setting, but never more than its answer can carry."""
    fits = (llm.max_output_tokens(settings) - ANSWER_OVERHEAD) // TOKENS_PER_CANDIDATE
    return max(5, min(llm.budget(settings)["score_chunk"], fits))


def _score_one_table(settings: "llm.Settings", table_name: str, entry: dict, step=None) -> dict:
    """Returns {archiving_object: {"score": int | "", "rationale": str}} for the table's candidates; the
    rationale is only on the best one."""
    candidates = entry["candidates"]
    note = step or (lambda message: None)

    reference = dvm_guide.get_reference(table_name)
    reference_block = ""
    if reference:
        limit = llm.budget(settings)["dvm_excerpt_chars"]
        if len(reference) > limit:
            entry["excerpt_cut"] = (limit, len(reference))     # reported with the result (see _score)
            reference = reference[:limit]
        reference_block = (
            "Reference excerpt from SAP's official Data Management Guide for "
            "SAP Business Suite, covering this specific table:\n"
            f"---\n{reference}\n---\n\n"
        )
    header = f"{reference_block}Table: {table_name} ({entry['description']})\n\n"

    size = _chunk_size(settings)
    if len(candidates) <= size:
        answer = _score_set(settings, header, candidates, final=True)
        if not answer["scores"]:
            raise llm.LLMError("The model returned no scores for this table.")
        return _finish(candidates, answer["scores"], answer["top"], answer["rationale"])

    # Phase A: the candidates part by part, scores only
    parts = [candidates[i:i + size] for i in range(0, len(candidates), size)]
    scores: dict[str, int] = {}
    last_error: Optional[Exception] = None
    for n, part in enumerate(parts, start=1):
        note(f"{table_name}: scoring part {n} of {len(parts)}")
        try:
            scores.update(_score_set(settings, header, part, final=False)["scores"])
        except _FATAL:
            raise
        except Exception as exc:
            logger.warning("Scoring part %d of %s failed: %s", n, table_name, exc)
            last_error = exc
    if not scores:
        raise last_error or llm.LLMError("The model returned no scores for this table.")

    # Phase B: the best ones, and those the guide names, compared with each other
    named = [c for c in candidates if c["object"] in (reference or "") and c["object"] in scores]
    ranked = sorted((c for c in candidates if c["object"] in scores), key=lambda c: -scores[c["object"]])
    finalists: list[dict] = named[:PLAYOFF_NAMED]
    for c in ranked:
        if len(finalists) >= PLAYOFF_SIZE:
            break
        if c not in finalists:
            finalists.append(c)
    note(f"{table_name}: comparing the {len(finalists)} best candidates")
    top, rationale = "", ""
    try:
        answer = _score_set(settings, header, finalists, final=True)
        if answer["scores"]:
            floor = min(answer["scores"].values())
            names = {c["object"] for c in finalists}
            for obj in list(scores):
                if obj not in names:                      # keep the ranking: nothing outside the playoff beats a finalist
                    scores[obj] = min(scores[obj], max(0, floor - 1))
            scores.update(answer["scores"])
            top, rationale = answer["top"], answer["rationale"]
    except _FATAL:
        raise
    except Exception as exc:
        logger.warning("Final comparison for %s failed, using the part scores: %s", table_name, exc)
    return _finish(candidates, scores, top, rationale)


def _score_set(settings: "llm.Settings", header: str, cands: list[dict], final: bool, retry_missing: bool = True) -> dict:
    """One scoring request for *cands* (split in two if the answer comes back cut off or malformed, and the
    candidates it left out are asked for once more). Returns {"scores": {object: int}, "top": str, "rationale": str}
    (top and rationale only when the request was not split)."""
    lines = "\n".join(f"- {c['object']}: {c['description']}" for c in cands)
    user = f"{header}Candidate archiving objects:\n{lines}\n\nScore every candidate listed above."
    max_tokens = max(300, min(llm.max_output_tokens(settings), TOKENS_PER_CANDIDATE * len(cands) + (ANSWER_OVERHEAD if final else 60)))
    schema = ScoringResponse if final else ScoreList
    try:
        parsed = llm.structured(SYSTEM_PROMPT if final else CHUNK_PROMPT, user, schema, max_tokens=max_tokens, settings=settings)
    except _FATAL:
        raise
    except Exception as exc:
        if len(cands) <= 4:
            raise
        logger.info("Splitting a scoring request of %d candidates after: %s", len(cands), exc)
        half = len(cands) // 2
        left = _score_set(settings, header, cands[:half], final=False)
        right = _score_set(settings, header, cands[half:], final=False)
        return {"scores": {**left["scores"], **right["scores"]}, "top": "", "rationale": ""}

    wanted = {c["object"].strip().upper(): c["object"] for c in cands}
    scores: dict[str, int] = {}
    for item in parsed.candidates:
        key = item.archiving_object.strip().upper()
        if key in wanted and wanted[key] not in scores:
            scores[wanted[key]] = item.score
    answer = {
        "scores": scores,
        "top": getattr(parsed, "top_choice", "") or "",
        "rationale": (getattr(parsed, "top_rationale", "") or "").strip(),
    }
    missing = [c for c in cands if c["object"] not in scores]
    if missing and scores and retry_missing:
        more = _score_set(settings, header, missing, final=False, retry_missing=False)
        scores.update(more["scores"])
    return answer


def _finish(candidates: list[dict], scores: dict, top: str, rationale: str) -> dict:
    """The per-candidate result: its score (blank if it was not scored) and the rationale on the best one."""
    out = {c["object"]: {"score": scores.get(c["object"], ""), "rationale": ""} for c in candidates}
    best = None
    for c in candidates:                     # first of the highest scores, as the recommended list picks it
        if isinstance(out[c["object"]]["score"], int) and (best is None or out[c["object"]]["score"] > out[best]["score"]):
            best = c["object"]
    if best is not None:
        if rationale and re.sub(r"\s+", "", top).upper() == re.sub(r"\s+", "", best).upper():
            out[best]["rationale"] = rationale
        else:
            out[best]["rationale"] = f"Highest score ({out[best]['score']}) among the candidates."
    return out
