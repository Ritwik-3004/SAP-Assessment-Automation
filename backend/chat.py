"""
The always-available assistant (right-hand chat column of the app).

It answers from, in order of authority:
  1. the DVM Guide (SAP's Data Management Guide)        -- search_dvm_guide
  2. SAP for Me (Notes / Knowledge Base Articles)       -- search_sap_for_me
  3. the live SAP system, through six transactions      -- DB15, AOBJ, SE16N, SE11, TAANA, SARA
  4. the current scored results, if any are loaded      -- and it can change them (update_table_result)
  5. the model's own SAP knowledge, which it must flag as such.

Modified rows are returned alongside the text reply so the frontend can update its state
without re-running scoring.
"""
import logging
from copy import deepcopy
from typing import Any

import llm
from sap_connector import sap
import dvm_guide
import sap_for_me
import transactions.db02 as db02
import transactions.db15 as db15
import transactions.aobj as aobj
import transactions.se16n as se16n
import transactions.se11 as se11
import transactions.taana as taana
import transactions.sara as sara

logger = logging.getLogger(__name__)

_TOOLS = [
    {
        "name": "lookup_archiving_objects",
        "description": (
            "[DB15] Use when the user asks which archiving objects cover a specific "
            "DATABASE TABLE, e.g. 'what objects archive BKPF?', 'check BALDAT'. "
            "Input is a table name. Do NOT use to validate an archiving object name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "SAP table name, e.g. BKPF"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "check_archiving_object",
        "description": (
            "[AOBJ] Use when the user asks about an ARCHIVING OBJECT NAME — whether "
            "it exists, what it does, or wants to list objects by module prefix. "
            "Examples: 'does BC_SBAL exist?', 'what is FI_DOCUMNT?', 'list all MM_ "
            "objects'. Accepts exact names or wildcard patterns (FI_*, MM_*)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "object_filter": {
                    "type": "string",
                    "description": "Object name or pattern, e.g. FI_DOCUMNT, BC_SBAL, MM_*",
                }
            },
            "required": ["object_filter"],
        },
    },
    {
        "name": "browse_table_contents",
        "description": (
            "[SE16N] Use when the user wants to SEE ACTUAL DATA ROWS inside a table, "
            "e.g. 'show me rows from T001', 'how many entries does BKPF have for 2023?', "
            "'what does a MKPF record look like?'. Accepts an optional WHERE clause "
            "and a row limit (default 50, max 200)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "SAP table name"},
                "max_rows": {"type": "integer", "description": "Max rows to return (default 50)"},
                "where_clause": {
                    "type": "string",
                    "description": "Optional WHERE clause, e.g. \"GJAHR = '2023'\"",
                },
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_table_definition",
        "description": (
            "[SE11] Use when the user asks about a TABLE'S STRUCTURE — what fields it "
            "has, their data types, key fields, or what kind of data it stores. "
            "Examples: 'what fields does BKPF have?', 'is MANDT a key field in T001?', "
            "'describe the structure of VBAK'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "SAP table name"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "analyze_table",
        "description": (
            "[TAANA] Use when the user asks about TABLE STATISTICS — row count, size, "
            "last archiving run, or archivability analysis. Examples: 'how many rows "
            "does BKPF have?', 'what is the size of BSEG?', 'has VBAP ever been "
            "archived?', 'analyze KNA1'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {"type": "string", "description": "SAP table name or pattern"},
                "max_rows": {"type": "integer", "description": "Max result rows (default 100)"},
            },
            "required": ["table_name"],
        },
    },
    {
        "name": "get_archiving_sessions",
        "description": (
            "[SARA] Use when the user asks about ARCHIVING HISTORY or SESSIONS for a "
            "specific archiving object — whether data has actually been archived, when "
            "the last run was, how many records, file sizes. Examples: 'has FI_DOCUMNT "
            "been run before?', 'show archiving sessions for MM_MATBEL', 'when was the "
            "last archive run for SD_VBAK?'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "archiving_object": {
                    "type": "string",
                    "description": "Archiving object name, e.g. FI_DOCUMNT",
                }
            },
            "required": ["archiving_object"],
        },
    },
    {
        "name": "get_largest_tables",
        "description": (
            "[DB02] Use when the user asks which tables are the BIGGEST in the system, wants a "
            "top-N list by size, or asks about the system's size in general: 'what is the largest "
            "table?', 'top 10 tables', 'biggest tables'. Returns table name, description and "
            "size in GB/MB, largest first (HANA in-memory size of column-store tables, via DB02's "
            "SQL editor)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "How many tables to return (default 10, max 50)"},
            },
        },
    },
    {
        "name": "get_table_sizes",
        "description": (
            "[DB02] Use when the user asks how BIG one or more NAMED tables are (size in GB/MB), "
            "or wants to compare tables: 'how big is BSEG?', 'size of BKPF and VBAK'. Prefer this "
            "over analyze_table for plain size questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "SAP table names, e.g. [\"BSEG\", \"BKPF\"] (at most 30)",
                },
            },
            "required": ["table_names"],
        },
    },
    {
        "name": "search_dvm_guide",
        "description": (
            "[DVM Guide] Search SAP's official Data Management Guide (the most authoritative "
            "source in this app). Use for: which archiving object or housekeeping/deletion "
            "program applies to a table, what a table stores, how it grows, avoidance / "
            "summarization / deletion options. Input is a table name (e.g. 'BKPF') or keywords "
            "(e.g. 'application log deletion'). Fast and offline - try this before SAP for Me."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Table name or keywords"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "search_sap_for_me",
        "description": (
            "[SAP for Me] Search SAP for Me for SAP Notes and Knowledge Base Articles (falls back "
            "to SAP Community posts). SLOW (opens a browser and signs in; can take a minute or "
            "more) - use only when the DVM Guide and the live system do not answer, e.g. a "
            "housekeeping program, a known issue, a recommended report. Needs saved SAP for Me "
            "credentials."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search text, e.g. 'COSP housekeeping program'"},
                "keyword": {
                    "type": "string",
                    "description": "Most specific term (a table/object/program name) to focus the article text on",
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "update_table_result",
        "description": (
            "Modify the archiving object, score, or rationale for a specific table "
            "in the scored results. Call this when the user explicitly asks to change, "
            "update, replace, or set a result. The preview will refresh automatically "
            "after the change. You may call lookup_archiving_objects first to confirm "
            "the object name before applying the change."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "SAP table name to modify (uppercase)",
                },
                "archiving_object": {
                    "type": "string",
                    "description": "New archiving object name to set as the recommended choice",
                },
                "score": {
                    "type": "number",
                    "description": "New score for this object (0-100). Defaults to 100 if not given.",
                },
                "rationale": {
                    "type": "string",
                    "description": "New rationale text explaining why this object was chosen",
                },
            },
            "required": ["table_name"],
        },
    },
]

