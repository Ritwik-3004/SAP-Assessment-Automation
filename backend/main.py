"""
SAP Assessment Automation — FastAPI backend.

Start with:
    uvicorn main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import io
import json
import logging
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from sap_connector import sap
from progress import ProgressTracker
from config import INPUT_DIR, OUTPUT_DIR, CREDENTIALS_FILE, SAP_FOR_ME_CREDENTIALS_FILE
import scoring
import grouping
import llm
import header_tables
import object_descriptions
import reference_doc as ref_doc_mod
import transactions.taana as taana
import transactions.db15 as db15
import transactions.db02 as db02
import transactions.se16n as se16n
import transactions.se11 as se11
import transactions.aobj as aobj
import transactions.sara as sara

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("Input folder:  %s", INPUT_DIR)
    logger.info("Output folder: %s", OUTPUT_DIR)
    yield


app = FastAPI(
    title="SAP Assessment Automation API",
    version="1.0.0",
    description="Backend for SAP archivability analysis via GUI scripting.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Single-user local app: one shared tracker per long-running batch operation
# is enough, no job IDs needed. The frontend polls the matching /progress
# endpoint while the background thread below runs the real work.
_db15_batch_progress = ProgressTracker()
_scoring_progress = ProgressTracker()
_header_table_progress = ProgressTracker()

# Column order for the "Grouped by Object" sheet/endpoint — mirrors the
# "Recommended" row shape (grouping.build_object_groups() only adds fields,
# never renames the existing ones) plus the three new per-group columns.
# Rationale is intentionally omitted here -- it's already on the
# "Recommended" sheet and would just be noise repeated per group member.
GROUPED_COLUMNS = [
    "Archiving Object", "Object Description", "Housekeeping Program",
    "Table Name", "Table Description", "Volume (GB)", "Volume (MB)",
    "Cumulative Size (GB)", "Cumulative Size (MB)", "Table Count",
]

# Group-level columns in GROUPED_COLUMNS -- constant across every member row
# of a group (see grouping.build_object_groups()) -- get vertically merged
# into one cell per group in the exported sheet, so a group with several
# tables doesn't repeat the same Archiving Object/Housekeeping
# Program/cumulative-size value on every row. Table Name/Table
# Description/Volume are per-table and are never merged.
GROUPED_MERGE_COLUMNS = [
    "Archiving Object", "Object Description", "Housekeeping Program",
    "Cumulative Size (GB)", "Cumulative Size (MB)", "Table Count",
]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ConnectRequest(BaseModel):
    system: str
    client: str
    username: str
    password: str
    language: str = "EN"


class TaanaRequest(BaseModel):
    table_name: Optional[str] = None
    max_rows: int = 500


class Db15Request(BaseModel):
    table_name: str


class Se16nRequest(BaseModel):
    table_name: str
    max_rows: int = 200
    where_clause: Optional[str] = None


class Se11Request(BaseModel):
    table_name: str


class AobjRequest(BaseModel):
    object_filter: Optional[str] = None


class SaraRequest(BaseModel):
    archiving_object: str


class Db15ExportRequest(BaseModel):
    rows: list[dict[str, str]]


class Db15ScoreRequest(BaseModel):
    rows: list[dict[str, str]]


class Db15ScoreExportRequest(BaseModel):
    rows: list[dict[str, str]]
    recommended: list[dict[str, str]]


class GroupByObjectRequest(BaseModel):
    recommended: list[dict[str, str]]


class Db02TopTablesRequest(BaseModel):
    limit: int = 300


class Db02ExportRequest(BaseModel):
    rows: list[dict[str, str]]


class Db02DebugClickRequest(BaseModel):
    tree_id: str
    node_key: str
    action: str = "select"


class Db15BatchFromInputRequest(BaseModel):
    filename: str


class HeaderTableBatchFromOutputRequest(BaseModel):
    filename: str
    max_objects: int = 20


# Columns of the Find Header Tables sheet/exports.
HEADER_TABLE_COLUMNS = ["Archiving Object", "Header Table", "Source", "Confidence", "Comments"]


class HeaderTableExportRequest(BaseModel):
    rows: list[dict[str, str]]


class SaveTablesRequest(BaseModel):
    rows: list[dict[str, str]]


class ChatHistoryItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    message: str
    scored_rows: list[dict[str, str]] = []
    recommended: list[dict[str, str]] = []
    history: list[ChatHistoryItem] = []


class SaveCredentialsRequest(BaseModel):
    system: str
    client: str
    username: str
    password: str
    language: str = "EN"


class SapForMeCredentialsRequest(BaseModel):
    email: str
    password: str


class LlmSettingsRequest(BaseModel):
    provider: str
    model: str = ""


# ---------------------------------------------------------------------------
# SAP session endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    return {"status": "ok", "sap_connected": sap.is_connected}


@app.get("/api/sap/systems")
def list_systems():
    """Return the SAP systems configured in the local SAP Logon pad."""
    systems = sap.list_systems()
    return {"systems": systems}


@app.post("/api/sap/connect")
def connect(req: ConnectRequest):
    result = sap.connect(
        system=req.system,
        client=req.client,
        username=req.username,
        password=req.password,
        language=req.language,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/sap/disconnect")
def disconnect():
    return sap.disconnect()


@app.get("/api/sap/status")
def status():
    return {"connected": sap.is_connected}


@app.get("/api/sap/info")
def sap_info():
    """Return current connection state including system and user — used by the
    frontend on page load to restore the session without re-entering credentials."""
    return {
        "connected": sap.is_connected,
        "system": sap.connected_system,
        "user": sap.connected_user,
    }


@app.post("/api/sap/credentials")
def save_credentials(req: SaveCredentialsRequest):
    """Persist connection details (including password) to a local JSON file so
    the login form auto-fills on the next session."""
    data = {
        "system": req.system,
        "client": req.client,
        "username": req.username,
        "password": req.password,
        "language": req.language,
    }
    CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"saved": True}


@app.get("/api/sap/credentials")
def load_credentials():
    """Return previously saved connection details, or an empty object if none."""
    if not CREDENTIALS_FILE.exists():
        return {}
    try:
        return json.loads(CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


@app.post("/api/sap-for-me/credentials")
def save_sap_for_me_credentials(req: SapForMeCredentialsRequest):
    """Persist SAP for Me sign-in details (used by housekeeping.py's SAP for
    Me fallback to auto-login) to a local JSON file."""
    data = {"email": req.email, "password": req.password}
    SAP_FOR_ME_CREDENTIALS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return {"saved": True}


@app.get("/api/sap-for-me/credentials")
def load_sap_for_me_credentials():
    """Return previously saved SAP for Me sign-in details, or an empty object if none."""
    if not SAP_FOR_ME_CREDENTIALS_FILE.exists():
        return {}
    try:
        return json.loads(SAP_FOR_ME_CREDENTIALS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# AI model selection (Claude or Groq) -- applies to every AI step in the app
# ---------------------------------------------------------------------------

@app.get("/api/llm/settings")
def get_llm_settings():
    """Active AI provider/model plus the options the UI can offer. API keys are never
    returned, only whether each is set in backend/.env."""
    return llm.public_settings()


@app.post("/api/llm/settings")
def save_llm_settings(req: LlmSettingsRequest):
    """Choose the AI model used by scoring, housekeeping lookups, SAP for Me
    extraction, reference-document analysis and chat. API keys are not accepted
    here: they come from backend/.env."""
    try:
        llm.save_settings(req.provider, req.model)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return llm.public_settings()


@app.get("/api/llm/usage")
def get_llm_usage():
    """Today's request/token count for the active model (and Groq's free-tier limits)."""
    return llm.usage()


@app.post("/api/llm/test")
def test_llm():
    """Make one tiny call to check the saved key/model work."""
    return llm.test_connection()


# ---------------------------------------------------------------------------
# Chat agent endpoint
# ---------------------------------------------------------------------------

@app.post("/api/chat")
def chat_endpoint(req: ChatRequest):
    """Natural-language chat over scored results, with live SAP DB15 tool access."""
    import chat as chat_module
    result = chat_module.run_chat(
        message=req.message,
        scored_rows=req.scored_rows,
        recommended=req.recommended,
        history=[{"role": h.role, "content": h.content} for h in req.history],
    )
    return result


# ---------------------------------------------------------------------------
# File folder endpoints
# ---------------------------------------------------------------------------

@app.get("/api/files/input")
def list_input_files():
    """List Excel files in the input folder, newest-modified first."""
    files = sorted(
        [f for f in INPUT_DIR.glob("*.xlsx")] + [f for f in INPUT_DIR.glob("*.xls")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return {"files": [f.name for f in files]}


@app.get("/api/files/output")
def list_output_files():
    """List Excel files in the output folder, newest-modified first --
    mirrors list_input_files() above. Used to auto-detect
    archiving_objects_scored.xlsx for the header-table lookup tool."""
    files = sorted(
        [f for f in OUTPUT_DIR.glob("*.xlsx")] + [f for f in OUTPUT_DIR.glob("*.xls")],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    return {"files": [f.name for f in files]}


@app.post("/api/files/input/save")
def save_tables_to_input(req: SaveTablesRequest):
    """Save the table list rows to input/list_of_tables.xlsx."""
    columns = ["Table Name", "Description"] + _optional_size_columns(req.rows)
    buf = _build_workbook(req.rows, columns=columns, sheet_title="Top Tables")
    dest = INPUT_DIR / "list_of_tables.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


@app.post("/api/files/output/save-archiving")
def save_archiving_to_output(req: Db15ExportRequest):
    """Save archiving-objects rows to output/archiving_objects_by_table.xlsx."""
    buf = _build_workbook(
        req.rows,
        columns=["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description"],
        sheet_title="DB15 Results",
    )
    dest = OUTPUT_DIR / "archiving_objects_by_table.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


@app.post("/api/files/output/save-scored")
def save_scored_to_output(req: Db15ScoreExportRequest):
    """Save scored archiving-objects to output/archiving_objects_scored.xlsx."""
    buf = _build_multi_sheet_workbook([
        (
            "All Scored Objects",
            ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description", "Housekeeping Program", "Score"],
            req.rows,
        ),
        (
            "Recommended",
            ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description", "Housekeeping Program", "Score", "Rationale"],
            req.recommended,
        ),
        (
            "Grouped by Object",
            GROUPED_COLUMNS,
            grouping.build_object_groups(req.recommended),
            GROUPED_MERGE_COLUMNS,
        ),
    ])
    dest = OUTPUT_DIR / "archiving_objects_scored.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


# ---------------------------------------------------------------------------
# Transaction endpoints
# ---------------------------------------------------------------------------

@app.post("/api/transactions/taana")
def run_taana(req: TaanaRequest):
    _require_connection()
    result = taana.run(table_name=req.table_name, max_rows=req.max_rows)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/db15")
def run_db15(req: Db15Request):
    _require_connection()
    result = db15.run(table_name=req.table_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/se16n")
def run_se16n(req: Se16nRequest):
    _require_connection()
    result = se16n.run(
        table_name=req.table_name,
        max_rows=req.max_rows,
        where_clause=req.where_clause,
    )
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/se11")
def run_se11(req: Se11Request):
    _require_connection()
    result = se11.run(table_name=req.table_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/aobj")
def run_aobj(req: AobjRequest):
    _require_connection()
    result = aobj.run(object_filter=req.object_filter)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/sara")
def run_sara(req: SaraRequest):
    _require_connection()
    result = sara.run(archiving_object=req.archiving_object)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


# ---------------------------------------------------------------------------
# DB15 batch (Excel upload -> run DB15 per table -> Excel export)
# ---------------------------------------------------------------------------

@app.post("/api/transactions/db15/batch")
def run_db15_batch(file: UploadFile = File(...)):
    _require_connection()
    if _db15_batch_progress.is_running():
        raise HTTPException(status_code=409, detail="A batch lookup is already in progress.")

    contents = file.file.read()
    tables = _parse_table_list(contents)
    if not tables:
        raise HTTPException(
            status_code=400,
            detail="No table names found in the uploaded file. Expected a table "
            "name in column A (and optionally a description in column B), "
            "starting from row 2.",
        )

    _db15_batch_progress.start(len(tables))
    threading.Thread(target=_run_db15_batch_job, args=(tables,), daemon=True).start()
    return {"status": "started", "total": len(tables)}


@app.post("/api/transactions/db15/batch-from-input")
def run_db15_batch_from_input(req: Db15BatchFromInputRequest):
    """Start a DB15 batch job using a file that already exists in the input folder."""
    _require_connection()
    if _db15_batch_progress.is_running():
        raise HTTPException(status_code=409, detail="A batch lookup is already in progress.")

    safe_name = Path(req.filename).name
    file_path = INPUT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"'{safe_name}' not found in the input folder.")

    tables = _parse_table_list(file_path.read_bytes())
    if not tables:
        raise HTTPException(
            status_code=400,
            detail="No table names found in the file. Expected a table name in column A "
            "(and optionally a description in column B), starting from row 2.",
        )

    _db15_batch_progress.start(len(tables))
    threading.Thread(target=_run_db15_batch_job, args=(tables,), daemon=True).start()
    return {"status": "started", "total": len(tables)}


@app.get("/api/transactions/db15/batch/progress")
def get_db15_batch_progress():
    return _db15_batch_progress.snapshot()


@app.get("/api/transactions/db15/debug-screen")
def debug_db15_screen():
    """Diagnostic: dump every element on the DB15 selection screen (id, type,
    name, text) to discover the real radio-button / field IDs for this SAP
    system. Not used by the UI — call directly (e.g. via browser or curl)
    while connected to SAP."""
    _require_connection()
    result = db15.debug_dump_screen()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "debug dump failed"))
    return result


@app.get("/api/transactions/db15/debug-grid")
def debug_db15_grid(table_name: str):
    """Diagnostic: filter DB15 by *table_name* and report exactly what
    happens reading the results grid (found / row count / column order /
    first cell), including raw error text on failure at each step."""
    _require_connection()
    result = db15.debug_read_grid(table_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "debug dump failed"))
    return result


@app.post("/api/transactions/db15/export")
def export_db15_batch(req: Db15ExportRequest):
    buffer = _build_workbook(
        req.rows,
        columns=["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description"],
        sheet_title="DB15 Results",
    )
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=archiving_objects_by_table.xlsx"},
    )


@app.post("/api/transactions/db15/score")
def score_db15_batch(req: Db15ScoreRequest):
    if _scoring_progress.is_running():
        raise HTTPException(status_code=409, detail="A scoring run is already in progress.")

    distinct_tables = {row.get("Table Name", "") for row in req.rows}
    _scoring_progress.start(len(distinct_tables))

    def job():
        try:
            result = scoring.score_archiving_objects(req.rows, on_progress=_scoring_progress.update)
            if result["status"] == "error":
                _scoring_progress.fail(result["message"])
            else:
                _scoring_progress.finish(result)
        except Exception as exc:
            logger.exception("Scoring job failed")
            _scoring_progress.fail(str(exc))

    threading.Thread(target=job, daemon=True).start()
    return {"status": "started", "total": len(distinct_tables)}


@app.get("/api/transactions/db15/score/progress")
def get_db15_score_progress():
    return _scoring_progress.snapshot()


@app.post("/api/transactions/db15/score-export")
def export_db15_scored(req: Db15ScoreExportRequest):
    buffer = _build_multi_sheet_workbook([
        (
            "All Scored Objects",
            ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description", "Housekeeping Program", "Score"],
            req.rows,
        ),
        (
            "Recommended",
            ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)", "Archiving Object", "Object Description", "Housekeeping Program", "Score", "Rationale"],
            req.recommended,
        ),
        (
            "Grouped by Object",
            GROUPED_COLUMNS,
            grouping.build_object_groups(req.recommended),
            GROUPED_MERGE_COLUMNS,
        ),
    ])
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=archiving_objects_scored.xlsx"},
    )


@app.post("/api/transactions/db15/group-by-object")
def group_db15_by_object(req: GroupByObjectRequest):
    """Group scored tables by their recommended Archiving Object or
    Housekeeping Program, sorted by cumulative size — pure in-memory
    computation over already-scored data, so no progress polling needed."""
    return {"status": "ok", "rows": grouping.build_object_groups(req.recommended)}


# ---------------------------------------------------------------------------
# DB02 (generate a starting table list via the SQL Editor's "top tables by
# size" query, for users who don't already have a list of tables to check)
# ---------------------------------------------------------------------------

@app.post("/api/transactions/db02/top-tables")
def get_db02_top_tables(req: Db02TopTablesRequest):
    _require_connection()
    result = db02.run_get_top_tables(req.limit)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


@app.post("/api/transactions/db02/export")
def export_db02_top_tables(req: Db02ExportRequest):
    columns = ["Table Name", "Description"] + _optional_size_columns(req.rows)
    buffer = _build_workbook(
        req.rows,
        columns=columns,
        sheet_title="Top Tables",
    )
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=db02_top_tables.xlsx"},
    )


@app.get("/api/transactions/db02/debug-screen")
def debug_db02_screen(container_id: str = "wnd[0]/usr"):
    """Diagnostic: navigate to DB02 and dump every element under
    *container_id* (id, type, name, text). Not used by the UI — call
    directly while connected to SAP to discover the real tree/SQL editor
    structure before wiring up db02.run_get_top_tables()."""
    _require_connection()
    result = db02.debug_dump_screen(container_id)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "debug dump failed"))
    return result


@app.get("/api/transactions/db02/debug-tree")
def debug_db02_tree(tree_id: str):
    """Diagnostic: navigate to DB02, find the tree control at *tree_id*, and
    list every node's key + display text, to identify the real node keys for
    "Diagnostics" and "SQL Editor" without guessing."""
    _require_connection()
    result = db02.debug_dump_tree(tree_id)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "tree dump failed"))
    return result


@app.get("/api/transactions/db02/debug-probe-run")
def debug_db02_probe_run(limit: int = 20):
    """Diagnostic: drill into SQL Editor, try setting the query text,
    pressing F8 to execute, switching to the Result tab, and dumping the
    screen afterward — reporting each step's outcome independently."""
    _require_connection()
    result = db02.debug_probe_run(limit)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "probe run failed"))
    return result


