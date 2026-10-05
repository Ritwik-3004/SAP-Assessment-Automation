"""
Table analysis task: TAANA ad hoc analyses for the header tables of a header-table sheet.

For each table, in order:
  1. read the fields TAANA offers (transactions/taana_analysis.py) and the date/year-like ones
     (ABAP dictionary types, looked up once for all tables);
  2. ask the user which DATE/YEAR/MONTH fields to analyse (and whether to group dates by year);
  3. ask whether other fields (company code, document type, ...) should be added; if yes, ask which;
  4. create the ad hoc variant, schedule the analysis in the background (start immediately);
  5. wait until it has completed, copy the result grid into  output/Table analysis.xlsx, and move on.

The job runs on a background thread and talks to the UI through a small state object: the UI polls
snapshot() and answers the current question through answer(). Questions are asked between SAP steps, so
SAP is not held while the user thinks.
"""

import io
import logging
import re
import threading
import time
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment, Font
from openpyxl.utils import get_column_letter

from config import OUTPUT_DIR
import transactions.db02 as db02
import transactions.taana_analysis as taana

logger = logging.getLogger(__name__)

OUTPUT_NAME = "Table analysis.xlsx"
POLL_SECONDS = 10.0          # between checks of a running background job
FIRST_POLL_SECONDS = 4.0
MAX_WAIT_SECONDS = 4 * 3600  # give up on one table's job after this long (the user can also Skip)
MAX_CHECK_ERRORS = 3

# Fields worth suggesting first when the user adds "other" fields.
SUGGESTED_FIELDS = {
    "BUKRS", "BLART", "MONAT", "KOKRS", "WERKS", "VKORG", "VTWEG", "SPART", "EKORG", "EKGRP",
    "LGORT", "OBJECTCLAS", "TCODE", "USNAM", "ERNAM", "BSTAT", "VBTYP", "AUART", "BSART", "LAND1",
}
EXCLUDED_OTHER = {"MANDT"}

_TABLE_NAME = re.compile(r"^[A-Z0-9_/]{2,30}$")
_YEAR_NAME = re.compile(r"(GJAHR|JAHR|YEAR|STJAH|FYEAR)")
_YEAR_LABEL = re.compile(r"\b(year|yr|jahr)\b", re.IGNORECASE)
_MONTH_NAME = re.compile(r"(MONAT|MONTH|MON$|^MON|_MON|SPBUP|POPER|BUPER|PERIO|PERID|PERBL|PERAB|PERBI)")
_MONTH_LABEL = re.compile(r"\b(month|monat|period|periode)\b", re.IGNORECASE)


class _Skipped(Exception):
    pass


class _Cancelled(Exception):
    pass


# ---------------------------------------------------------------------------
# Input: the header tables
# ---------------------------------------------------------------------------

def _norm(text) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def parse_tables(contents: bytes) -> list[str]:
    """Distinct header tables from a header-table workbook, in sheet order.

    Uses the LAST sheet that has a "Header Table" column (for the reference-reviewed workbook that is the
    final list after the user's overrides). Falls back to a "Table Name"/"Table" column. Blank cells and
    names that are not valid table names are skipped. Raises ValueError with a readable message."""
    workbook = openpyxl.load_workbook(io.BytesIO(contents), read_only=True, data_only=True)
    try:
        chosen: Optional[tuple] = None
        for wanted in (("headertable",), ("tablename", "table")):
            for ws in workbook.worksheets:
                header = next(ws.iter_rows(min_row=1, max_row=1, values_only=True), None)
                if not header:
                    continue
                for idx, cell in enumerate(header):
                    if _norm(cell) in wanted:
                        chosen = (ws, idx)
                        break
            if chosen:
                break
        if not chosen:
            raise ValueError(
                "No 'Header Table' column found. Use the file saved by Find Header Tables "
                "(header_tables.xlsx / header_tables_with_reference.xlsx)."
            )
        ws, idx = chosen
        tables: list[str] = []
        for row in ws.iter_rows(min_row=2, values_only=True):
            value = str(row[idx] if idx < len(row) and row[idx] is not None else "").strip().upper()
            if value and _TABLE_NAME.match(value) and value not in tables:
                tables.append(value)
        if not tables:
            raise ValueError("The 'Header Table' column has no table names.")
        return tables
    finally:
        workbook.close()


# ---------------------------------------------------------------------------
# Which fields are dates / years
# ---------------------------------------------------------------------------

def _year_like(name: str, label: str) -> bool:
    return bool(_YEAR_NAME.search(name) or _YEAR_LABEL.search(label or ""))


def _month_like(name: str, label: str) -> bool:
    return bool(_MONTH_NAME.search(name) or _MONTH_LABEL.search(label or ""))