_SYSTEM = """\
You are the SAP archiving assistant built into the SAP Assessment Automation tool. You help \
people doing an SAP archivability assessment: which tables are big, which archiving objects or \
housekeeping programs apply, how SAP transactions work, and what is in their SAP system.

KNOWLEDGE SOURCES - in order of authority:
  1. DVM Guide (search_dvm_guide): SAP's official Data Management Guide. Authoritative for which \
archiving object / cleanup program fits a table.
  2. SAP for Me (search_sap_for_me): SAP Notes and Knowledge Base Articles. Slow - use it when the \
DVM Guide does not cover the question.
  3. The live SAP system, through the tools below: facts about THIS system (what exists, row \
counts, fields, archiving history).
  4. The scored results of the current session, if loaded (see the end of this prompt).
  5. Your own SAP knowledge - last. Use it to explain concepts and transactions, but say it is \
general knowledge and not from the guide, SAP for Me or this system.
When sources disagree, follow the order above and point out the disagreement.

LIVE TRANSACTIONS you can run (each needs SAP to be connected):
  DB15 (lookup_archiving_objects)   archiving objects that cover a table
  DB02 (get_largest_tables, get_table_sizes)  biggest tables in the system; size of named tables
  AOBJ (check_archiving_object)     archiving object definitions: does X exist, list MM_*
  SE16N (browse_table_contents)     rows in any table, optional WHERE
  SE11 (get_table_definition)       any table's fields, types, keys
  TAANA (analyze_table)             row counts, size, archiving statistics
  SARA (get_archiving_sessions)     archive runs / sessions of an object
Other transactions you can EXPLAIN but cannot run here (say so if asked to run them): SE38/SA38 (run an ABAP report, e.g. a cleanup program), \
SM36/SM37 (schedule / monitor background jobs), SARI (archive information system), SM30/SM31 \
(table maintenance), SE37 (function modules), ST03N / ST22 (workload / dumps).

TOOL SELECTION:
  - "which archiving object / cleanup program for table X" -> search_dvm_guide first, then \
lookup_archiving_objects (DB15) for what this system really has
  - "does object Y exist / list MM_ objects" -> check_archiving_object
  - "show rows" -> browse_table_contents; "fields / structure" -> get_table_definition
  - "largest / biggest tables, top N" -> get_largest_tables; "how big is table X" -> get_table_sizes
  - "how many rows / archived?" -> analyze_table; "archiving history" -> get_archiving_sessions
  - concept or transaction questions -> answer directly (source 5), or check the DVM Guide
  - change a result -> update_table_result (only when results are loaded)

RULES:
  - Always say where an answer came from: "DVM Guide: ...", "SAP for Me (Note 12345): ...", \
"Checked in AOBJ: ...", "DB15 shows ...", "From my general SAP knowledge: ...". Never present \
general knowledge as if it came from the system or SAP's documents.
  - Only attribute something to the DVM Guide or SAP for Me if a tool call in THIS reply returned it. \
Do not describe menu paths, report names or parameters of a transaction unless you are sure of them; if \
unsure, say so. If a live tool exists for the question, use it instead of telling the user to do it \
themselves in SAP.
  - Never invent a table, archiving object, program or note number. Verify names with a tool \
before relying on them; if a tool fails or finds nothing, say that plainly instead of guessing.
  - If SAP is not connected, still use the DVM Guide, SAP for Me and your knowledge, and say that \
live system checks are unavailable until the user connects.
  - Keep answers concise; short bulleted lists for data. Confirm what changed after a modification.
  - Call at most 5 tools per reply. Start with the cheapest source that can answer.

{results_section}
"""