@app.post("/api/transactions/db02/debug-click")
def debug_db02_click(req: Db02DebugClickRequest):
    """Diagnostic: navigate to DB02, perform *action* on *node_key* in the
    tree at *tree_id*, then dump wnd[0]/usr afterwards so the effect of the
    click is visible in the response."""
    _require_connection()
    result = db02.debug_click_tree_node(req.tree_id, req.node_key, req.action)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "tree click failed"))
    return result


# ---------------------------------------------------------------------------
# Header table lookup (SE16N / ARCH_DEF) for the top N archiving objects by
# cumulative size, sourced from the scored workbook's "Grouped by Object"
# sheet or an uploaded equivalent.
# ---------------------------------------------------------------------------

@app.post("/api/transactions/header-tables/batch")
def run_header_table_batch(file: UploadFile = File(...), max_objects: int = 20):
    _require_connection()
    if _header_table_progress.is_running():
        raise HTTPException(status_code=409, detail="A header-table lookup is already in progress.")

    contents = file.file.read()
    archiving_objects = _parse_top_archiving_objects(contents, limit=max_objects)
    if not archiving_objects:
        raise HTTPException(
            status_code=400,
            detail="No 'Archiving Object' column with values found in the uploaded file's "
            "last sheet. Expected a sheet shaped like the 'Grouped by Object' sheet from "
            "archiving_objects_scored.xlsx.",
        )

    _header_table_progress.start(len(archiving_objects))
    threading.Thread(target=_run_header_table_job, args=(archiving_objects,), daemon=True).start()
    return {"status": "started", "total": len(archiving_objects)}