def classify_fields(fields: list[dict], types: dict, types_known: bool) -> tuple[list[dict], list[dict]]:
    """Split TAANA's fields into (date/year/month fields, other fields).

    *types* is {FIELD: {"type", "length"}} from the ABAP dictionary for the date-like candidates of this
    table. Kinds: "date" (DATS), "period" (YYYYMM: ACCP, or a 6-character field named like a period),
    "year" (4-digit NUMC named/labelled as a year), "month" (a 2/3-character month or posting-period field
    such as MONAT or POPER). Dates and periods can be grouped by year; a year or month field already is
    one. If the dictionary lookup failed (*types_known* False) names and labels are used instead, so the
    task still works."""
    date_fields: list[dict] = []
    other_fields: list[dict] = []
    for f in fields:
        name, label = f["name"].upper(), f.get("label", "")
        kind = None
        info = types.get(name)
        if info:
            if info["type"] == "DATS":
                kind = "date"
            elif info["type"] == "ACCP":
                kind = "period"
            elif info["type"] in ("NUMC", "CHAR") and info["length"] == 4 and _year_like(name, label):
                kind = "year"
            elif info["type"] in ("NUMC", "CHAR") and info["length"] == 6 and _month_like(name, label):
                kind = "period"
            elif info["type"] in ("NUMC", "CHAR") and info["length"] in (2, 3) and _month_like(name, label):
                kind = "month"
        elif not types_known:
            lowered = label.lower()
            if _year_like(name, label):
                kind = "year"
            elif _month_like(name, label):
                kind = "month"
            elif name.endswith("DAT") or "DATE" in name or "DATUM" in name or re.search(r"\bdate\b", lowered):
                kind = "date"
        if kind:
            date_fields.append({"name": name, "label": label, "kind": kind})
        elif name not in EXCLUDED_OTHER:
            other_fields.append({"name": name, "label": label, "suggested": name in SUGGESTED_FIELDS})
    return date_fields, other_fields


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------