_RESULTS_LOADED = """\
=== Scored results ({table_count} tables) - the user's current results, which you may discuss and \
change with update_table_result ===
{context}"""

_RESULTS_EMPTY = """\
No scored results are loaded (the user has not run Score & Recommend yet), so there is nothing to \
modify; answer general and live-system questions."""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(scored_rows: list[dict], max_tables: int = 60) -> tuple[str, int]:
    by_table: dict[str, list[dict]] = {}
    for row in scored_rows:
        tbl = (row.get("Table Name") or "").strip().upper()
        if tbl:
            by_table.setdefault(tbl, []).append(row)

    lines = []
    for tbl, rows in list(by_table.items())[:max_tables]:
        try:
            best = max(rows, key=lambda r: float(r.get("Score") or 0))
        except (TypeError, ValueError):
            best = rows[0]
        obj = (best.get("Archiving Object") or "").strip()
        score = (best.get("Score") or "").strip()
        rationale = (best.get("Rationale") or "").strip()[:80]
        others = [
            r.get("Archiving Object", "").strip()
            for r in rows
            if r is not best and r.get("Archiving Object")
        ]
        line = f"{tbl}: best={obj or '(none)'} score={score}"
        if others:
            line += f" others=[{', '.join(others[:3])}]"
        if rationale:
            line += f" note={rationale!r}"
        lines.append(line)

    if len(by_table) > max_tables:
        lines.append(f"... and {len(by_table) - max_tables} more tables (ask about specific ones)")

    return ("\n".join(lines) if lines else "(no scored results)"), len(by_table)


# ---------------------------------------------------------------------------
# Recommended list derivation
# ---------------------------------------------------------------------------

def _recompute_recommended(rows: list[dict]) -> list[dict]:
    """Pick the highest-scoring row per table (mirrors the scoring module logic)."""
    by_table: dict[str, list[dict]] = {}
    for row in rows:
        tbl = (row.get("Table Name") or "").strip().upper()
        if tbl:
            by_table.setdefault(tbl, []).append(row)

    recommended = []
    for tbl_rows in by_table.values():
        try:
            best = max(tbl_rows, key=lambda r: float(r.get("Score") or 0))
        except (TypeError, ValueError):
            best = tbl_rows[0]
        recommended.append(best)
    return recommended


