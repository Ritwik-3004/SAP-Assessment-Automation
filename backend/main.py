"""
SAP Assessment Automation — FastAPI backend.

Start with:
    uvicorn main:app --host 127.0.0.1 --port 8000 --reload
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from sap_connector import sap
import transactions.taana as taana
import transactions.db15 as db15
import transactions.se16n as se16n
import transactions.se11 as se11
import transactions.aobj as aobj
import transactions.sara as sara

logging.basicConfig(level=logging.INFO, format="%(levelname)s | %(name)s | %(message)s")

app = FastAPI(
    title="SAP Assessment Automation API",
    version="1.0.0",
    description="Backend for SAP archivability analysis via GUI scripting.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
# Helpers
# ---------------------------------------------------------------------------

def _require_connection():
    if not sap.is_connected:
        raise HTTPException(
            status_code=403,
            detail="Not connected to SAP. POST /api/sap/connect first.",
        )


if __name__ == "__main__":
    import uvicorn
    from config import API_HOST, API_PORT

    uvicorn.run("main:app", host=API_HOST, port=API_PORT, reload=True)