def _clock(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


class TableAnalysisJob:
    def __init__(self):
        self._lock = threading.RLock()
        self._answered = threading.Event()
        self._reset()

    def _reset(self) -> None:
        self._state = {
            "status": "idle",        # idle | running | waiting (for the user) | done | error
            "total": 0,
            "completed": 0,
            "message": None,
            "current": None,         # {"table": ..., "step": ...}
            "prompt": None,          # the open question, if status == "waiting"
            "records": [],           # one summary entry per table
            "output_path": None,
            "warnings": [],
        }
        self._results: list[dict] = []   # full result grids, for the workbook
        self._answer: Optional[dict] = None
        self._prompt_seq = 0
        self._cancel = False
        self._skip = False
        self._answered.clear()

    # -- public API -----------------------------------------------------------

    def snapshot(self) -> dict:
        with self._lock:
            state = dict(self._state)
            state["records"] = [dict(r) for r in self._state["records"]]
            state["warnings"] = list(self._state["warnings"])
            return state

    def is_active(self) -> bool:
        with self._lock:
            return self._state["status"] in ("running", "waiting")

    def start(self, tables: list[str]) -> None:
        with self._lock:
            if self._state["status"] in ("running", "waiting"):
                raise RuntimeError("A table analysis is already running.")
            self._reset()
            self._state.update(status="running", total=len(tables), message="Starting…")
        threading.Thread(target=self._run, args=(tables,), daemon=True, name="table-analysis").start()

    def answer(self, prompt_id: int, answer: dict) -> bool:
        with self._lock:
            prompt = self._state["prompt"]
            if not prompt or prompt["id"] != prompt_id:
                return False
            self._answer = answer
        self._answered.set()
        return True

    def skip(self) -> None:
        """Skip the table being worked on (the SAP background job, if already scheduled, keeps running)."""
        with self._lock:
            self._skip = True

    def cancel(self) -> None:
        with self._lock:
            self._cancel = True

    # -- plumbing -------------------------------------------------------------

    def _set(self, **changes) -> None:
        with self._lock:
            self._state.update(changes)

    def _step(self, table: str, step: str, message: Optional[str] = None) -> None:
        self._set(current={"table": table, "step": step}, message=message or step)

    def _check_flags(self) -> None:
        with self._lock:
            if self._cancel:
                raise _Cancelled()
            if self._skip:
                self._skip = False
                raise _Skipped("Skipped by the user.")

    def _ask(self, kind: str, payload: dict) -> dict:
        """Put a question to the user and block (SAP is not held) until they answer."""
        with self._lock:
            self._prompt_seq += 1
            self._answer = None
            self._answered.clear()
            self._state["prompt"] = {"id": self._prompt_seq, "kind": kind, **payload}
            self._state["status"] = "waiting"
        try:
            while not self._answered.wait(0.4):
                self._check_flags()
            with self._lock:
                return self._answer or {}
        finally:
            with self._lock:
                self._state["prompt"] = None
                if self._state["status"] == "waiting":
                    self._state["status"] = "running"

    # -- the work -------------------------------------------------------------

    def _run(self, tables: list[str]) -> None:
        try:
            self._set(message="Reading the date/year/month fields of all tables from the ABAP dictionary…")
            lookup = db02.run_get_field_types(tables)
            types_known = lookup.get("status") == "ok"
            all_types = lookup.get("types", {}) if types_known else {}
            if not types_known:
                with self._lock:
                    self._state["warnings"].append(
                        "Could not read field types from the ABAP dictionary "
                        f"({lookup.get('message', 'unknown error')}); date/year/month fields are guessed from their names."
                    )

            for number, table in enumerate(tables, start=1):
                with self._lock:
                    if self._cancel:
                        break
                    self._skip = False
                try:
                    record = self._process(table, all_types.get(table, {}), types_known)
                except _Skipped as skipped:
                    record = {"table": table, "state": "skipped", "fields": [], "rows": 0, "note": str(skipped)}
                except _Cancelled:
                    break
                except Exception as exc:
                    logger.exception("Table analysis failed for %s", table)
                    record = {"table": table, "state": "failed", "fields": [], "rows": 0, "note": str(exc)}
                with self._lock:
                    self._state["records"].append(record)
                    self._state["completed"] = number
                try:
                    self._write_workbook()
                except Exception as exc:
                    logger.exception("Could not write the Table analysis workbook")
                    with self._lock:
                        self._state["warnings"].append(f"Could not save the Excel file: {exc}")

            with self._lock:
                self._state["status"] = "done"
                self._state["current"] = None
                self._state["message"] = "Cancelled." if self._cancel else "Finished."
        except Exception as exc:
            logger.exception("Table analysis job failed")
            with self._lock:
                self._state["status"] = "error"
                self._state["message"] = str(exc)

    def _process(self, table: str, types: dict, types_known: bool) -> dict:
        self._step(table, "Reading the table's fields in TAANA…")
        listed = taana.list_fields(table)
        if listed["status"] != "ok":
            raise RuntimeError(listed["message"])
        date_fields, other_fields = classify_fields(listed["fields"], types, types_known)

        specs: list[dict] = []
        if date_fields:
            self._step(table, "Waiting for you to choose the date/year/month fields", "Choose the date/year/month fields.")
            answer = self._ask("date_fields", {
                "table": table,
                "fields": date_fields,
                "can_group_by_year": any(f["kind"] in ("date", "period") for f in date_fields),
            })
            if answer.get("skip"):
                raise _Skipped("Skipped by the user.")
            by_name = {f["name"]: f for f in date_fields}
            group = bool(answer.get("group_by_year"))
            for name in answer.get("selected", []):
                f = by_name.get(str(name).upper())
                if f:
                    specs.append({"name": f["name"], "year": group and f["kind"] in ("date", "period")})

        self._step(table, "Waiting for you: add other fields?", "Add other fields?")
        more = self._ask("more_fields", {
            "table": table,
            "no_date_fields": not date_fields,
            "selected": [s["name"] for s in specs],
            "has_other_fields": bool(other_fields),
        })
        if more.get("skip"):
            raise _Skipped("Skipped by the user.")
        if more.get("add") and other_fields:
            self._step(table, "Waiting for you to choose the other fields", "Choose the other fields.")
            picked = self._ask("other_fields", {"table": table, "fields": other_fields})
            if picked.get("skip"):
                raise _Skipped("Skipped by the user.")
            valid = {f["name"] for f in other_fields}
            specs += [{"name": str(n).upper(), "year": False} for n in picked.get("selected", []) if str(n).upper() in valid]

        if not specs:
            raise _Skipped("No fields were selected.")

        self._step(table, "Creating the ad hoc variant and starting the background job in SAP…")
        started = taana.start_analysis(table, specs)
        if started["status"] != "ok":
            raise RuntimeError(started["message"])
        result = self._wait_for_result(table, started["before"])

        field_labels = [s["name"] + ("(year)" if s["year"] else "") for s in specs]
        self._results.append({
            "table": table,
            "fields": field_labels,
            "started": result.get("started", ""),
            "status": result.get("status", ""),
            "columns": result["columns"],
            "column_ids": result["column_ids"],
            "rows": result["rows"],
        })
        return {
            "table": table, "state": "done", "fields": field_labels,
            "rows": len(result["rows"]), "note": f"Analysis {result.get('status', 'completed')}.",
        }

    def _wait_for_result(self, table: str, before: list[str]) -> dict:
        began = time.monotonic()
        errors = 0
        time.sleep(FIRST_POLL_SECONDS)
        while True:
            elapsed = time.monotonic() - began
            self._check_flags()
            result = taana.check_analysis(table, before)
            state = result.get("state")
            if state == "completed":
                return result
            if state == "failed":
                raise RuntimeError(f"The SAP analysis job ended with status '{result.get('status')}'.")
            errors = errors + 1 if state == "error" else 0
            if errors >= MAX_CHECK_ERRORS:
                raise RuntimeError(f"Could not read the analysis in TAANA: {result.get('status')}")
            if elapsed > MAX_WAIT_SECONDS:
                raise RuntimeError(f"The analysis did not finish within {_clock(MAX_WAIT_SECONDS)}.")
            self._step(
                table,
                "Waiting for the background job in SAP",
                f"Waiting for the SAP job for {table} ({_clock(elapsed)}): {result.get('status', '')}",
            )
            slept = 0.0
            while slept < POLL_SECONDS:
                self._check_flags()
                time.sleep(0.5)
                slept += 0.5

    # -- output ---------------------------------------------------------------

    def _write_workbook(self) -> None:
        wb = openpyxl.Workbook()
        summary = wb.active
        summary.title = "Summary"
        summary.append(["Table", "Result", "Fields analysed", "Result rows", "Analysis started", "Note"])
        started_by_table = {r["table"]: r["started"] for r in self._results}
        with self._lock:
            records = [dict(r) for r in self._state["records"]]
        for r in records:
            summary.append([
                r["table"], r["state"].capitalize(), ", ".join(r["fields"]), r["rows"],
                started_by_table.get(r["table"], ""), r["note"],
            ])
        self._style_header(summary, 1)
        self._autosize(summary)

        used = {"Summary"}
        for res in self._results:
            ws = wb.create_sheet(self._sheet_name(res["table"], used))
            ws.append([f"Table {res['table']}"])
            ws["A1"].font = Font(bold=True, size=13)
            ws.append([f"Fields analysed: {', '.join(res['fields'])}"])
            ws.append([f"Analysis started: {res['started']}    Status: {res['status']}"])
            ws.append([])
            ws.append(res["columns"])
            self._style_header(ws, 5)
            count_idx = res["column_ids"].index("_ENTRY_CNT") if "_ENTRY_CNT" in res["column_ids"] else len(res["columns"]) - 1
            for row in res["rows"]:
                out = list(row)
                digits = "".join(ch for ch in str(out[count_idx]) if ch.isdigit())
                if digits:
                    out[count_idx] = int(digits)
                # SAP ends the grid with a total line whose field cells are blank
                if digits and not any(str(v).strip() for i, v in enumerate(out) if i != count_idx):
                    out[0] = "Total"
                ws.append(out)
            ws.freeze_panes = "A6"
            self._autosize(ws, skip_rows=4)

        path = self._save(wb)
        self._set(output_path=str(path))

    @staticmethod
    def _sheet_name(table: str, used: set) -> str:
        base = re.sub(r"[\\/*?:\[\]]", "_", table)[:31] or "Table"
        name, n = base, 2
        while name in used:
            name = f"{base[:28]}_{n}"
            n += 1
        used.add(name)
        return name

    @staticmethod
    def _style_header(ws, row: int) -> None:
        for cell in ws[row]:
            cell.font = Font(bold=True)
            cell.alignment = Alignment(vertical="center")

    @staticmethod
    def _autosize(ws, skip_rows: int = 0) -> None:
        widths: dict[int, int] = {}
        for r_idx, row in enumerate(ws.iter_rows(values_only=True), start=1):
            if r_idx <= skip_rows:
                continue
            for c_idx, value in enumerate(row, start=1):
                widths[c_idx] = max(widths.get(c_idx, 8), min(len(str(value)) + 2 if value is not None else 0, 60))
        for c_idx, width in widths.items():
            ws.column_dimensions[get_column_letter(c_idx)].width = width

    @staticmethod
    def _save(wb) -> Path:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        candidates = [OUTPUT_DIR / OUTPUT_NAME] + [
            OUTPUT_DIR / f"{Path(OUTPUT_NAME).stem} ({n}){Path(OUTPUT_NAME).suffix}" for n in range(2, 10)
        ]
        for path in candidates:
            try:
                wb.save(path)
                return path
            except PermissionError:  # open in Excel
                continue
        raise PermissionError("Could not write the Table analysis workbook (is it open in Excel?).")


job = TableAnalysisJob()