# ---------------------------------------------------------------------------
# Tool execution
# ---------------------------------------------------------------------------

def _exec_lookup(table: str) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = db15.run(table)
        if result.get("status") == "error":
            return f"DB15 error for {table}: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return f"No archiving objects found for {table} in this SAP system."
        lines = [f"Archiving objects for {table} ({len(rows)} found):"]
        for r in rows:
            obj = (r.get("Archiving Object") or "").strip()
            desc = (r.get("Object Description") or "").strip()
            if obj:
                lines.append("  • " + obj + (f" — {desc}" if desc else ""))
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: DB15 failed for %s", table)
        return f"Error running DB15 for {table}: {exc}"


def _exec_check_archiving_object(object_filter: str) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = aobj.run(object_filter.strip() or None)
        if result.get("status") == "error":
            return f"AOBJ error: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return (
                f"No archiving objects found matching '{object_filter}' in this SAP system. "
                "It likely does not exist or is not configured here."
            )
        # Find the column that holds the object name (first column is usually it)
        lines = [f"Found {len(rows)} archiving object(s) matching '{object_filter}':"]
        for r in rows:
            # Try common column names for the object identifier and description
            name = (
                r.get("Archiving Object") or r.get("ArchObj") or r.get("Object")
                or next(iter(r.values()), "")
            ).strip()
            desc = (
                r.get("Description") or r.get("Desc") or r.get("Text")
                or list(r.values())[1] if len(r) > 1 else ""
            ).strip()
            if name:
                lines.append("  • " + name + (f" — {desc}" if desc else ""))
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: AOBJ failed for filter %s", object_filter)
        return f"Error checking archiving object '{object_filter}': {exc}"


def _exec_browse_table(table_name: str, max_rows: int, where_clause: str | None) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = se16n.run(table_name.upper(), max_rows=max_rows, where_clause=where_clause or None)
        if result.get("status") == "error":
            return f"SE16N error for {table_name}: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return f"No rows returned for {table_name} (table may be empty or the filter returned nothing)."
        # Summarise: show column headers + up to 10 rows as compact text
        cols = list(rows[0].keys())
        header = " | ".join(cols[:8])  # cap columns shown
        lines = [f"{table_name}: {len(rows)} row(s) returned", header, "-" * len(header)]
        for r in rows[:10]:
            lines.append(" | ".join(str(r.get(c, "")) for c in cols[:8]))
        if len(rows) > 10:
            lines.append(f"... {len(rows) - 10} more rows not shown")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: SE16N failed for %s", table_name)
        return f"Error browsing {table_name}: {exc}"


def _exec_get_table_definition(table_name: str) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = se11.run(table_name.upper())
        if result.get("status") == "error":
            return f"SE11 error for {table_name}: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return f"No field definitions returned for {table_name}."
        lines = [f"{table_name} — {len(rows)} field(s):"]
        for r in rows[:30]:
            field = r.get("Field", r.get("field", "")).strip()
            dtype = r.get("Data Type", r.get("data_type", r.get("DType", ""))).strip()
            length = r.get("Length", r.get("length", "")).strip()
            desc = r.get("Description", r.get("Short Description", "")).strip()
            key = r.get("Key", "").strip()
            parts = [field]
            if key:
                parts.append("KEY")
            if dtype:
                parts.append(dtype + (f"({length})" if length else ""))
            if desc:
                parts.append(f"— {desc}")
            lines.append("  • " + " ".join(parts))
        if len(rows) > 30:
            lines.append(f"  ... {len(rows) - 30} more fields")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: SE11 failed for %s", table_name)
        return f"Error getting definition for {table_name}: {exc}"


