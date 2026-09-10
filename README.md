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

## Transactions

| Transaction | Purpose |
|---|---|
| **TAANA** | Database table analysis — row counts, sizes, archivability flags |
| **DB15** | Find which archiving objects reference a given table |
| **SE16N** | Browse table contents with optional WHERE filter |
| **SE11** | ABAP Dictionary — view table field definitions and data types |
| **AOBJ** | List all archiving objects with customizing settings |
| **SARA** | Archive Administration — view sessions and statistics for an archiving object |

Each transaction can be run one-off from its own panel in the sidebar (single table/object at a time).

## Batch Archiving Analysis (Excel upload)

The **Batch Archiving Analysis** tab (`frontend/src/components/BatchArchivingPanel.tsx`) automates DB15 across a whole list of tables in one go:

1. Upload an `.xlsx`/`.xls` file with a header row, table name in column A, and an optional description in column B.
2. Pick a transaction from the dropdown (currently only **DB15** is implemented — the other five are listed but disabled until they get the same treatment) and click Submit.
3. The backend navigates to DB15 once, selects the **Archiving Objects** radio button, then for each table types it into **Objects for Table**, presses Enter, and reads the resulting archiving-object grid.
4. Results (Table Name, Table Description, Archiving Object, Object Description) are shown on screen and can be downloaded as a single `.xlsx` via **Export to Excel**.

Backend implementation: `db15.run_batch()` in `backend/transactions/db15.py`, plus `/api/transactions/db15/batch` and `/api/transactions/db15/export` in `backend/main.py` (see [API Reference](#api-reference)).

## Generate Table List (DB02)

Don't have a starting list of tables yet? The **Generate Table List (DB02)** tab (`frontend/src/components/GenerateTableListPanel.tsx`) automates DB02/DBACOCKPIT's SQL Editor to build one:

1. Pick "Top N tables" (default 300) and click **Generate List**.
2. The backend navigates to DB02, opens the **Diagnostics → SQL Editor** tree node, pastes in a canned HANA SQL query that lists the system's largest tables (by memory size) with their descriptions, presses F8 (Execute), switches to the **Result** tab, and reads the grid.
3. The first 20 rows are shown as a preview; **Download Excel** exports the full list.

The exported file's columns (Table Name in column A, Description in column B) intentionally match what the **Batch Archiving Analysis** upload expects, so the downloaded file can be fed straight into that tool without any edits.

Backend implementation: `db02.run_get_top_tables()` in `backend/transactions/db02.py`, plus `/api/transactions/db02/top-tables` and `/api/transactions/db02/export` in `backend/main.py`.

## Adjusting Screen Element IDs

SAP GUI element IDs (e.g. `wnd[0]/usr/ctxtP_TNAME`) can differ across SAP versions and screen variants. Two ways to find the correct ones for your system:

**Option A — SAP GUI script recording:**
1. Open SAP GUI manually and navigate to the transaction
2. Go to **Help → Scripting → Record Script**
3. Perform the actions (fill fields, press Execute)
4. Stop recording and open the generated `.vbs` file
5. Copy the correct element IDs into the corresponding file in `backend/transactions/`

**Option B — built-in diagnostic endpoints (DB15 only, for now):** while connected to SAP, call these directly (browser, curl, or PowerShell's `Invoke-RestMethod`) instead of recording a script:

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
| POST | `/api/transactions/db15/batch` | Upload an Excel file of tables and run DB15 against each one |
| POST | `/api/transactions/db15/export` | Export batch DB15 results (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db15/debug-screen` | Diagnostic: dump DB15 selection-screen elements |
| GET | `/api/transactions/db15/debug-grid` | Diagnostic: filter DB15 by a table and report grid-read details |
| POST | `/api/transactions/db02/top-tables` | Run the "top tables by size" SQL query via DB02's SQL Editor |
| POST | `/api/transactions/db02/export` | Export the top-tables result (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db02/debug-screen` | Diagnostic: dump DB02 screen elements under a given container |
| GET | `/api/transactions/db02/debug-tree` | Diagnostic: dump a tree control's node keys + display text |
| POST | `/api/transactions/db02/debug-click` | Diagnostic: perform an action on a tree node and dump the result |
| GET | `/api/transactions/db02/debug-probe-run` | Diagnostic: dry-run the whole SQL Editor flow step by step |