@app.post("/api/transactions/header-tables/batch-from-output")
def run_header_table_batch_from_output(req: HeaderTableBatchFromOutputRequest):
    """Start a header-table lookup job using a file that already exists in
    the output folder (e.g. archiving_objects_scored.xlsx)."""
    _require_connection()
    if _header_table_progress.is_running():
        raise HTTPException(status_code=409, detail="A header-table lookup is already in progress.")

    safe_name = Path(req.filename).name
    file_path = OUTPUT_DIR / safe_name
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"'{safe_name}' not found in the output folder.")

    archiving_objects = _parse_top_archiving_objects(file_path.read_bytes(), limit=req.max_objects)
    if not archiving_objects:
        raise HTTPException(
            status_code=400,
            detail=f"No 'Archiving Object' column with values found in '{safe_name}''s last sheet.",
        )

    _header_table_progress.start(len(archiving_objects))
    threading.Thread(target=_run_header_table_job, args=(archiving_objects,), daemon=True).start()
    return {"status": "started", "total": len(archiving_objects)}


@app.get("/api/transactions/header-tables/batch/progress")
def get_header_table_batch_progress():
    return _header_table_progress.snapshot()


@app.post("/api/transactions/header-tables/export")
def export_header_tables(req: HeaderTableExportRequest):
    buffer = _build_workbook(
        req.rows,
        columns=HEADER_TABLE_COLUMNS,
        sheet_title="Header Tables",
    )
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=header_tables.xlsx"},
    )