def _exec_analyze_table(table_name: str, max_rows: int) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = taana.run(table_name=table_name.upper(), max_rows=max_rows)
        if result.get("status") == "error":
            return f"TAANA error for {table_name}: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return f"No TAANA data for {table_name} — it may not be included in the analysis."
        lines = [f"TAANA analysis for {table_name}:"]
        for r in rows[:5]:
            for k, v in r.items():
                if v and str(v).strip():
                    lines.append(f"  {k}: {v}")
            lines.append("")
        return "\n".join(lines).strip()
    except Exception as exc:
        logger.exception("Chat tool: TAANA failed for %s", table_name)
        return f"Error analyzing {table_name}: {exc}"


def _exec_get_archiving_sessions(archiving_object: str) -> str:
    if not sap.is_connected:
        return "SAP is not connected — reconnect via the login panel first."
    try:
        result = sara.run(archiving_object.upper())
        if result.get("status") == "error":
            return f"SARA error for {archiving_object}: {result.get('message', 'unknown error')}"
        sessions = result.get("sessions", [])
        if not sessions:
            return (
                f"No archiving sessions found for {archiving_object}. "
                "The object may not have been run yet, or it is not configured on this system."
            )
        lines = [f"Archiving sessions for {archiving_object} ({len(sessions)} found):"]
        for s in sessions[:10]:
            parts = []
            for k in ("Session", "Status", "Start Date", "Records", "File Size"):
                v = s.get(k, "").strip()
                if v:
                    parts.append(f"{k}: {v}")
            lines.append("  • " + " | ".join(parts) if parts else "  • " + str(s))
        if len(sessions) > 10:
            lines.append(f"  ... {len(sessions) - 10} more sessions")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: SARA failed for %s", archiving_object)
        return f"Error fetching sessions for {archiving_object}: {exc}"


def _format_sizes(rows: list[dict], limit: int | None = None) -> list[str]:
    lines = []
    for r in rows[:limit] if limit else rows:
        name = (r.get("Table Name") or "").strip()
        desc = (r.get("Description") or "").strip()
        gb, mb = (r.get("Volume (GB)") or "").strip(), (r.get("Volume (MB)") or "").strip()
        size = f"{gb} GB ({mb} MB)" if gb and mb else (f"{mb} MB" if mb else gb)
        lines.append("  " + f"{len(lines) + 1}. {name} - {size}" + (f" - {desc}" if desc else ""))
    return lines


def _exec_largest_tables(limit: int) -> str:
    if not sap.is_connected:
        return "SAP is not connected - connect via the login panel first."
    limit = max(1, min(int(limit or 10), 50))
    try:
        result = db02.run_get_top_tables(limit)
        if result.get("status") == "error":
            return f"DB02 error: {result.get('message', 'unknown error')}"
        rows = result.get("rows", [])
        if not rows:
            return "DB02 returned no tables."
        return (
            f"Largest {len(rows)} tables by size (DB02 / HANA M_CS_TABLES, in-memory size of "
            f"column-store tables; row-store tables are not included):\n" + "\n".join(_format_sizes(rows))
        )
    except Exception as exc:
        logger.exception("Chat tool: DB02 top tables failed")
        return f"Error running DB02: {exc}"


def _exec_table_sizes(table_names: list[str]) -> str:
    if not sap.is_connected:
        return "SAP is not connected - connect via the login panel first."
    names = [str(n).strip().upper() for n in (table_names or []) if str(n).strip()][:30]
    if not names:
        return "No table names given."
    try:
        result = db02.run_get_table_sizes(names)
        if result.get("status") == "error":
            return f"DB02 error: {result.get('message', 'unknown error')}"
        sizes = result.get("sizes", {})
        lines = []
        for n in names:
            if n in sizes:
                lines.append(f"  {n}: {sizes[n].get('volume_gb', '')} GB ({sizes[n].get('volume_mb', '')} MB)")
            else:
                lines.append(f"  {n}: no size found (not a column-store table in this system, or it does not exist)")
        return "Table sizes (DB02 / HANA M_CS_TABLES):\n" + "\n".join(lines)
    except Exception as exc:
        logger.exception("Chat tool: DB02 table sizes failed")
        return f"Error running DB02: {exc}"


