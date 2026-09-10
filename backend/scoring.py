"""
Score archiving objects for relevance per table, using the Claude API, and
derive a recommended (highest-scoring) object per table.

Takes the output of db15.run_batch() -- rows shaped
{"Table Name", "Table Description", "Archiving Object", "Object Description"},
one row per (table, candidate archiving object) pair -- and adds a Score
(0-100) to every row, plus a separate "recommended" list with one row per
table (the max-score candidate) and a Rationale column.

One Claude API call per table (not per object): all of a table's candidates
are scored together in a single structured response, which is both cheaper
and lets the model reason comparatively across that table's own candidates.
Tables with a single candidate skip the API call entirely (nothing to
compare against). Tables with zero candidates pass through unscored.
"""

import logging
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed

import anthropic
from pydantic import BaseModel, Field

import dvm_guide
from config import ANTHROPIC_API_KEY, SCORING_MODEL

logger = logging.getLogger(__name__)

MAX_WORKERS = 8

SYSTEM_PROMPT = (
    "You are an SAP data archiving expert helping decide which SAP archiving "
    "object is the most appropriate one for archiving a given database table. "
    "For each candidate archiving object listed, assign a relevance score from "
    "0 (not relevant) to 100 (highly relevant -- very likely the correct "
    "primary archiving object for this table), based on typical SAP archiving "
    "practice and how closely the object's purpose matches the table. Give a "
    "rationale for each -- ONE short sentence, ideally under 20 words. Only "
    "mention a specific SAP Note, KBA, or documentation reference in the "
    "rationale if you are genuinely aware of one -- do not invent one. "
    "Approximate scores are fine; exact precision is not required. A table "
    "may have many candidates -- keep every rationale terse so the full "
    "response fits comfortably.\n\n"
    "You may also be given a reference excerpt from SAP's official Data "
    "Management Guide for SAP Business Suite, specific to the table being "
    "scored. Treat it as the authoritative source when present: if it names "
    "a specific recommended archiving object for this table, that candidate "
    "should score much higher than unrelated ones, and its rationale should "
    "say the recommendation comes from the guide. If no excerpt is given, "
    "or it doesn't clearly resolve the choice, fall back on general SAP "
    "archiving knowledge."
)


class ScoredCandidate(BaseModel):
    archiving_object: str
    score: int = Field(ge=0, le=100)
    rationale: str


class ScoringResponse(BaseModel):
    candidates: list[ScoredCandidate]


def score_archiving_objects(rows: list[dict], on_progress=None) -> dict:
    """
    Score every (table, archiving object) row for relevance, and derive the
    recommended (highest-scoring) object per table.

    *on_progress*, if given, is called as on_progress(completed_count,
    table_name) once per distinct table as its scoring resolves (including
    the zero/single-candidate shortcuts), so a caller can report progress on
    a long-running batch.

    Returns {"status": "ok", "rows": [...with Score...], "recommended": [...]}
    or {"status": "error", "message": ...}.
    """
    if not ANTHROPIC_API_KEY:
        return {
            "status": "error",
            "message": "ANTHROPIC_API_KEY is not set. Add it to backend/.env and restart the backend.",
        }

    tables: "OrderedDict[str, dict]" = OrderedDict()
    for row in rows:
        table_name = row.get("Table Name", "")
        entry = tables.setdefault(
            table_name,
            {"description": row.get("Table Description", ""), "candidates": []},
        )
        obj = row.get("Archiving Object", "")
        if obj:
            entry["candidates"].append(
                {"object": obj, "description": row.get("Object Description", "")}
            )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    try:
        scores_by_table = _score_all_tables(client, tables, on_progress)
    except (
        anthropic.AuthenticationError,
        anthropic.RateLimitError,
        anthropic.APIStatusError,
        anthropic.APIConnectionError,
    ) as exc:
        logger.exception("Scoring failed")
        return {"status": "error", "message": str(exc)}

    scored_rows = []
    recommended_rows = []
    for table_name, entry in tables.items():
        description = entry["description"]
        candidate_scores = scores_by_table.get(table_name, {})

        if not entry["candidates"]:
            scored_rows.append({
                "Table Name": table_name,
                "Table Description": description,
                "Archiving Object": "",
                "Object Description": "(no archiving objects found)",
                "Score": "",
            })
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
                "Archiving Object": cand["object"],
                "Object Description": cand["description"],
                "Score": score_str,
            })
            numeric_score = raw_score if isinstance(raw_score, int) else -1
            if best is None or numeric_score > best_score:
                best_score = numeric_score
                best = {
                    "Table Name": table_name,
                    "Table Description": description,
                    "Archiving Object": cand["object"],
                    "Object Description": cand["description"],
                    "Score": score_str,
                    "Rationale": score_info.get("rationale", ""),
                }
        if best is not None:
            recommended_rows.append(best)

    return {"status": "ok", "rows": scored_rows, "recommended": recommended_rows}


def _score_all_tables(
    client: anthropic.Anthropic, tables: "OrderedDict[str, dict]", on_progress=None
) -> dict:
    """Returns {table_name: {archiving_object: {"score": int, "rationale": str}}}.
    Reports progress once per distinct table in *tables* (zero-candidate
    tables included), so completed count always reaches len(tables)."""
    results: dict[str, dict] = {}
    completed = 0

    def _report(table_name: str):
        nonlocal completed
        completed += 1
        if on_progress:
            on_progress(completed, table_name)

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
        return results

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_table = {
            pool.submit(_score_one_table, client, table_name, tables[table_name]): table_name
            for table_name in multi_candidate_tables
        }
        for future in as_completed(future_to_table):
            table_name = future_to_table[future]
            try:
                results[table_name] = future.result()
            except Exception as exc:
                logger.warning("Scoring failed for table %s: %s", table_name, exc, exc_info=True)
                results[table_name] = {
                    cand["object"]: {"score": "", "rationale": f"Scoring failed: {exc}"}
                    for cand in tables[table_name]["candidates"]
                }
            _report(table_name)

    return results


def _score_one_table(client: anthropic.Anthropic, table_name: str, entry: dict) -> dict:
    candidate_lines = "\n".join(
        f"- {cand['object']}: {cand['description']}" for cand in entry["candidates"]
    )

    reference = dvm_guide.get_reference(table_name)
    reference_block = ""
    if reference:
        reference_block = (
            "Reference excerpt from SAP's official Data Management Guide for "
            "SAP Business Suite, covering this specific table:\n"
            f"---\n{reference}\n---\n\n"
        )

    user_content = (
        f"{reference_block}"
        f"Table: {table_name} ({entry['description']})\n\n"
        f"Candidate archiving objects:\n{candidate_lines}\n\n"
        "Score every candidate listed above."
    )

    # Every candidate needs its own score + rationale in the same JSON
    # response, so the token budget must grow with the candidate count --
    # a flat/too-small budget truncates (and thus fails to parse) the
    # response for tables with many candidates (e.g. CDHDR). Haiku 4.5's
    # ceiling is 64000; cap at 16000, the point past which the SDK docs
    # recommend switching to streaming to avoid HTTP timeouts -- comfortably
    # covers even a ~60-candidate table at this prompt's terseness.
    max_tokens = min(16000, max(2048, 250 * len(entry["candidates"]) + 500))

    response = client.messages.parse(
        model=SCORING_MODEL,
        max_tokens=max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
        output_format=ScoringResponse,
    )

    parsed: ScoringResponse = response.parsed_output
    return {
        c.archiving_object: {"score": c.score, "rationale": c.rationale}
        for c in parsed.candidates
    }