@app.post("/api/files/output/save-header-tables")
def save_header_tables_to_output(req: HeaderTableExportRequest):
    """Save header-table rows to output/header_tables.xlsx."""
    buf = _build_workbook(
        req.rows,
        columns=HEADER_TABLE_COLUMNS,
        sheet_title="Header Tables",
    )
    dest = OUTPUT_DIR / "header_tables.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


@app.get("/api/transactions/se16n/debug-arch-def-screen")
def debug_arch_def_screen():
    """Diagnostic: navigate to SE16N, load ARCH_DEF's Selection Criteria
    screen, and dump every element (id/type/name/text). Not used by the UI —
    call directly while connected to SAP to discover the real 'Arch. Object'
    field ID before wiring up se16n.find_header_table()."""
    _require_connection()
    result = se16n.debug_dump_arch_def_screen()
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "debug dump failed"))
    return result


@app.get("/api/transactions/se16n/debug-arch-def-query")
def debug_arch_def_query(archiving_object: str, table: str = "ARCH_DEF"):
    """Diagnostic: query ARCH_DEF for *archiving_object* and report every
    step's outcome independently (filter readback, status bar text after
    Execute, each candidate grid path's found/row_count/column_order/
    first_row) instead of only the final result — for diagnosing a wrong/
    empty result."""
    _require_connection()
    result = se16n.debug_query_arch_def(archiving_object, table.upper())
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result.get("message", "debug query failed"))
    return result