def _exec_search_dvm(query: str) -> str:
    query = (query or "").strip()
    if not query:
        return "DVM Guide search needs a table name or keywords."
    try:
        hits = dvm_guide.search(query, limit=3, window=900)
    except Exception as exc:
        logger.exception("Chat tool: DVM Guide search failed for %r", query)
        return f"DVM Guide search failed: {exc}"
    if not hits:
        if not dvm_guide.load_index():
            return "The DVM Guide is not available (backend/resources/DVM_Guide.pdf is missing)."
        return f"The DVM Guide has nothing on '{query}'. (It covers about 200 tables, not every table.)"
    parts = []
    for h in hits:
        parts.append(f"DVM Guide section covering {', '.join(h['tables'][:6])}:\n{h['excerpt'].strip()}")
    return "\n\n---\n\n".join(parts)


def _exec_search_sap_for_me(query: str, keyword: str | None) -> str:
    query = (query or "").strip()
    if not query:
        return "SAP for Me search needs search text."
    settings = llm.load_settings()
    try:
        session = sap_for_me.open_session()
    except sap_for_me.SapForMeLoginError as exc:
        return f"SAP for Me sign-in failed: {exc}"
    except Exception as exc:
        logger.exception("Chat tool: SAP for Me could not start")
        return f"SAP for Me could not be opened: {sap_for_me._describe(exc)}"
    if session is None:
        return "SAP for Me is not available: no SAP for Me credentials are saved (use the SAP for Me panel on the left)."
    try:
        focus = (keyword or "").strip() or max(query.split(), key=len)
        articles, problem = session.fetch_articles(query, focus, settings, subject=query)
        if not articles:
            return f"Nothing found on SAP for Me: {problem}"
        per_article = 3500
        return "\n\n".join(
            f"SAP for Me result {n + 1} ({kind}) - {title}:\n{text[:per_article].strip()}"
            for n, (title, kind, text) in enumerate(articles)
        )
    except Exception as exc:
        logger.exception("Chat tool: SAP for Me search failed for %r", query)
        return f"SAP for Me search failed: {sap_for_me._describe(exc)}"
    finally:
        session.close()


