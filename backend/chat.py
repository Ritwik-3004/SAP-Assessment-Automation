"""
Chat agent for SAP assessment follow-up queries.

Two capabilities:
  1. lookup_archiving_objects — runs DB15 on the live SAP session
  2. update_table_result — modifies the in-memory scored rows so the preview
     refreshes after the user approves a change in chat

Modified rows are returned alongside the text reply so the frontend can
update its state without re-running scoring.
"""
import logging
from copy import deepcopy
from typing import Any

import anthropic

from config import ANTHROPIC_API_KEY, SCORING_MODEL
from sap_connector import sap
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
You are an SAP archivability assessment assistant with access to a full set of \
SAP transactions. The user has run a batch archiving-object lookup and AI scoring. \
You can answer questions from the scored context, look up live SAP data, or \
modify results on request.

TOOL SELECTION — pick the right tool for each question type:
  • "which archiving objects cover table X?" → lookup_archiving_objects (DB15)
  • "does object Y exist / what is FI_* / list MM_ objects" → check_archiving_object (AOBJ)
  • "show me rows / data in table X" → browse_table_contents (SE16N)
  • "what fields / structure does table X have?" → get_table_definition (SE11)
  • "how many rows / size / has table X been archived?" → analyze_table (TAANA)
  • "archiving history / sessions for object Y" → get_archiving_sessions (SARA)
  • "change / set / update a result" → update_table_result
  • question answerable from the scored context → answer directly, no tool

Rules:
  - Never guess archiving object names; use check_archiving_object to verify first.
  - Keep answers concise. Use short bulleted lists for SAP data.
  - Confirm what changed when applying a modification.
  - Call at most 5 SAP tools per response.

=== Scored results ({table_count} tables) ===
{context}
"""


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(scored_rows: list[dict]) -> tuple[str, int]:
    by_table: dict[str, list[dict]] = {}
    for row in scored_rows:
        tbl = (row.get("Table Name") or "").strip().upper()
        if tbl:
            by_table.setdefault(tbl, []).append(row)

    lines = []
    for tbl, rows in list(by_table.items())[:60]:
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

    if len(by_table) > 60:
        lines.append(f"... and {len(by_table) - 60} more tables (ask about specific ones)")

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
    if not ANTHROPIC_API_KEY:
        return {
            "reply": (
                "ANTHROPIC_API_KEY is not configured. "
                "Add it to backend/.env to enable the chat assistant."
            ),
            "updated_rows": None,
            "updated_recommended": None,
        }

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Work on a deep copy so the caller's data is never mutated here
    current_rows: list[dict] = deepcopy(scored_rows)
    was_modified = False

    context_text, table_count = _build_context(current_rows)
    system = _SYSTEM.format(context=context_text, table_count=table_count)

    messages: list[dict[str, Any]] = []
    for h in history:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    for _ in range(8):
        response = client.messages.create(
            model=SCORING_MODEL,
            max_tokens=1500,
            system=system,
            messages=messages,
            tools=_TOOLS,
        )

        if response.stop_reason != "tool_use":
            text = "".join(
                b.text for b in response.content if hasattr(b, "text")
            ).strip()
            updated_recommended = _recompute_recommended(current_rows) if was_modified else None
            return {
                "reply": text or "(no response)",
                "updated_rows": current_rows if was_modified else None,
                "updated_recommended": updated_recommended,
            }

        tool_results = []
        assistant_content = list(response.content)
        for block in assistant_content:
            if block.type != "tool_use":
                continue

            logger.info("Chat tool call: %s(%s)", block.name, block.input)
            inp = block.input

            if block.name == "lookup_archiving_objects":
                table = (inp.get("table_name") or "").strip().upper()
                result_text = _exec_lookup(table)

            elif block.name == "check_archiving_object":
                obj_filter = inp.get("object_filter") or ""
                result_text = _exec_check_archiving_object(obj_filter)

            elif block.name == "browse_table_contents":
                table = (inp.get("table_name") or "").strip().upper()
                max_r = int(inp.get("max_rows") or 50)
                where = inp.get("where_clause") or None
                result_text = _exec_browse_table(table, max_r, where)

            elif block.name == "get_table_definition":
                table = (inp.get("table_name") or "").strip().upper()
                result_text = _exec_get_table_definition(table)

            elif block.name == "analyze_table":
                table = (inp.get("table_name") or "").strip().upper()
                max_r = int(inp.get("max_rows") or 100)
                result_text = _exec_analyze_table(table, max_r)

            elif block.name == "get_archiving_sessions":
                obj = (inp.get("archiving_object") or "").strip().upper()
                result_text = _exec_get_archiving_sessions(obj)

            elif block.name == "update_table_result":
                table = (inp.get("table_name") or "").strip().upper()
                obj = inp.get("archiving_object")
                sc = inp.get("score")
                rat = inp.get("rationale")
                result_text, changed = _exec_update(current_rows, table, obj, sc, rat)
                if changed:
                    was_modified = True

            else:
                result_text = f"Unknown tool: {block.name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result_text,
            })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    return {
        "reply": "Too many tool calls without finishing. Please try rephrasing.",
        "updated_rows": current_rows if was_modified else None,
        "updated_recommended": _recompute_recommended(current_rows) if was_modified else None,
    }