def _run_header_table_job(archiving_objects: list[str]):
    try:
        # Stage 1 (holds the SAP GUI session): read ARCH_DEF for every object. Objects with
        # exactly one top-level segment are resolved here; the rest come back as "ambiguous".
        result = se16n.run_batch_find_header_tables(archiving_objects, on_progress=_header_table_progress.update)
        if result["status"] == "error":
            _header_table_progress.fail(result["message"])
            return

        # Stage 2 (SAP session no longer needed): settle ambiguous objects with the DVM Guide
        # and SAP for Me. The bar stays full; the message says which object is being worked on.
        ambiguous = result.pop("ambiguous", [])
        if ambiguous:
            total = _header_table_progress.snapshot()["total"]
            try:
                result["rows"] = header_tables.resolve(
                    result["rows"], ambiguous,
                    on_progress=lambda msg: _header_table_progress.update(total, msg),
                )
            except Exception as exc:
                logger.exception("Header-table resolution failed")
                result.setdefault("errors", []).append(
                    {"archiving_object": "(several)", "message": f"Could not settle ambiguous objects: {exc}"}
                )

        # Stage 3 (SAP session again): only report header tables that really exist.
        total = _header_table_progress.snapshot()["total"]
        _header_table_progress.update(total, "Verifying the header tables exist in SAP…")
        try:
            result["rows"] = header_tables.verify_exist(result["rows"], ambiguous, se16n.tables_exist)
        except Exception as exc:
            logger.exception("Header-table existence check failed")
            result.setdefault("errors", []).append(
                {"archiving_object": "(several)", "message": f"Could not verify that the header tables exist in SAP: {exc}"}
            )
        _header_table_progress.finish(result)
    except Exception as exc:
        logger.exception("Header-table batch job failed")
        _header_table_progress.fail(str(exc))


