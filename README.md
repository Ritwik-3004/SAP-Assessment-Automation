# SAP Assessment Automation

A desktop-local web application for SAP archivability analysis. A Python/FastAPI backend drives SAP GUI via Windows COM scripting; a React frontend provides the UI.

## Architecture

```
Browser (React + Vite)  ←→  FastAPI (Python)  ←→  SAP GUI (COM scripting)  ←→  SAP System
    localhost:5173              localhost:8000          win32com.client
```

## Prerequisites

| Requirement | Notes |
|---|---|
| SAP GUI for Windows | Must be installed (standard SAP Logon) |
| SAP GUI Scripting enabled | Options → Accessibility & Scripting → Scripting tab → **Enable Scripting** |
| Python 3.11+ | `python --version` |
| Node.js 18+ | `node --version` |

### Enable SAP GUI Scripting

1. Open SAP Logon
2. Go to **Customize Local Layout** (Alt+F12) → **Options**
3. Navigate to **Accessibility & Scripting** → **Scripting**
4. Check **Enable Scripting**
5. Optionally uncheck **Notify when a script attaches** (avoids popups during automation)

## Quick Start

Open **two** terminal windows:

**Terminal 1 — Backend:**
```bat
start_backend.bat
```

**Terminal 2 — Frontend:**
```bat
start_frontend.bat
```

Then open `http://localhost:5173` in your browser.

## Manual Setup

