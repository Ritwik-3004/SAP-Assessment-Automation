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

logger = logging.getLogger(__name__)

_TOOLS = [
    {
        "name": "lookup_archiving_objects",
        "description": (
            "Query the live SAP system via transaction DB15 to find all archiving "
            "objects for a specific table. Use this to verify a result, look up a "
            "table not in the scored results, or explore alternatives before modifying. "
            "Limit to at most 5 tables per response."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "table_name": {
                    "type": "string",
                    "description": "SAP table name in uppercase, e.g. BKPF, BALDAT",
                }
            },
            "required": ["table_name"],
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
You are an SAP archivability assessment assistant. The user has run a batch \
archiving-object lookup and AI scoring across SAP tables. You can:
  1. Answer questions about the results from the context below.
  2. Look up live SAP data (DB15) to verify or discover archiving objects.
  3. Modify results when the user explicitly asks to change an object, score, \
or rationale — call update_table_result to apply the change; the preview \
updates automatically.

Never guess archiving object names — look them up first when unsure. Keep \
answers concise. When presenting SAP data use a short bulleted list. When you \
apply a modification, confirm what changed in your reply.

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