# ---------------------------------------------------------------------------
# Reference document analysis
# ---------------------------------------------------------------------------

class ReferenceDocSaveRequest(BaseModel):
    rows: list[dict]
    recommended: list[dict]


@app.post("/api/reference-doc/analyze")
def analyze_reference_doc(
    files: list[UploadFile] = File(...),
    recommended: str = Form(...),
    known_descriptions: str = Form("{}"),
):
    """Parse the uploaded reference document(s) and compare their archiving-object
    mappings against the current scored recommendations. Several documents are merged in
    upload order (the first to mention a table wins). *known_descriptions* is a JSON object
    {archiving object: description} from the current run."""
    documents = [(f.file.read(), f.filename or "document") for f in files]
    try:
        recommended_rows = json.loads(recommended)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'recommended' field.")
    try:
        known = json.loads(known_descriptions)
        known = known if isinstance(known, dict) else {}
    except Exception:
        known = {}
    result = ref_doc_mod.analyze_reference_doc(documents, recommended_rows, known)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


def _reference_doc_workbook(req: "ReferenceDocSaveRequest") -> io.BytesIO:
    """The reference-reviewed archiving-object workbook: the scored list, the recommended list
    with the reference-document comments, and -- as in archiving_objects_scored.xlsx -- a last
    "Grouped by Object" sheet built from the post-override recommended list. It stays last because
    Find Header Tables reads the last sheet of an output file as the size-sorted grouped list."""
    ref_cols = ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)",
                "Archiving Object", "Object Description", "Housekeeping Program",
                "Score", "Rationale", "Comments"]
    # A row with an object but no description gets one from the same object elsewhere in the list or
    # from the remembered list (cheap layers only: saving never calls SAP or the AI model).
    recommended = object_descriptions.fill_missing(req.recommended)
    return _build_multi_sheet_workbook([
        (
            "All Scored Objects",
            ["Table Name", "Table Description", "Volume (GB)", "Volume (MB)",
             "Archiving Object", "Object Description", "Housekeeping Program", "Score"],
            req.rows,
        ),
        (
            "Recommended (with Ref Doc)",
            ref_cols,
            recommended,
        ),
        (
            "Grouped by Object",
            GROUPED_COLUMNS,
            grouping.build_object_groups(recommended),
            GROUPED_MERGE_COLUMNS,
        ),
    ])


@app.post("/api/reference-doc/save")
def save_reference_doc_to_output(req: ReferenceDocSaveRequest):
    """Save the reference-doc-annotated recommended list to the output folder."""
    buf = _reference_doc_workbook(req)
    dest = OUTPUT_DIR / "archiving_objects_with_reference.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


@app.post("/api/reference-doc/export")
def export_reference_doc(req: ReferenceDocSaveRequest):
    """Download the reference-doc-annotated recommended list as an Excel file."""
    return StreamingResponse(
        _reference_doc_workbook(req),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=archiving_objects_with_reference.xlsx"},
    )


# ---------------------------------------------------------------------------
# Reference document analysis for header tables (archiving object -> header table)
# ---------------------------------------------------------------------------

class HeaderReferenceSaveRequest(BaseModel):
    rows: list[dict]    # the header tables as the app found them
    final: list[dict]   # after the reference-document review (with "Reference Check")


def _header_reference_workbook(req: HeaderReferenceSaveRequest) -> io.BytesIO:
    return _build_multi_sheet_workbook([
        ("Header Tables", HEADER_TABLE_COLUMNS, req.rows),
        ("Header Tables (with Reference)", HEADER_TABLE_COLUMNS + ["Reference Check"], req.final),
    ])


@app.post("/api/header-reference/analyze")
def analyze_header_reference(
    files: list[UploadFile] = File(...),
    rows: str = Form(...),
):
    """Parse the uploaded reference document(s) and compare their archiving object ->
    header table mappings against the header tables the app found. Several documents are
    merged in upload order (the first to mention an object wins)."""
    documents = [(f.file.read(), f.filename or "document") for f in files]
    try:
        header_rows = json.loads(rows)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON in 'rows' field.")
    result = ref_doc_mod.analyze_header_reference(documents, header_rows)
    if result.get("status") == "error":
        raise HTTPException(status_code=400, detail=result["message"])
    return result


@app.post("/api/header-reference/save")
def save_header_reference_to_output(req: HeaderReferenceSaveRequest):
    """Save the reference-reviewed header tables to the output folder."""
    buf = _header_reference_workbook(req)
    dest = OUTPUT_DIR / "header_tables_with_reference.xlsx"
    dest.write_bytes(buf.getvalue())
    return {"saved": True, "path": str(dest)}


