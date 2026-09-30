"""
Chat agent for SAP assessment follow-up queries.

After scoring, users can ask questions in natural language. Claude acts as
the assistant and has access to a live DB15 tool that looks up archiving
objects for any table in the connected SAP system. For questions about
scores or rationale it answers from the context passed in — no SAP call needed.
"""
import logging
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
            "objects associated with a specific table. Use this to verify a result, "
            "look up a table not in the scored results, or explore alternatives. "
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
    }
]

_SYSTEM = """\
You are an SAP archivability assessment assistant. The user has run a batch \
archiving-object lookup and AI scoring across SAP tables. Answer follow-up \
questions, validate results, or look up additional information.

You have a tool to query the live SAP system (DB15) for any table. Call it \
whenever the user asks to check, verify, or look up archiving objects — never \
guess object names. For questions about scores or rationale, answer from the \
context below without calling the tool. Keep answers concise. When presenting \
SAP data use a short bulleted list. Do not call the tool for more than 5 tables \
per response.

=== Scored results ({table_count} tables) ===
{context}
"""


def _build_context(scored_rows: list[dict], recommended: list[dict]) -> tuple[str, int]:
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


def _run_tool(name: str, inputs: dict) -> str:
    if name != "lookup_archiving_objects":
        return f"Unknown tool: {name}"

    table = inputs.get("table_name", "").strip().upper()
    if not table:
        return "table_name is required."
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


def run_chat(
    message: str,
    scored_rows: list[dict],
    recommended: list[dict],
    history: list[dict],
) -> dict:
    """
    Run one conversational turn.

    *history* is a list of {"role": "user"|"assistant", "content": str} dicts
    from prior visible turns. Returns {"reply": str}.
    """
    if not ANTHROPIC_API_KEY:
        return {
            "reply": (
                "ANTHROPIC_API_KEY is not configured. "
                "Add it to backend/.env to enable the chat assistant."
            )
        }

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    context_text, table_count = _build_context(scored_rows, recommended)
    system = _SYSTEM.format(context=context_text, table_count=table_count)

    messages: list[dict[str, Any]] = []
    for h in history:
        if h.get("role") in ("user", "assistant") and h.get("content"):
            messages.append({"role": h["role"], "content": str(h["content"])})
    messages.append({"role": "user", "content": message})

    for _ in range(6):
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
            return {"reply": text or "(no response)"}

        tool_results = []
        assistant_content = list(response.content)
        for block in assistant_content:
            if block.type == "tool_use":
                logger.info("Chat tool call: %s(%s)", block.name, block.input)
                result_text = _run_tool(block.name, block.input)
                tool_results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result_text,
                })

        messages.append({"role": "assistant", "content": assistant_content})
        messages.append({"role": "user", "content": tool_results})

    return {"reply": "Too many tool calls without finishing. Please try rephrasing."}