def _exec_update(
    current_rows: list[dict],
    table_name: str,
    archiving_object: str | None,
    score: float | None,
    rationale: str | None,
) -> tuple[str, bool]:
    """
    Apply the requested change to *current_rows* in-place.
    Returns (confirmation_message, was_changed).
    """
    table = table_name.strip().upper()
    table_rows = [r for r in current_rows if (r.get("Table Name") or "").strip().upper() == table]

    if not table_rows:
        if not archiving_object:
            return f"Table {table} not found in results. Specify an archiving_object to add it.", False
        # Add brand-new entry for a table not yet in the results
        new_score = str(int(score)) if score is not None else "100"
        new_row: dict[str, str] = {
            "Table Name": table,
            "Table Description": "",
            "Archiving Object": archiving_object.upper().strip(),
            "Object Description": "",
            "Score": new_score,
            "Rationale": rationale or "Manually set by user via chat.",
        }
        current_rows.append(new_row)
        return (
            f"Added {table} with archiving object {archiving_object.upper().strip()} "
            f"(score {new_score}). Preview will refresh.",
            True,
        )

    if archiving_object:
        obj_upper = archiving_object.upper().strip()
        existing = next(
            (r for r in table_rows if (r.get("Archiving Object") or "").strip().upper() == obj_upper),
            None,
        )
        if existing:
            # Object already in the list — promote it by bumping its score
            if score is not None:
                new_score_val = str(int(score))
            else:
                max_score = max(
                    (float(r.get("Score") or 0) for r in table_rows), default=0
                )
                cur = float(existing.get("Score") or 0)
                new_score_val = str(int(max(max_score + 1, cur)))
            existing["Score"] = new_score_val
            if rationale:
                existing["Rationale"] = rationale
            return (
                f"Updated {table}: promoted {obj_upper} to score {new_score_val}. Preview will refresh.",
                True,
            )
        else:
            # New object not previously in list — add a row for this table
            first = table_rows[0]
            new_score = str(int(score)) if score is not None else "100"
            new_row = {
                "Table Name": table,
                "Table Description": first.get("Table Description", ""),
                "Archiving Object": obj_upper,
                "Object Description": "",
                "Score": new_score,
                "Rationale": rationale or "Manually set by user via chat.",
            }
            current_rows.append(new_row)
            return (
                f"Added {obj_upper} as an option for {table} with score {new_score}. "
                "It is now the recommended object. Preview will refresh.",
                True,
            )
    else:
        # No object change — just update score/rationale on the best row
        try:
            best = max(table_rows, key=lambda r: float(r.get("Score") or 0))
        except (TypeError, ValueError):
            best = table_rows[0]
        parts = []
        if score is not None:
            best["Score"] = str(int(score))
            parts.append(f"score → {int(score)}")
        if rationale:
            best["Rationale"] = rationale
            parts.append("rationale updated")
        if not parts:
            return f"No changes specified for {table}.", False
        return f"Updated {table}: {', '.join(parts)}. Preview will refresh.", True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_chat(
    message: str,
    scored_rows: list[dict],
    recommended: list[dict],
    history: list[dict],
) -> dict:
    """
    Run one conversational turn.

    Returns::

        {
            "reply": str,
            "updated_rows": list | None,        # present when rows were modified
            "updated_recommended": list | None,  # present when rows were modified
        }
    """
    settings = llm.load_settings()
    problem = llm.not_configured_message(settings)
    if problem:
        return {
            "reply": f"The chat assistant can't run yet. {problem}",
            "updated_rows": None,
            "updated_recommended": None,
        }
    limits = llm.budget(settings)

    # Work on a deep copy so the caller's data is never mutated here
    current_rows: list[dict] = deepcopy(scored_rows)
    was_modified = False

    if current_rows:
        context_text, table_count = _build_context(current_rows, limits["chat_context_tables"])
        results_section = _RESULTS_LOADED.format(context=context_text, table_count=table_count)
        tools = _TOOLS
    else:
        results_section = _RESULTS_EMPTY
        tools = [t for t in _TOOLS if t["name"] != "update_table_result"]
    system = _SYSTEM.format(results_section=results_section)

    messages: list[dict[str, Any]] = []
    for h in history[-limits["chat_history_messages"]:]:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    def execute_tool(name: str, inp: dict) -> str:
        nonlocal was_modified

        if name == "lookup_archiving_objects":
            return _exec_lookup((inp.get("table_name") or "").strip().upper())

        if name == "check_archiving_object":
            return _exec_check_archiving_object(inp.get("object_filter") or "")

        if name == "browse_table_contents":
            return _exec_browse_table(
                (inp.get("table_name") or "").strip().upper(),
                int(inp.get("max_rows") or 50),
                inp.get("where_clause") or None,
            )

        if name == "get_table_definition":
            return _exec_get_table_definition((inp.get("table_name") or "").strip().upper())

        if name == "analyze_table":
            return _exec_analyze_table(
                (inp.get("table_name") or "").strip().upper(), int(inp.get("max_rows") or 100)
            )

        if name == "get_archiving_sessions":
            return _exec_get_archiving_sessions((inp.get("archiving_object") or "").strip().upper())

        if name == "get_largest_tables":
            return _exec_largest_tables(int(inp.get("limit") or 10))

        if name == "get_table_sizes":
            names = inp.get("table_names") or []
            return _exec_table_sizes(names if isinstance(names, list) else [names])

        if name == "search_dvm_guide":
            return _exec_search_dvm(inp.get("query") or "")

        if name == "search_sap_for_me":
            return _exec_search_sap_for_me(inp.get("query") or "", inp.get("keyword"))

        if name == "update_table_result":
            if not current_rows:
                return "No scored results are loaded, so there is nothing to update."
            result_text, changed = _exec_update(
                current_rows,
                (inp.get("table_name") or "").strip().upper(),
                inp.get("archiving_object"),
                inp.get("score"),
                inp.get("rationale"),
            )
            if changed:
                was_modified = True
            return result_text

        return f"Unknown tool: {name}"

    try:
        reply = llm.run_tool_loop(
            system, messages, tools, execute_tool,
            max_rounds=limits["chat_max_rounds"], settings=settings,
        )
    except llm.LLMError as exc:
        reply = str(exc)

    if reply is None:
        reply = "Too many tool calls without finishing. Please try rephrasing."

    return {
        "reply": reply or "(no response)",
        "updated_rows": current_rows if was_modified else None,
        "updated_recommended": _recompute_recommended(current_rows) if was_modified else None,
    }
