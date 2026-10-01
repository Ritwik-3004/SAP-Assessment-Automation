"""
Reference document analysis.

Parses an uploaded document (Excel, PDF, PowerPoint) that contains past project
analysis of archiving object recommendations, then compares those mappings
against the current scored/recommended results to surface matches and mismatches.
"""

import io
import json
import logging
from typing import Optional

import anthropic
import openpyxl
from pypdf import PdfReader

from config import ANTHROPIC_API_KEY, SCORING_MODEL

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Text extraction
# ---------------------------------------------------------------------------

def _extract_excel_text(contents: bytes) -> str:
    wb = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        lines.append(f"=== Sheet: {sheet_name} ===")
        for row in ws.iter_rows(values_only=True):
            cells = [str(v).strip() if v is not None else "" for v in row]
            if any(cells):
                lines.append(" | ".join(cells))
    wb.close()
    return "\n".join(lines)


def _extract_pdf_text(contents: bytes) -> str:
    reader = PdfReader(io.BytesIO(contents))
    lines: list[str] = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text() or ""
        if text.strip():
            lines.append(f"=== Page {i + 1} ===")
            lines.append(text)
    return "\n".join(lines)


def _extract_pptx_text(contents: bytes) -> str:
    try:
        from pptx import Presentation  # type: ignore
    except ImportError as exc:
        raise ValueError(
            "python-pptx is required for PowerPoint files. "
            "Run: pip install python-pptx"
        ) from exc
    prs = Presentation(io.BytesIO(contents))
    lines: list[str] = []
    for i, slide in enumerate(prs.slides):
        lines.append(f"=== Slide {i + 1} ===")
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                lines.append(shape.text)
    return "\n".join(lines)


def _extract_text(contents: bytes, filename: str) -> str:
    lower = filename.lower()
    if lower.endswith((".xlsx", ".xls")):
        return _extract_excel_text(contents)
    if lower.endswith(".pdf"):
        return _extract_pdf_text(contents)
    if lower.endswith((".pptx", ".ppt")):
        return _extract_pptx_text(contents)
    raise ValueError(f"Unsupported file type: {filename}")


# ---------------------------------------------------------------------------
# Claude-based mapping extraction
# ---------------------------------------------------------------------------

def _extract_mappings_via_claude(text: str, table_names: list[str]) -> dict[str, str]:
    """
    Ask Claude to extract SAP table → archiving object pairs from *text*.
    Returns {TABLE_NAME: ARCHIVING_OBJECT} (both uppercased).
    """
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    tables_hint = ", ".join(table_names[:60])  # keep the prompt reasonable

    prompt = f"""You are analysing a reference document from an SAP archiving assessment project.
The document contains past experience or recommendations mapping SAP TABLE NAMES to ARCHIVING OBJECTS.

Extract every explicit mapping of a table name to an archiving object you can find.
Return ONLY a JSON object like:
{{"BKPF": "FI_DOCUMNT", "VBAK": "SD_VBAK", "MKPF": "MM_MATBEL"}}

We are particularly interested in these tables (but extract any you find):
{tables_hint}

Rules:
- Use exact SAP uppercase naming (e.g. FI_DOCUMNT, not fi_documnt).
- Only include mappings explicitly stated in the document — never guess.
- If a table appears with multiple objects, use the most recently mentioned one.
- Return ONLY the JSON object, no extra text.

Document:
{text[:14000]}
"""

    response = client.messages.create(
        model=SCORING_MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text.strip()

    # Strip optional markdown fences
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw

    try:
        mappings: dict = json.loads(raw)
        return {str(k).upper(): str(v).upper() for k, v in mappings.items()}
    except Exception:
        logger.warning("Could not parse Claude mapping response: %.200s", raw)
        return {}


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def analyze_reference_doc(
    contents: bytes,
    filename: str,
    recommended: list[dict],
) -> dict:
    """
    Parse *contents* (the uploaded reference document), extract table→object
    mappings, then compare with *recommended* (the current scored output).

    Returns
    -------
    {
        "status": "ok",
        "filename": str,
        "ref_mappings": {TABLE: OBJECT, ...},
        "annotated_recommended": [...rows with "Comments" and "Ref Doc Object" added...],
        "matches": [...rows where ref doc agrees...],
        "mismatches": [...rows where ref doc differs, with "Ref Doc Object" column...],
        "not_in_ref": [...rows not mentioned in the reference document...],
    }
    """
    # 1. Extract text
    try:
        text = _extract_text(contents, filename)
    except ValueError as exc:
        return {"status": "error", "message": str(exc)}
    except Exception as exc:
        return {"status": "error", "message": f"Could not read file: {exc}"}

    if not text.strip():
        return {"status": "error", "message": "Could not extract any text from the document."}

    # 2. Get table names from current recommendations
    table_names = [
        row.get("Table Name", "").upper()
        for row in recommended
        if row.get("Table Name")
    ]

    # 3. Extract mappings using Claude
    ref_mappings = _extract_mappings_via_claude(text, table_names)

    # 4. Compare
    matches: list[dict] = []
    mismatches: list[dict] = []
    not_in_ref: list[dict] = []
    annotated: list[dict] = []

    for row in recommended:
        table = row.get("Table Name", "").upper()
        current_obj = row.get("Archiving Object", "").upper()
        annotated_row = dict(row)

        if table in ref_mappings:
            ref_obj = ref_mappings[table]
            if ref_obj == current_obj or not current_obj:
                annotated_row["Comments"] = "Matches reference document"
                annotated_row["Ref Doc Object"] = ""
                matches.append(annotated_row)
            else:
                annotated_row["Comments"] = f"Reference document suggests: {ref_obj}"
                annotated_row["Ref Doc Object"] = ref_obj
                mismatches.append(annotated_row)
        else:
            annotated_row["Comments"] = ""
            annotated_row["Ref Doc Object"] = ""
            not_in_ref.append(annotated_row)

        annotated.append(annotated_row)

    return {
        "status": "ok",
        "filename": filename,
        "ref_mappings": ref_mappings,
        "annotated_recommended": annotated,
        "matches": matches,
        "mismatches": mismatches,
        "not_in_ref": not_in_ref,
    }