### Backend
```powershell
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

### Frontend
```powershell
cd frontend
npm install
npm run dev
```

## Design principle: no tcodes in the UI

The sidebar and login screen deliberately never show a transaction code or ask the
user to pick one — end users doing an archivability assessment shouldn't need to know
which SAP tcode does the work (they could just as easily run it by hand in SAP GUI if
that were the point). Each sidebar item names the *task* ("Generate Table List", "Find
Archiving Objects for Tables"); the transaction(s) behind it are an implementation
detail documented here for developers, not exposed in the app itself.

## Backend transaction modules

Each of the following has its own `backend/transactions/*.py` module and single-item
FastAPI endpoint (see [API Reference](#api-reference)), usable directly (e.g. via
`/docs` or curl) for development/debugging, but **none of them has a dedicated frontend
panel** — only the task-oriented tools below (Find Archiving Objects for Tables,
Generate Table List) are exposed in the UI.

| Transaction | Purpose |
|---|---|
| **TAANA** | Database table analysis — row counts, sizes, archivability flags |
| **DB15** | Find which archiving objects reference a given table |
| **DB02** | HANA database administration cockpit (used here for its SQL Editor) |
| **SE16N** | Browse table contents with optional WHERE filter |
| **SE11** | ABAP Dictionary — view table field definitions and data types |
| **AOBJ** | List all archiving objects with customizing settings |
| **SARA** | Archive Administration — view sessions and statistics for an archiving object |

## Find Archiving Objects for Tables (Excel upload)

The **Find Archiving Objects for Tables** tab (`frontend/src/components/BatchArchivingPanel.tsx`) automates DB15 across a whole list of tables in one go. The tcode itself is an implementation detail — the UI only presents the task ("look up the archiving object(s) for these tables"), by design, so end users don't need to know which transaction does the work:

1. Upload an `.xlsx`/`.xls` file with a header row, table name in column A, and an optional description in column B.
2. Click Submit. A progress bar fills in as each table finishes (e.g. "12 of 40 — BSEG"), since this can take a while on a large list.
3. The backend navigates to DB15 once, selects the **Archiving Objects** radio button, then for each table types it into **Objects for Table**, presses Enter, and reads the resulting archiving-object grid.
4. Results (Table Name, Table Description, Archiving Object, Object Description) are shown on screen and can be downloaded as a single `.xlsx` via **Export to Excel**.

Backend implementation: `db15.run_batch()` in `backend/transactions/db15.py`, plus `/api/transactions/db15/batch` and `/api/transactions/db15/export` in `backend/main.py` (see [API Reference](#api-reference), and [Progress polling](#progress-polling-for-long-running-batches) for how the progress bar works).

### Score & Recommend Objects (Claude API)

A table can have many candidate archiving objects (e.g. CDHDR has dozens) — picking
the right one has always required manual judgment (experience, search, or asking an
LLM by hand). Once a lookup above has results, click **Score & Recommend Objects** to
automate that judgment call:

1. The backend groups the results by table and sends each table's full candidate list
   to Claude (`claude-haiku-4-5`) in one request, asking for a 0–100 relevance score
   and a short rationale for every candidate. Tables with only one candidate skip the
   API call entirely (score is trivially 100). This runs concurrently (~8 tables at a
   time) since a large batch (e.g. 300 tables from the DB02 tool) can mean hundreds of
   calls — a progress bar tracks tables resolved, not individual API calls, so it still
   advances one table at a time even though several are scored in parallel underneath.
2. A preview of the first 20 scored rows renders on screen; **Show Recommended List**
   reveals the highest-scoring object per table (one row per table); **Download Excel
   (Scored)** exports a 2-sheet workbook — "All Scored Objects" (every table/object
   pair with its Score) and "Recommended" (the top pick per table, plus the Rationale).
3. Scores and rationale are the model's best-effort judgment. For a table covered by
   the DVM Guide (see below), the rationale explicitly says so when it draws on it
   (e.g. "Official Data Management Guide recommends BC_E071K..."); otherwise it's the
   model's own recollection, not independently checked.

Requires `ANTHROPIC_API_KEY` in `backend/.env` (see [Configuration](#configuration)).
Backend implementation: `scoring.py` (new top-level module, not under `transactions/`
since it's a post-processing/LLM step, not a SAP transaction), plus
`/api/transactions/db15/score` and `/api/transactions/db15/score-export` in
`backend/main.py`.

#### Grounded in SAP's official DVM Guide

`backend/resources/DVM_Guide.pdf` is SAP's own "Data Management Guide for SAP Business
Suite" (Best-Practice Document) — the same document people already consult by hand to
pick the right archiving object. `backend/dvm_guide.py` parses it once (~225 pages,
one numbered section per table/table-group, e.g. "5.13 E070, E071, E071K: Change and
Transport System", each with an "Archiving" subsection naming the recommended
object(s)) into a `{TABLE_NAME: section_text}` lookup, cached to
`backend/resources/dvm_guide_index.json` (git-ignored — derived data, rebuilt
automatically whenever it's missing or older than the PDF; ~7s cold, instant after).
For every table being scored, `scoring.py` looks up that table's excerpt and includes
it in the prompt, with an explicit instruction to treat it as authoritative when it
names a specific object. Confirmed live: not every table is covered (the guide indexes
~207 distinct table names), so scoring gracefully falls back to the model's general
knowledge for anything the guide doesn't mention.

If you replace `DVM_Guide.pdf` with a different or updated version, just delete
`dvm_guide_index.json` (or touch the PDF) — the index rebuilds on the next scoring run.

**Token-budget gotcha (found while testing against a real table with ~20+
candidates):** each table's candidates are scored in a single structured-output
response, so the response's required length grows with the candidate count. An
initial flat `max_tokens` (later a per-candidate estimate capped too low) silently
truncated the JSON for large tables, which failed to parse and — because of a second
bug in the recommended-list aggregation — caused the whole table to disappear from the
Recommended sheet with no visible error. Both are fixed: `max_tokens` scales with
candidate count up to 16,000 (Haiku 4.5's ceiling is 64,000; 16,000 is the point past
which non-streaming responses risk HTTP timeouts, and comfortably covers even a
~60-candidate table), the rationale prompt asks for one short sentence per candidate to
keep actual usage well under that, and a table that still fails to score now shows up
in both sheets with an explicit "Scoring failed: ..." message instead of vanishing.

## Progress polling for long-running batches

DB15 lookup and Scoring can take minutes on a large batch (e.g. 300 tables from the
DB02 tool), so both moved from "block the HTTP request until the whole batch
finishes" to a start-then-poll pattern:

1. `POST /api/transactions/db15/batch` (or `/db15/score`) validates the input, kicks
   off the real work on a background `threading.Thread`, and returns immediately with
   `{"status": "started", "total": N}`.
2. The frontend polls `GET .../batch/progress` (or `.../score/progress`) every ~700ms
   (`pollUntilDone()` in `frontend/src/api/client.ts`) until `status` is `"done"` or
   `"error"` — each snapshot carries `completed`/`total`/`message` (the table just
   finished), rendered by `frontend/src/components/ProgressBar.tsx`. Once `"done"`,
   the snapshot's `result` field is the exact payload the endpoint used to return
   directly.

`backend/progress.py`'s `ProgressTracker` holds this state — one shared instance per
operation (this app is single-user/local, so no job IDs needed). A second
`POST .../batch` or `.../score` while one is already running gets `409`, not a second
overlapping job. `db15.run_batch()` and `scoring.score_archiving_objects()` both take
an optional `on_progress(completed, message)` callback, called once per table
resolved — for scoring that means once per table as its `ThreadPoolExecutor` future
completes (or immediately for the zero/single-candidate shortcuts), not once per
underlying API call.

`GET /api/transactions/db02/top-tables` is unchanged (still a single blocking
request) — a single SQL query execution has no sub-step to poll, so its panel shows
an indeterminate animated bar (`<ProgressBar mode="indeterminate" />`) instead of a
real percentage.

## Generate Table List (DB02)

Don't have a starting list of tables yet? The **Generate Table List (DB02)** tab (`frontend/src/components/GenerateTableListPanel.tsx`) automates DB02/DBACOCKPIT's SQL Editor to build one:

1. Pick "Top N tables" (default 300) and click **Generate List**. An animated progress bar shows the query is running (no percentage — see [Progress polling](#progress-polling-for-long-running-batches) for why).
2. The backend navigates to DB02, opens the **Diagnostics → SQL Editor** tree node, pastes in a canned HANA SQL query that lists the system's largest tables (by memory size) with their descriptions, presses F8 (Execute), switches to the **Result** tab, and reads the grid.
3. The first 20 rows are shown as a preview; **Download Excel** exports the full list.

The exported file's columns (Table Name in column A, Description in column B) intentionally match what the **Find Archiving Objects for Tables** upload expects, so the downloaded file can be fed straight into that tool without any edits.

Backend implementation: `db02.run_get_top_tables()` in `backend/transactions/db02.py`, plus `/api/transactions/db02/top-tables` and `/api/transactions/db02/export` in `backend/main.py`.

## Adjusting Screen Element IDs

SAP GUI element IDs (e.g. `wnd[0]/usr/ctxtP_TNAME`) can differ across SAP versions and screen variants. Two ways to find the correct ones for your system:

**Option A — SAP GUI script recording:**
1. Open SAP GUI manually and navigate to the transaction
2. Go to **Help → Scripting → Record Script**
3. Perform the actions (fill fields, press Execute)
4. Stop recording and open the generated `.vbs` file
5. Copy the correct element IDs into the corresponding file in `backend/transactions/`

**Option B — built-in diagnostic endpoints (DB15 and DB02 only, for now):** while connected to SAP, call these directly (browser, curl, or PowerShell's `Invoke-RestMethod`) instead of recording a script:

| Endpoint | Purpose |
|---|---|
| `GET /api/transactions/db15/debug-screen` | Navigates to DB15 and lists every element on the selection screen (id, type, name, text) — use this to find the correct radio-button / input-field IDs. |
| `GET /api/transactions/db15/debug-grid?table_name=BKPF` | Filters DB15 by the given table and reports, for each candidate grid path, whether it was found, its row count, column order, and first cell value — with the raw error text if a step fails. |
| `GET /api/transactions/db02/debug-screen?container_id=wnd[0]/usr` | Navigates to DB02 and recursively lists every element under the given container — use this to find tree/control IDs if they differ on your system. |
| `GET /api/transactions/db02/debug-tree?tree_id=...` | Lists every node's key + display text for the tree control at the given ID, to identify real node keys (e.g. for "SQL Editor") without guessing. |
| `POST /api/transactions/db02/debug-click` | Body `{tree_id, node_key, action}` — performs `select`/`expand`/`doubleclick` on a tree node, then dumps the screen afterward so the effect is visible. |
| `GET /api/transactions/db02/debug-probe-run?limit=20` | End-to-end dry run of the SQL Editor flow (open editor, set query text, execute, switch tabs, read the result grid), reporting each step's outcome independently. |

**Known SAP GUI ALV grid gotchas** (discovered while wiring up DB15, likely relevant to the other five transactions too, since they share `sap_connector.py`'s grid-reading helper):
- `grid.GetColumnTitles(col_id)` is not a valid method on every system's ActiveX grid control — calling it can leave the grid object unable to serve subsequent `RowCount`/`GetCellValue` calls, even though the bad call itself is caught. Prefer hardcoding known column IDs (see `DB15_COLUMN_LABELS` in `db15.py`) over relying on that method.
- The grid only renders rows currently scrolled into its visible window — `GetCellValue` on a table with more results than fit in that window (e.g. CDHDR's ~24 archiving objects) silently returns `""` for the rows outside it. Set `grid.FirstVisibleRow = row_idx` before reading each row to force it into view first.

## Configuration

Create a `backend/.env` file to override defaults:

```env
SAPLOGON_EXE=C:\Program Files (x86)\SAP\FrontEnd\SAPgui\saplogon.exe
SAP_SCREEN_WAIT=1.5
API_HOST=127.0.0.1
API_PORT=8000

# Required only for "Score & Recommend Objects" (backend/scoring.py)
ANTHROPIC_API_KEY=sk-ant-...
SCORING_MODEL=claude-haiku-4-5
```

## API Reference

FastAPI auto-generates interactive docs at `http://127.0.0.1:8000/docs`.

Key endpoints:

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Backend + SAP connection status |
| GET | `/api/sap/systems` | List SAP systems from local Logon pad |
| POST | `/api/sap/connect` | Connect and log in |
| POST | `/api/sap/disconnect` | Log out |
| GET | `/api/sap/status` | Whether currently connected |
| POST | `/api/transactions/taana` | Run TAANA |
| POST | `/api/transactions/db15` | Run DB15 for a single table |
| POST | `/api/transactions/se16n` | Run SE16N |
| POST | `/api/transactions/se11` | Run SE11 |
| POST | `/api/transactions/aobj` | Run AOBJ |
| POST | `/api/transactions/sara` | Run SARA |
| POST | `/api/transactions/db15/batch` | Upload an Excel file of tables; starts the DB15 batch lookup in the background and returns `{status: "started", total}` immediately |
| GET | `/api/transactions/db15/batch/progress` | Poll for batch lookup progress; `result` is populated once `status` is `"done"` |
| POST | `/api/transactions/db15/export` | Export batch DB15 results (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db15/debug-screen` | Diagnostic: dump DB15 selection-screen elements |
| GET | `/api/transactions/db15/debug-grid` | Diagnostic: filter DB15 by a table and report grid-read details |
| POST | `/api/transactions/db15/score` | Starts scoring every table/object row for archiving relevance via the Claude API in the background; returns `{status: "started", total}` immediately |
| GET | `/api/transactions/db15/score/progress` | Poll for scoring progress; `result` (rows + recommended) is populated once `status` is `"done"` |
| POST | `/api/transactions/db15/score-export` | Export the scored rows + recommended list (JSON) to a downloadable 2-sheet `.xlsx` |
| POST | `/api/transactions/db02/top-tables` | Run the "top tables by size" SQL query via DB02's SQL Editor |
| POST | `/api/transactions/db02/export` | Export the top-tables result (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db02/debug-screen` | Diagnostic: dump DB02 screen elements under a given container |
| GET | `/api/transactions/db02/debug-tree` | Diagnostic: dump a tree control's node keys + display text |
| POST | `/api/transactions/db02/debug-click` | Diagnostic: perform an action on a tree node and dump the result |
| GET | `/api/transactions/db02/debug-probe-run` | Diagnostic: dry-run the whole SQL Editor flow step by step |
