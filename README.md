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
`/docs` or curl) for development/debugging. Only the task-oriented tools below (Find
Archiving Objects for Tables, Generate Table List, Score & Recommend Objects, Find
Header Tables for Archiving Objects) are exposed in the UI — **SE16N is the one
exception with a real, task-specific automation** (looking up a header table via
`ARCH_DEF`, see [Find Header Tables for Archiving Objects](#find-header-tables-for-archiving-objects)); its generic table-browsing `run()` function, like the other
transactions below, has no dedicated panel.

| Transaction | Purpose |
|---|---|
| **TAANA** | Database table analysis — row counts, sizes, archivability flags |
| **DB15** | Find which archiving objects reference a given table |
| **DB02** | HANA database administration cockpit (used here for its SQL Editor) |
| **SE16N** | Browse table contents with optional WHERE filter; also used for a specific ARCH_DEF header-table lookup (see below) |
| **SE11** | ABAP Dictionary — view table field definitions and data types |
| **AOBJ** | List all archiving objects with customizing settings |
| **SARA** | Archive Administration — view sessions and statistics for an archiving object |

## Input and Output Folders

The backend creates two folders at the project root on startup:

| Folder | Purpose |
|---|---|
| `input/` | Holds table lists (`.xlsx`) used as input to the Find Archiving Objects tool |
| `output/` | Holds saved results exported from Find Archiving Objects, Score & Recommend, and Find Header Tables |

Files are never auto-saved. Every panel shows a **Save to folder** button (writes to the appropriate folder) and a separate **Download** button (downloads to the browser). This keeps folder contents intentional — only files the user explicitly saved land there.

Both folders are listable via the API (`GET /api/files/input`, `GET /api/files/output`) so a panel can offer a file picker instead of requiring an upload every time — **Find Archiving Objects for Tables** and **Find Header Tables for Archiving Objects** both do this, picking the `input/`/`output/` folder respectively.

## Find Archiving Objects for Tables

The **Find Archiving Objects for Tables** tab (`frontend/src/components/BatchArchivingPanel.tsx`) automates DB15 across a whole list of tables in one go. The tcode itself is an implementation detail — the UI only presents the task ("look up the archiving object(s) for these tables"), by design, so end users don't need to know which transaction does the work:

1. **Choose the input file.** The panel lists all `.xlsx`/`.xls` files from the `input/` folder as radio buttons — typically this is the file saved by **Generate Table List**. If you want a different file, click **Upload a different file** to pick one from anywhere on disk; the uploaded filename replaces the radio list until you click **Use input folder** to go back.
2. Click **Submit**. A progress bar fills in as each table finishes (e.g. "12 of 40 — BSEG"), since this can take a while on a large list.
3. The backend navigates to DB15 once, selects the **Archiving Objects** radio button, then for each table types it into **Objects for Table**, presses Enter, and reads the resulting archiving-object grid.
4. Results (Table Name, Table Description, Volume (GB), Volume (MB), Archiving Object, Object Description) are shown on screen (20 rows per page, with ← → navigation).
5. Click **Save to output folder** to write `output/archiving_objects_by_table.xlsx`, or **Download** to get the file directly in the browser.

Backend implementation: `db15.run_batch()` in `backend/transactions/db15.py`, plus `/api/transactions/db15/batch` and `/api/transactions/db15/export` in `backend/main.py` (see [API Reference](#api-reference), and [Progress polling](#progress-polling-for-long-running-batches) for how the progress bar works).

### Table size (Volume GB / MB)

Every result carries the table's size in both GB and MB (MB matters because many small
tables round to `0.00` GB, losing all resolution). `_parse_table_list()` in
`backend/main.py` detects a size column in an uploaded file by header name (`Volume
(GB)`, `Size (GB)`, `Volume (MB)`, and a few other variants — case/spacing
insensitive), so a file re-uploaded from this app's own exports round-trips correctly.
If a size is missing for some or all tables, `_ensure_table_sizes()` backfills just
what's missing via a new HANA query — `db02.run_get_table_sizes()` — against the same
`M_CS_TABLES` system view **Generate Table List** uses, filtered to the specific table
names instead of ordered by size. Both GB and MB are derived in Python from a single
raw-byte-count SQL column rather than two separate `ROUND()` expressions, which also
avoids MB losing resolution when it would otherwise be derived from an already-rounded
GB value.

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
2. A preview of the first 20 scored rows renders on screen with ← → pagination; **Show Recommended List**
   reveals the highest-scoring object per table (one row per table), and **Show Grouped by Object** rolls that up further by shared archiving object/housekeeping program (see below). Click **Save to output folder** to write `output/archiving_objects_scored.xlsx`, or **Download Excel (Scored)** to get the file directly — either exports a 3-sheet workbook: "All Scored Objects" (every table/object pair with its Score), "Recommended" (the top pick per table, plus the Rationale), and "Grouped by Object".
3. Scores and rationale are the model's best-effort judgment. For a table covered by
   the DVM Guide (see below), the rationale explicitly says so when it draws on it
   (e.g. "Official Data Management Guide recommends BC_E071K..."); otherwise it's the
   model's own recollection, not independently checked.
4. **Every table appears in the results, even with no archiving object and no
   housekeeping program found** — never silently dropped. Archiving Object and
   Housekeeping Program are always kept in their own columns (never both populated for
   the same table), and a Rationale explains whichever of the three outcomes applies.

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

#### Housekeeping/cleanup program lookup for tables with no archiving object

Not every table is covered by a formal archiving object — some (log, temporary, or
staging tables) are instead cleaned up via a standalone housekeeping/cleanup program,
report, or transaction. For a table with zero DB15 candidates, `backend/housekeeping.py`
looks for one instead of just leaving the table with nothing:

1. **DVM Guide (implemented):** the same per-table excerpt used to ground
   archiving-object scoring is read by a separate Claude call whose system prompt
   explicitly rules out reporting an archiving-object mention as a housekeeping program
   — confirmed live: table `E071`'s guide excerpt names both archiving object
   `BC_E071K` *and* a separate Unicode-migration deletion report, and the model
   correctly returns only the latter.
2. **SAP for Me portal search (planned, not yet implemented):** `search_sap_for_me()`
   in `housekeeping.py` is a stub that always returns `None` for now, pending SAP for Me
   portal access. Once granted, it should search for `"<table name> housekeeping
   programs"`, scrape the relevant articles, and use an LLM to extract the correct
   program — `find_housekeeping_programs()` already calls it as the fallback after the
   DVM Guide, so no other code needs to change once it's implemented.

A table with neither an archiving object nor a housekeeping program still gets a row in
every result, with both columns blank and a Rationale explaining that nothing was found
(and that the SAP for Me lookup isn't available yet).

#### Grouped by Object

Click **Show Grouped by Object** (or open the "Grouped by Object" sheet in the scored
workbook) to see which tables share the same archiving object or housekeeping program,
and how much data that represents together — useful for prioritizing which
object/program to tackle first. `backend/grouping.py`'s `build_object_groups()`:

- Groups tables by Archiving Object (if set), else Housekeeping Program (if set), else
  a "nothing found" bucket.
- Computes a `Cumulative Size (GB)`/`Cumulative Size (MB)`/`Table Count` per group,
  repeated on every member row (so the sheet stays directly sortable/filterable in
  Excel with no merged-cell ambiguity when read back programmatically).
- Sorts groups by `(cumulative GB, cumulative MB)` descending — MB breaks ties, since GB
  alone is rounded to 2 decimals and different groups can tie on it. The "nothing
  found" bucket is always last, regardless of its own total.
- In the Excel export, repeated group-level cells (Archiving Object, Object
  Description, Housekeeping Program, and the three cumulative-size columns) are
  vertically merged and left-aligned/vertically-centered across their member rows
  (`_merge_repeated_rows()` in `backend/main.py`) — applied uniformly to every row in
  those columns, not just the ones that end up merged, so a singleton group's cell
  looks consistent with a merged multi-row group's instead of falling back to Excel's
  default alignment.

## Progress polling for long-running batches

DB15 lookup, Scoring, and the Header Table batch lookup can all take minutes on a large
batch (e.g. 300 tables from the DB02 tool, or 20 archiving objects each needing a
separate SE16N round trip), so all three moved from "block the HTTP request until the
whole batch finishes" to a start-then-poll pattern:

1. `POST /api/transactions/db15/batch` (or `/db15/score`, or `/header-tables/batch`)
   validates the input, kicks off the real work on a background `threading.Thread`, and
   returns immediately with `{"status": "started", "total": N}`.
2. The frontend polls `GET .../batch/progress` (or `.../score/progress`, or
   `/header-tables/batch/progress`) every ~700ms (`pollUntilDone()` in
   `frontend/src/api/client.ts`) until `status` is `"done"` or `"error"` — each snapshot
   carries `completed`/`total`/`message` (the table/object just finished), rendered by
   `frontend/src/components/ProgressBar.tsx`. Once `"done"`, the snapshot's `result`
   field is the exact payload the endpoint used to return directly.

`backend/progress.py`'s `ProgressTracker` holds this state — one shared instance per
operation (this app is single-user/local, so no job IDs needed). A second
`POST .../batch` or `.../score` while one is already running gets `409`, not a second
overlapping job. `db15.run_batch()`, `scoring.score_archiving_objects()`, and
`se16n.run_batch_find_header_tables()` all take an optional `on_progress(completed,
message)` callback, called once per table/object resolved — for scoring that means once
per table as its `ThreadPoolExecutor` future completes (or immediately for the
zero/single-candidate shortcuts), not once per underlying API call.

`GET /api/transactions/db02/top-tables` is unchanged (still a single blocking
request) — a single SQL query execution has no sub-step to poll, so its panel shows
an indeterminate animated bar (`<ProgressBar mode="indeterminate" />`) instead of a
real percentage.

## Generate Table List (DB02)

Don't have a starting list of tables yet? The **Generate Table List (DB02)** tab (`frontend/src/components/GenerateTableListPanel.tsx`) automates DB02/DBACOCKPIT's SQL Editor to build one:

1. Pick "Top N tables" (default 300) and click **Generate List**. An animated progress bar shows the query is running (no percentage — see [Progress polling](#progress-polling-for-long-running-batches) for why).
2. The backend navigates to DB02, opens the **Diagnostics → SQL Editor** tree node, pastes in a canned HANA SQL query that lists the system's largest tables by total memory size, presses F8 (Execute), switches to the **Result** tab, and reads the grid.
3. Results include **Table Name**, **Description**, **Volume (GB)**, and **Volume (MB)** (both rounded to 2 decimal places; MB is included because many small tables round to `0.00` GB). The first 20 rows are shown as a preview with ← → pagination; the full list is available via save or download.
4. Click **Save to input folder** to write `input/list_of_tables.xlsx` (making it immediately available in the Find Archiving Objects file picker), or **Download Excel** to get the file directly.

The saved file's columns (Table Name in column A, Description in column B) match what the **Find Archiving Objects for Tables** tool expects, so the saved file can be fed straight in without any edits. The Volume (GB)/Volume (MB) columns are included in the saved file but are not required by DB15 — see [Table size (Volume GB / MB)](#table-size-volume-gb--mb) for how they carry through.

Backend implementation: `db02.run_get_top_tables()` in `backend/transactions/db02.py`, plus `/api/transactions/db02/top-tables` and `/api/transactions/db02/export` in `backend/main.py`.

### DB02 navigation robustness

DB02's SQL Editor node is a toggle — double-clicking it when already open closes it, leaving the input shell absent (SAP error 619). The backend guards against this: it navigates to the SAP main menu first (ensuring a clean DB02 start), then checks whether the SQL Editor shell already exists before double-clicking the tree node. This makes repeated runs within the same session reliable regardless of what screen DB02 was left on.

## Find Header Tables for Archiving Objects

Once archiving objects are scored and grouped, the next question for planning an
archiving run is: for each top object, which table is its **header table** — the root
segment in its structure, with no parent? The **Find Header Tables for Archiving
Objects** tab automates this via `SE16N` against the control table `ARCH_DEF`:

1. **Choose the input file.** The panel lists `.xlsx`/`.xls` files from the `output/`
   folder as radio buttons, preferring `archiving_objects_scored.xlsx` if present —
   only its **last sheet** ("Grouped by Object", already sorted by descending
   cumulative size) is read. Upload a different file instead if you want to use one
   with a differently-shaped last sheet; it just needs an "Archiving Object" column.
2. Pick "Top N archiving objects" (default 20) — `_parse_top_archiving_objects()` in
   `backend/main.py` collects the first N *distinct*, non-blank values from that column
   in row order, so housekeeping-program-only and "nothing found" rows are naturally
   skipped.
3. Click **Submit**. For each object, the backend navigates to SE16N, enters
   `ARCH_DEF`, sets the "Arch. Object" filter, executes, and reads the result grid; the
   row whose "Parent Segment" is blank names the header table in its "Segment" column
   (`se16n.find_header_table()`/`run_batch_find_header_tables()` in
   `backend/transactions/se16n.py`) — reusing one SE16N screen across all N objects
   (pressing Back between each) rather than re-navigating from scratch every time. Not
   every archiving object resolves to one (a blank Header Table is a valid result, not
   an error).
4. Click **Save to output folder** to write `output/header_tables.xlsx`, or **Download
   Excel** to get the file directly — a simple 2-column result (Archiving Object,
   Header Table).

### SE16N/ARCH_DEF navigation notes

This was the first UI pattern in the app that's neither a plain dynpro field (like
DB15) nor an ALV grid alone (like DB02's SQL result) — SE16N's per-table "Selection
Criteria" screen is a dynamic `GuiTableControl`, one row per field. Confirmed live
element IDs:

- The table control is `wnd[0]/usr/subTAB_SUB:SAPLSE16N:0121/tblSAPLSE16NSELFIELDS_TC`,
  with cells addressed `findById(f"{base}/<element>[col,row]")` — column 2
  (`ctxtGS_SELFIELDS-LOW`) is the "Frm-Val." input, column 6
  (`txtGS_SELFIELDS-FIELDNAME`) is the Technical Name. `_set_object_filter()` searches
  column 6 for the row whose text is `"OBJECT"` rather than hardcoding a row index, so
  it keeps working even if a system orders `ARCH_DEF`'s fields differently.
- **Gotcha, same shape as DB02's tree control:** the result grid ("`ARCH_DEF`: Display
  of Entries Found") is a direct child of the window — `wnd[0]/shellcont/shell`
  (`GuiShell`, subtype `SAPGUI.GridViewCtrl.1`) — **not** nested under `wnd[0]/usr` like
  every other transaction in this app. A screen dump scoped to `usr` alone shows only
  header labels/counts, no grid at all — widen the dump to the whole `wnd[0]` window to
  find it.

Two diagnostic endpoints exist for correcting these on a different system (same
discovery-first pattern as DB15/DB02 — see
[Adjusting Screen Element IDs](#adjusting-screen-element-ids)):
`GET /api/transactions/se16n/debug-arch-def-screen` dumps the Selection Criteria
screen, and `GET /api/transactions/se16n/debug-arch-def-query?archiving_object=X`
reports every step of a query independently (filter readback, status bar text after
Execute, each candidate grid path's found/row_count/column_order/first_row, and a full
`wnd[0]` dump as a last resort).

## Session persistence

The tool keeps the SAP session alive in the backend process — closing or refreshing the browser tab does **not** disconnect from SAP. On page load the frontend calls `GET /api/sap/info`; if the backend is still connected it restores the connected state (system name, username) without requiring the user to re-enter credentials or re-authenticate.

If the SAP session is still live but the browser shows "Not Connected", click **Connect** with the same details — the backend detects the already-authenticated session, navigates back to the SAP main menu to ensure a clean screen state, and marks the session as connected without logging in again.

## Saved connection details

Click **Save** (next to **Connect** in the login form) to persist all connection fields — including the password — to `sap_credentials.json` at the project root. The next time the page loads (or the backend restarts), the form is pre-filled automatically from that file, so you only need to click **Connect**.

The credentials file is stored locally on the machine running the backend; it is not transmitted anywhere. Add it to `.gitignore` if this repository is shared.

## Adjusting Screen Element IDs

SAP GUI element IDs (e.g. `wnd[0]/usr/ctxtP_TNAME`) can differ across SAP versions and screen variants. Two ways to find the correct ones for your system:

**Option A — SAP GUI script recording:**
1. Open SAP GUI manually and navigate to the transaction
2. Go to **Help → Scripting → Record Script**
3. Perform the actions (fill fields, press Execute)
4. Stop recording and open the generated `.vbs` file
5. Copy the correct element IDs into the corresponding file in `backend/transactions/`

**Option B — built-in diagnostic endpoints (DB15, DB02, and SE16N/ARCH_DEF):** while connected to SAP, call these directly (browser, curl, or PowerShell's `Invoke-RestMethod`) instead of recording a script:

| Endpoint | Purpose |
|---|---|
| `GET /api/transactions/db15/debug-screen` | Navigates to DB15 and lists every element on the selection screen (id, type, name, text) — use this to find the correct radio-button / input-field IDs. |
| `GET /api/transactions/db15/debug-grid?table_name=BKPF` | Filters DB15 by the given table and reports, for each candidate grid path, whether it was found, its row count, column order, and first cell value — with the raw error text if a step fails. |
| `GET /api/transactions/db02/debug-screen?container_id=wnd[0]/usr` | Navigates to DB02 and recursively lists every element under the given container — use this to find tree/control IDs if they differ on your system. |
| `GET /api/transactions/db02/debug-tree?tree_id=...` | Lists every node's key + display text for the tree control at the given ID, to identify real node keys (e.g. for "SQL Editor") without guessing. |
| `POST /api/transactions/db02/debug-click` | Body `{tree_id, node_key, action}` — performs `select`/`expand`/`doubleclick` on a tree node, then dumps the screen afterward so the effect is visible. |
| `GET /api/transactions/db02/debug-probe-run?limit=20` | End-to-end dry run of the SQL Editor flow (open editor, set query text, execute, switch tabs, read the result grid), reporting each step's outcome independently. |
| `GET /api/transactions/se16n/debug-arch-def-screen` | Navigates to SE16N, loads `ARCH_DEF`'s Selection Criteria screen, and dumps every element — use this if the "Arch. Object" filter field can't be found. |
| `GET /api/transactions/se16n/debug-arch-def-query?archiving_object=X` | Queries `ARCH_DEF` for the given object and reports every step independently (filter readback, status bar text after Execute, each candidate grid path's found/row_count/column_order/first_row, and a full `wnd[0]` dump as a last resort) — for diagnosing a wrong or empty result. |

**Known SAP GUI ALV grid gotchas** (discovered while wiring up DB15, likely relevant to the other transactions too, since they share `sap_connector.py`'s grid-reading helper):
- `grid.GetColumnTitles(col_id)` is not a valid method on every system's ActiveX grid control — calling it can leave the grid object unable to serve subsequent `RowCount`/`GetCellValue` calls, even though the bad call itself is caught. Prefer hardcoding known column IDs (see `DB15_COLUMN_LABELS` in `db15.py`) over relying on that method.
- The grid only renders rows currently scrolled into its visible window — `GetCellValue` on a table with more results than fit in that window (e.g. CDHDR's ~24 archiving objects) silently returns `""` for the rows outside it. Set `grid.FirstVisibleRow = row_idx` before reading each row to force it into view first.
- A result grid isn't always nested under `wnd[0]/usr` — SE16N's `ARCH_DEF` result grid is a direct child of the window (`wnd[0]/shellcont/shell`), the same pattern DB02's tree control turned out to have. If a screen dump of `wnd[0]/usr` shows no grid/container at all, widen the dump to the whole `wnd[0]` window before assuming the feature is broken.

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
| GET | `/api/sap/info` | Current connection state including system + user (used by the frontend on page load to restore session) |
| GET | `/api/sap/credentials` | Return saved connection details from `sap_credentials.json` |
| POST | `/api/sap/credentials` | Save connection details (including password) to `sap_credentials.json` |
| GET | `/api/files/input` | List `.xlsx`/`.xls` files in the `input/` folder, newest first |
| GET | `/api/files/output` | List `.xlsx`/`.xls` files in the `output/` folder, newest first |
| POST | `/api/files/input/save` | Save table list rows to `input/list_of_tables.xlsx` |
| POST | `/api/files/output/save-archiving` | Save archiving-objects rows to `output/archiving_objects_by_table.xlsx` |
| POST | `/api/files/output/save-scored` | Save scored rows + recommended list + grouped list to `output/archiving_objects_scored.xlsx` |
| POST | `/api/files/output/save-header-tables` | Save header-table rows to `output/header_tables.xlsx` |
| POST | `/api/transactions/taana` | Run TAANA |
| POST | `/api/transactions/db15` | Run DB15 for a single table |
| POST | `/api/transactions/se16n` | Run SE16N |
| POST | `/api/transactions/se11` | Run SE11 |
| POST | `/api/transactions/aobj` | Run AOBJ |
| POST | `/api/transactions/sara` | Run SARA |
| POST | `/api/transactions/db15/batch` | Upload an Excel file of tables; starts the DB15 batch lookup in the background and returns `{status: "started", total}` immediately |
| POST | `/api/transactions/db15/batch-from-input` | Start DB15 batch using a file already in the `input/` folder (body: `{filename}`) |
| GET | `/api/transactions/db15/batch/progress` | Poll for batch lookup progress; `result` is populated once `status` is `"done"` |
| POST | `/api/transactions/db15/export` | Export batch DB15 results (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db15/debug-screen` | Diagnostic: dump DB15 selection-screen elements |
| GET | `/api/transactions/db15/debug-grid` | Diagnostic: filter DB15 by a table and report grid-read details |
| POST | `/api/transactions/db15/score` | Starts scoring every table/object row for archiving relevance via the Claude API in the background (including the housekeeping-program lookup for zero-candidate tables); returns `{status: "started", total}` immediately |
| GET | `/api/transactions/db15/score/progress` | Poll for scoring progress; `result` (rows + recommended) is populated once `status` is `"done"` |
| POST | `/api/transactions/db15/score-export` | Export the scored rows + recommended list + grouped-by-object list (JSON) to a downloadable 3-sheet `.xlsx` |
| POST | `/api/transactions/db15/group-by-object` | Group the recommended list by Archiving Object/Housekeeping Program with cumulative sizes, sorted descending — synchronous, no progress polling needed |
| POST | `/api/transactions/db02/top-tables` | Run the "top tables by size" SQL query via DB02's SQL Editor; returns Table Name, Description, Volume (GB), Volume (MB) |
| POST | `/api/transactions/db02/export` | Export the top-tables result (JSON rows) to a downloadable `.xlsx` |
| GET | `/api/transactions/db02/debug-screen` | Diagnostic: dump DB02 screen elements under a given container |
| GET | `/api/transactions/db02/debug-tree` | Diagnostic: dump a tree control's node keys + display text |
| POST | `/api/transactions/db02/debug-click` | Diagnostic: perform an action on a tree node and dump the result |
| GET | `/api/transactions/db02/debug-probe-run` | Diagnostic: dry-run the whole SQL Editor flow step by step |
| POST | `/api/transactions/header-tables/batch` | Upload an Excel file; starts the SE16N/ARCH_DEF header-table lookup for the top N archiving objects on its last sheet in the background |
| POST | `/api/transactions/header-tables/batch-from-output` | Start the header-table lookup using a file already in the `output/` folder (body: `{filename, max_objects}`) |
| GET | `/api/transactions/header-tables/batch/progress` | Poll for header-table lookup progress; `result` is populated once `status` is `"done"` |
| POST | `/api/transactions/header-tables/export` | Export header-table rows (JSON) to a downloadable 2-column `.xlsx` |
| GET | `/api/transactions/se16n/debug-arch-def-screen` | Diagnostic: dump `ARCH_DEF`'s Selection Criteria screen elements |
| GET | `/api/transactions/se16n/debug-arch-def-query` | Diagnostic: query `ARCH_DEF` for an archiving object and report every step independently |