@app.post("/api/header-reference/export")
def export_header_reference(req: HeaderReferenceSaveRequest):
    """Download the reference-reviewed header tables as an Excel file."""
    return StreamingResponse(
        _header_reference_workbook(req),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=header_tables_with_reference.xlsx"},
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_connection():
    if not sap.is_connected:
        raise HTTPException(
            status_code=403,
            detail="Not connected to SAP. POST /api/sap/connect first.",
        )


_HEADER_ALIASES = {
    "tablename": "table_name",
    "table": "table_name",
    "tabname": "table_name",
    "description": "description",
    "tabledescription": "description",
    "desc": "description",
    "ddtext": "description",
    "volumegb": "volume_gb",
    "volume": "volume_gb",
    "sizegb": "volume_gb",
    "size": "volume_gb",
    "tablesizegb": "volume_gb",
    "tablesize": "volume_gb",
    "volumemb": "volume_mb",
    "sizemb": "volume_mb",
    "tablesizemb": "volume_mb",
    "tablevolumemb": "volume_mb",
}


def _normalize_header(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.strip().lower())


def _parse_table_list(contents: bytes) -> list[dict]:
    """Read (table_name, description, [volume_gb], [volume_mb]) entries from
    an uploaded Excel file. Columns are located by header name (row 1) —
    recognizing "Table Name" / "Description" / "Volume (GB)" / "Volume (MB)"
    and a few common variants, case/spacing-insensitive — so a file
    re-uploaded from this app's own DB02/DB15 exports (which already carry
    size columns) round-trips correctly. Falls back to plain column A/B
    position for name/description if the header doesn't match anything
    recognized, so a bare two-column list without recognizable headers still
    works.

    A row's "volume_gb"/"volume_mb" key is present only when that size
    column was found AND the row has a value in it — a file missing one or
    both size columns simply never sets the corresponding key, which is what
    _ensure_table_sizes() below checks for before falling back to DB02."""
    workbook = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    sheet = workbook.active

    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    field_index: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        if not cell:
            continue
        field = _HEADER_ALIASES.get(_normalize_header(str(cell)))
        if field and field not in field_index:
            field_index[field] = i

    name_idx = field_index.get("table_name", 0)
    desc_idx = field_index.get("description", 1)
    volume_gb_idx = field_index.get("volume_gb")
    volume_mb_idx = field_index.get("volume_mb")

    tables = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if not row or name_idx >= len(row) or not row[name_idx]:
            continue
        name = str(row[name_idx]).strip()
        description = str(row[desc_idx]).strip() if desc_idx < len(row) and row[desc_idx] else ""

        entry = {"table_name": name, "description": description}
        if volume_gb_idx is not None and volume_gb_idx < len(row) and row[volume_gb_idx] not in (None, ""):
            entry["volume_gb"] = _format_volume(row[volume_gb_idx])
        if volume_mb_idx is not None and volume_mb_idx < len(row) and row[volume_mb_idx] not in (None, ""):
            entry["volume_mb"] = _format_volume(row[volume_mb_idx])
        tables.append(entry)
    return tables


def _format_volume(value) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value).strip()


def _optional_size_columns(rows: list[dict]) -> list[str]:
    """Include "Volume (GB)"/"Volume (MB)" in an export only if the rows
    actually carry them -- e.g. DB02's top-tables result always has both,
    but a plain saved table list might not."""
    if not rows:
        return []
    return [col for col in ("Volume (GB)", "Volume (MB)") if col in rows[0]]


_ARCHIVING_OBJECT_HEADER_ALIASES = {"archivingobject", "archobject", "archiveobject"}


def _parse_top_archiving_objects(contents: bytes, limit: int = 20) -> list[str]:
    """Read distinct, non-blank "Archiving Object" values from *contents*'s
    LAST sheet, in row order, capped at *limit*. Matches the "Grouped by
    Object" sheet's shape (see grouping.py) -- already sorted by descending
    cumulative size, so the first N distinct values are exactly the top N
    archiving objects. Housekeeping-program-only and "nothing found" rows
    are naturally skipped since their Archiving Object cell is blank."""
    workbook = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    sheet = workbook.worksheets[-1]

    header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True), ())
    archiving_object_idx = None
    for i, cell in enumerate(header_row):
        if cell and _normalize_header(str(cell)) in _ARCHIVING_OBJECT_HEADER_ALIASES:
            archiving_object_idx = i
            break

    if archiving_object_idx is None:
        return []

    seen: set[str] = set()
    objects: list[str] = []
    for row in sheet.iter_rows(min_row=2, values_only=True):
        if len(objects) >= limit:
            break
        if not row or archiving_object_idx >= len(row) or not row[archiving_object_idx]:
            continue
        value = str(row[archiving_object_idx]).strip().upper()
        if value and value not in seen:
            seen.add(value)
            objects.append(value)

    return objects


def _ensure_table_sizes(tables: list[dict]) -> list[dict]:
    """If any table in *tables* is missing "volume_gb" and/or "volume_mb"
    (the uploaded file had no size column(s) at all, or left some cells
    blank), backfill just the missing metric(s) by querying DB02's SQL
    Editor for those tables. A metric a row already carries from the
    uploaded file is never overwritten. A table DB02 has no size for is left
    with an empty string rather than treated as an error — visibility on
    *which* tables are missing a size is more useful than failing the whole
    batch over it."""
    missing = [
        t["table_name"] for t in tables
        if not t.get("volume_gb") or not t.get("volume_mb")
    ]
    if not missing:
        return tables

    result = db02.run_get_table_sizes(missing)
    if result.get("status") != "ok":
        logger.warning("DB02 table-size backfill failed: %s", result.get("message"))
        sizes = {}
    else:
        sizes = result.get("sizes", {})

    for t in tables:
        fetched = sizes.get(t["table_name"].strip().upper(), {})
        if not t.get("volume_gb"):
            t["volume_gb"] = fetched.get("volume_gb", "")
        if not t.get("volume_mb"):
            t["volume_mb"] = fetched.get("volume_mb", "")
    return tables


def _run_db15_batch_job(tables: list[dict]):
    try:
        _db15_batch_progress.update(0, "Checking table sizes via DB02…")
        tables = _ensure_table_sizes(tables)
        result = db15.run_batch(tables, on_progress=_db15_batch_progress.update)
        if result["status"] == "error":
            _db15_batch_progress.fail(result["message"])
        else:
            try:
                object_descriptions.remember_from_rows(result.get("rows", []))
            except Exception:
                logger.warning("Could not remember archiving object descriptions", exc_info=True)
            _db15_batch_progress.finish(result)
    except Exception as exc:
        logger.exception("DB15 batch job failed")
        _db15_batch_progress.fail(str(exc))


def _build_workbook(rows: list[dict], columns: list[str], sheet_title: str) -> io.BytesIO:
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = sheet_title

    sheet.append(columns)
    for row in rows:
        sheet.append([row.get(col, "") for col in columns])

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _build_multi_sheet_workbook(sheets: list[tuple]) -> io.BytesIO:
    """Build a workbook with one sheet per (title, columns, rows) or (title,
    columns, rows, merge_columns) tuple. Numeric-looking cell values (e.g.
    Score) are written as real numbers so they sort/filter correctly in
    Excel, instead of as text.

    When *merge_columns* is given, vertically-adjacent cells in those
    columns are merged (and centered) wherever every column listed shares
    the same value across consecutive rows -- used by the "Grouped by
    Object" sheet so a group's Archiving Object/Housekeeping
    Program/cumulative-size values appear once instead of repeated on every
    member row."""
    workbook = openpyxl.Workbook()
    workbook.remove(workbook.active)

    for sheet_spec in sheets:
        title, columns, rows = sheet_spec[0], sheet_spec[1], sheet_spec[2]
        merge_columns = sheet_spec[3] if len(sheet_spec) > 3 else None

        sheet = workbook.create_sheet(title)
        sheet.append(columns)
        for row in rows:
            sheet.append([_numeric_or_raw(row.get(col, "")) for col in columns])

        if merge_columns:
            _merge_repeated_rows(sheet, columns, rows, merge_columns)

    buffer = io.BytesIO()
    workbook.save(buffer)
    buffer.seek(0)
    return buffer


def _merge_repeated_rows(sheet, columns: list[str], rows: list[dict], merge_columns: list[str]):
    """Vertically merge each run of consecutive data rows that share the
    same value across every column in *merge_columns*, left-aligning and
    vertically centering the surviving value in the merged range. Assumes
    row 1 is the header and data starts at row 2, matching how
    _build_multi_sheet_workbook writes the sheet just above.

    This alignment is applied to *every* row in these columns, not just the
    ones that end up merged -- a singleton group (nothing to merge) would
    otherwise keep Excel's default top alignment, which looks inconsistent
    next to a merged multi-row group's vertically-centered text."""
    col_index = {name: i + 1 for i, name in enumerate(columns)}  # openpyxl columns are 1-based
    merge_col_numbers = [col_index[c] for c in merge_columns if c in col_index]
    if not merge_col_numbers or not rows:
        return

    alignment = Alignment(horizontal="left", vertical="center")
    for row_idx in range(len(rows)):
        excel_row = row_idx + 2  # +1 for header row, +1 for 1-based
        for col in merge_col_numbers:
            sheet.cell(row=excel_row, column=col).alignment = alignment

    def _key(row: dict) -> tuple:
        return tuple(row.get(c, "") for c in merge_columns)

    run_start = 0  # index into rows (0-based)
    for i in range(1, len(rows) + 1):
        if i == len(rows) or _key(rows[i]) != _key(rows[run_start]):
            if i - run_start > 1:
                start_excel_row = run_start + 2
                end_excel_row = i + 1
                for col in merge_col_numbers:
                    sheet.merge_cells(start_row=start_excel_row, start_column=col, end_row=end_excel_row, end_column=col)
            run_start = i


def _numeric_or_raw(value):
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value)
        except ValueError:
            try:
                return float(value)
            except ValueError:
                pass
    return value


if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT

    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=False)
