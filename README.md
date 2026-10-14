# SAP Assessment Automation

A desktop-local web application for SAP archivability analysis. A Python/FastAPI backend drives SAP GUI via Windows COM scripting; a React frontend provides the UI.

## Architecture

```
Browser (React + Vite)  ←→  FastAPI (Python)  ←→  SAP GUI (COM scripting)  ←→  SAP System
    localhost:5173              localhost:8000          win32com.client
                                    │
                                    ├──→  Claude API (scoring, housekeeping extraction, chat)
                                    └──→  Chrome for Testing (Selenium)  ←→  SAP for Me (me.sap.com)
```

Main backend modules (`backend/`): `main.py` (all endpoints), `sap_connector.py` (SAP GUI
connection), `transactions/` (one module per SAP transaction), `header_tables.py` (settles ambiguous header
tables), `llm.py` (the AI model switch: Claude or
Groq), `scoring.py` (AI scoring), `dvm_guide.py` (DVM Guide lookup), `housekeeping.py` + `sap_for_me.py`
(housekeeping-program lookup), `grouping.py` (Grouped by Object), `object_descriptions.py` (object descriptions), `chat.py` (the SAP
Assistant), `credentials_store.py` (several saved logins per credentials file), `reference_doc.py`
(reference document analysis), `table_analysis.py` (the Table Analysis task) with `transactions/taana_analysis.py`
(its TAANA screens), `open_item_analysis.py` (the Open Item Analysis task), `final_report.py` (the Final report generation task), `object_analysis.py` (the Archiving Object Analysis task) with `object_reference.py` (its reference-document check) and `transactions/sara_info.py`,
`help_browser.py` and `helpportal.py` (its SARA / SAP Help Portal steps), `progress.py` (progress
polling), `batching.py` (split-and-merge for steps that ask a model about many items or a long text), `completeness.py` (what a
result could not cover because of a model's limits), `tests/` (tests with a simulated model; see [Tests](#tests)), `debug_sap_for_me.py` (manual scraper diagnostics).

## Project status

| Area | Status |
|---|---|
| Generate Table List (DB02), Find Archiving Objects (DB15), table sizes | Working |
| Find archiving objects → score & recommend in one click (AI-scored, grounded in the DVM Guide) | Run end to end in the app: the one-click run is how the saved results that the later tasks were run on were produced |
| Housekeeping program lookup — DVM Guide stage | Working |
| Housekeeping program lookup — SAP for Me stage | Working in `debug_sap_for_me.py` (COSP → `RK_PLAN_DEL_ZERO_RECORDS`); **not yet verified through a full scoring run in the app**. Needs Chrome for Testing + IT approval. |
| SAP Community fallback (when SAP for Me has no usable Note/KBA) | Implemented for both the housekeeping and header-table lookups and checked offline with a stubbed browser. **Confirmed live (COSP, via `debug_sap_for_me.py`):** the Community search returns posts, they are recognised by their address shape (`/t5/…/(ba\|qaq\|td\|m)-p/<id>`) and a post's text reads cleanly. **Not yet seen live:** the fallback actually being used in a lookup (COSP has Notes, so it was never needed) and how good Community evidence is — for COSP the posts returned were mostly unrelated, which is why the model must see a program clearly named for the table before using one. Run `backend/.venv/Scripts/python.exe backend/debug_sap_for_me.py TABLE_NAME` to see what it finds (see [below](#housekeepingcleanup-program-lookup-for-tables-with-no-archiving-object)) |
| Grouped by Object sheet | Working (no Rationale column — it is on the Recommended sheet). The app has no button for it any more; the sheet stays in the saved workbooks because Find Header Tables, Open Item Analysis and the final report read it |
| `results/` folder: one sub-folder per task, results saved automatically (no save/download buttons), exact path shown, numbered copy when a file is open in Excel | Working and used in the app. The locked-file fallback was tested with read-only files, not with a real Excel lock |
| Final report generation (Overview workbook with an archiving-object sheet and a housekeeping-program sheet; Object details workbook with one sheet per object) | Run in the app on the current results (3 archiving objects) and on synthetic results, and the layout was checked in Excel. **Not yet tried** with many objects, or with several objects sharing one header table |
| Object descriptions always filled (run → remembered list → SAP → AI-suggested) | Working; verified live (override from a reference document, SAP lookup of the description) and offline with fake SAP/AI. The SAP stage reads table `ARCH_TXT`, whose layout was confirmed live with `debug-arch-def-query?archiving_object=FI_DOCUMNT&table=ARCH_TXT`; the other stages guarantee a value even if that lookup fails — see [Object descriptions](#object-descriptions) |
| Find Header Tables (SE16N/`ARCH_DEF`) | Working for objects with one top-level segment |
| Archiving Object Analysis (conditions from SARA's information page, DVM Guide and SAP for Me; objects to archive first from SARA's network) | **Run end to end against a live system** on FI_DOCUMNT and SD_VBAK (all three sources, dependencies, workbook); the two-step split (step 1, then SAP for Me in the background) was tested offline with fakes and live on SD_VBAK. The reference-document check was tested offline with real PowerPoint/Excel/Word files and canned model answers, and live with a generated deck. The tab handling, failure cases and ordering were also tested offline with fakes. Not yet run on the full list of recommended objects (about 5 minutes per object with all sources) or on a machine whose default browser is not Edge/Chrome — see [Archiving Object Analysis](#archiving-object-analysis) |
| Open Item Analysis (the sheet's open-item query per archiving object, run in DB02's SQL Editor, results to Excel — objects read from the Grouped by Object sheet, one workbook sheet per object) | **Run live on the OTE sandbox (client 100)** with the real DB02 SQL Editor: the query runner (SQL errors caught, zero rows, 2,500-row results, recovery after errors), and the whole job on IDOC, BC_SBAL, WORKITEM and an object without a query (workbook and Summary checked). Editing a query while another runs, cancel and the endpoints were tested offline with a fake DB02. **Not yet run:** the long queries (FI_DOCUMNT, SD_VBAK, MM_EKKO … on big tables). The sheet's WORKITEM query still fails until its client column is corrected (BC_SBAL's already was, and ran in the app) — see [Open Item Analysis](#open-item-analysis) |
| Table Analysis (TAANA ad hoc variant per header table, results to Excel) | **Run end to end against a live system** on tiny tables (SWWWIHEAD and BKPF, including two tables in parallel and a month field): field list (date, year and month fields offered), ad hoc variant with a year-grouped date plus another field, immediate background job, wait for "Completed", result copied to Excel. The question flow, several tables, skipping, a failed job and the saving were tested offline with a fake SAP, as were the Fields for Table Analysis sheet (listed table skips the questions, unlisted or unusable table is asked, sheet missing) and the re-run with additional fields (replaces the result; cancelled or failed re-run keeps the old one). The sheet-driven run and the re-run have **not yet been run against a live system**. **Not yet run** on large tables (long waits) or with many tables — see [Table Analysis](#table-analysis) |
| Header tables: DVM Guide + SAP for Me resolution of ambiguous objects, and reference-document review | Ran live once on ~20 objects; most resolved from ARCH_DEF, the rest via DVM Guide / SAP for Me. That run exposed objects left blank (no ARCH_DEF entries) and a search failing on `/` in namespaced names — both fixed since (every object now gets a header table; see below), checked offline with fake SAP/AI/browser sessions and then **re-tested live on the same ~20 objects**. Low-confidence rows are best guesses to be confirmed by the reference document |
| SAP Assistant (chat column that can collapse to a floating button: DVM Guide, SAP for Me, live SAP, scored results) | Implemented. **Checked live:** the DB02 largest-table / table-size tools against a real system (REPOSRC 2.63 GB), and the model choosing the DVM Guide, DB02 and general-knowledge routes. **Not yet run live:** the SAP for Me search tool and the other SAP tools through the chat window — see [Chat assistant](#chat-assistant) for known gaps. **Streaming** (word by word, status line, stops when the page is left): checked live on Groq (GPT-OSS 20B, with a DVM Guide tool call) and in a browser against a scripted model; Claude and Ollama streaming are tested with simulated streams only (no Claude key or Ollama model was available) |
| Sidebar layout: tasks first, Connection / AI model / SAP for Me as folded rows with a status line | Checked in a real browser (headless Chrome, 23 checks) against the real backend with a switchable fake SAP connection: all seven tasks are in view with no scrolling, rows open one at a time, Disconnect opens the Connection row and replaces the tasks with a hint, a restored session leaves the rows closed, the icon rail still works. Not tried with a live SAP session; see [Collapsible side panels](#collapsible-side-panels) |
| Several saved SAP / SAP for Me logins with a dropdown | Working; storage and endpoints tested offline with temporary files — see [Saved connection details](#saved-connection-details) |
| AI model switch (Claude, Groq free tier, or Ollama on this computer) | Working with a live Groq key; also checked offline with fake clients (all steps, rate limits, tool calls). Groq answer quality vs Claude **not yet compared**. Ollama: tested live with a tiny 270M model and offline with a fake server; **not yet tried with a real 7–8B model** — see [AI model selection](#ai-model-selection-claude-groq-or-ollama) |
| Leaving a task and coming back (every task keeps its state in the backend; runs and reviews continue while another task is open) | Tested in a browser with a faked SAP, AI model and results folder: leaving mid-run and returning, finished results, reference-review step and ticks, page refresh. **Not yet checked** against live SAP or a real AI model, or with very long runs |
| Scoring on free-tier models: parts and playoff for big candidate lists, output-limit pacing, unscored tables and retry | Written after live tests showed Qwen's 1,000 output tokens/min limit and 374-candidate tables (CDHDR, CDPOS) failing. Tested offline with fake models and a fake Groq client (chunk sizes, playoff, cut-off splitting, limit stops, retry merge, limiter) and in a browser with fakes. **Checked live on Qwen 3.8 27B (2026-10-13):** CDHDR (374 candidates) and BALDAT (34) scored completely with no refusal, and Groq accepted the "reasoning off" setting. **Not yet run live:** a full batch including CDPOS, a daily-limit stop, and Retry the unscored tables against Groq — see [Big tables and limits](#big-tables-and-limits) |
| Results never silently incomplete because of a model's limits (cut-off answers noticed on every provider, long documents and lists read in parts, what could not be done reported) | **Verified:** 91 offline tests with a simulated model that has a tiny window and output limit (tiny and big model give the same results, or every difference is reported); **live on Groq (GPT-OSS 20B):** a 27,380-character reference sheet gave all 360 of 360 mappings (the old code read the first 105 rows), a chat request with three 3,500-character articles is accepted with a 1,500-token answer allowance (it was 200), and the new token estimate is 1.02x-1.32x Groq's real count. **Not run live:** Claude and Ollama (no key / no local model here), so their cut-off handling is tested with simulated responses only; no "retry the incomplete items" button except for scoring — see [Model limits](#model-limits-complete-or-says-what-is-missing) |
| Reference document analysis (Excel / PDF / PowerPoint, one or several files) | Implemented; the multi-document merge was tested offline with canned model answers, not yet run end to end against real documents — see [Reference document analysis](#reference-document-analysis) |

## Prerequisites

| Requirement | Notes |
|---|---|
| SAP GUI for Windows | Must be installed (standard SAP Logon) |
| SAP GUI Scripting enabled (client) | Options → Accessibility & Scripting → Scripting tab → **Enable Scripting** |
| SAP GUI Scripting allowed (server) | Profile parameter `sapgui/user_scripting = TRUE` (RZ11, set by Basis) and authorization object `S_SCR` for your user. If Connect times out while the login window looks normal, check this first. |
| Python 3.11+ | `python --version`. The project's `.venv` is **32-bit** Python 3.12, which suits the SAP GUI COM scripting (and is why Selenium is used instead of Playwright). |
| Node.js 18+ | `node --version` |
| `ANTHROPIC_API_KEY` | In `backend/.env`; needed for every AI step while Claude is the selected model |
| `OLLAMA_URL` (optional) | In `backend/.env`; address of Ollama, default `http://127.0.0.1:11434`. Must be this computer — other hosts are refused. `OLLAMA_TIMEOUT` (seconds per answer, default 900) tunes slow machines |
| `GROQ_API_KEY` (optional) | In `backend/.env`; only needed if you switch to the Groq free tier (free key from console.groq.com). The app never asks for or stores it |
| Chrome for Testing + chromedriver | Only for the SAP for Me housekeeping lookup; needs IT approval. See [setup](#housekeepingcleanup-program-lookup-for-tables-with-no-archiving-object) |
| SAP for Me account | Email and password saved in the app's sidebar; internet access to me.sap.com, and SAP's terms must permit automated access |

### Enable SAP GUI Scripting

1. Open SAP Logon
2. Go to **Customize Local Layout** (Alt+F12) → **Options**
3. Navigate to **Accessibility & Scripting** → **Scripting**
4. Check **Enable Scripting**
5. Optionally uncheck **Notify when a script attaches** (avoids popups during automation)

If scripting is disabled, Connect fails with COM error 605 (`The 'Sapgui Component' could
not be instantiated`). The backend catches this and returns a readable message pointing
to the setting above instead of the raw COM error.

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

Both scripts install or update their libraries on every start (`pip install -r
requirements.txt` for the backend, `npm install` for the frontend), so after a `git pull`
just restart them — a package added by someone else's commit is picked up automatically.
Starting the frontend with `npm run dev` directly skips that step; run `npm install` first
if the pull changed `frontend/package.json`.

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
Archiving Objects for Tables", "Table Analysis"); the transaction(s) behind it — and
the names of the folders under `results/` — are an implementation
detail documented here for developers, not exposed in the app itself. (The one
exception is the [Chat assistant](#chat-assistant): users can ask it questions that
the assistant answers by running DB15, AOBJ, SE16N, SE11, TAANA, SARA or DB02 on their behalf, and its
replies always name the source it used.)

## Look and feel

The UI uses the Deloitte Green palette (charcoal header with the green line, green actions,
Open Sans). All colours are tokens at the top of `frontend/src/App.css`. Two rules to keep
when changing styles: Deloitte Green (`--primary`, `#86BC25`) is a *fill* colour, so text on it
is black (`--primary-text`) because white fails contrast, and green used *as text* on a light
background must be `--accent-text` (`#4C7A09`). The font is self-hosted through the
`@fontsource/open-sans` npm package (Latin only, weights 300/400/600/700), so it also works on
machines with no internet access.

## Collapsible side panels

Both side panels can be tucked away to give the middle of the page more room (`frontend/src/App.tsx`,
`frontend/src/components/AssistantPanel.tsx`, icons in `components/icons.tsx`, styles in `App.css`).

- **Sidebar layout (left).** The **tasks come first**, so a signed-in user never scrolls to reach them. Below them is a **Settings** group of three folded rows
  (`components/SettingsRow.tsx`): **Connection**, **AI model** and **SAP for Me**. Each shows its status on one line, so nothing has to be opened to see it
  (a green or red dot with "Connected — <system>" or "Not connected"; the model and how much of today's tokens are used, e.g. "Groq · Qwen 3.8 27B · 26% used";
  the saved SAP for Me account), and opens on click, one row at a time, with the same panel as before inside. While SAP is not connected the Connection row opens
  by itself, its sign-in form is in view and the task list is replaced by "Connect to SAP to unlock the tasks."; once connected, or when a session is restored after a
  reload, the rows are closed and the tasks are what you see. The panels stay mounted while a row is closed, so a half-typed form and the usage refresh are not lost.
- **Sidebar rail.** The chevron next to *Workspace* collapses it to a 60 px rail: an expand button, a dot showing whether SAP is
  connected, and one icon per task (hover for its name; the active task is marked). A rail icon switches task just like the list does.
- **SAP Assistant (right).** The chevron in its header collapses it to a floating **Ask the assistant** button in the bottom-right
  corner, which shows a red dot when a reply arrived while it was closed. The button opens the assistant as a pop-up over the page
  (Esc or the cross closes it, the dock button puts it back beside the page). The chat is never reset by any of this: the
  assistant stays mounted, so the conversation and the text being typed survive collapsing, popping up and docking.
- **Remembered.** The state is kept in the browser (`localStorage` keys `sap.sidebarCollapsed` and `sap.assistantDocked`); the pop-up itself is
  not remembered, only docked or not. A window narrower than 1,200 px starts with both panels collapsed the first time.

## Backend transaction modules

Each of the following has its own `backend/transactions/*.py` module and single-item
FastAPI endpoint (see [API Reference](#api-reference)), usable directly (e.g. via
`/docs` or curl) for development/debugging. The UI only offers the task-oriented tools
(Generate Table List, Find Archiving Objects for Tables, Find Header Tables for Archiving
Objects, Table Analysis, Archiving Object Analysis, Open Item Analysis and Final report
generation), each of which (except the last, which only reads the others' results) drives one or
more of the transactions below with its own automation — for example SE16N looks up a header table via `ARCH_DEF` (see
[Find Header Tables for Archiving Objects](#find-header-tables-for-archiving-objects)). The generic
single-item `run()` functions (browse a table, show a definition, list objects, …) have no
dedicated panel; the chat assistant uses them.

| Transaction | Purpose |
|---|---|
| **TAANA** | Table analysis. The **Table Analysis** task (below) drives it with ad hoc variants; the older single-item `taana.run()` endpoint is no longer used by the chat (see [Data sent to the AI model](#data-sent-to-the-ai-model)) and has not been verified against the real screens |
| **DB15** | Find which archiving objects reference a given table |
| **DB02** | HANA database administration cockpit (used here for its SQL Editor) |
| **SE16N** | Browse table contents with optional WHERE filter; also used for a specific ARCH_DEF header-table lookup (see below) |
| **SE11** | ABAP Dictionary — view table field definitions and data types |
| **AOBJ** | List all archiving objects with customizing settings |
| **SARA** | Archive Administration — view sessions and statistics for an archiving object; the Archiving Object Analysis task also uses its information button |

## Results folder

The backend creates one `results/` folder at the project root on startup, with one
sub-folder per task, named exactly like the task in the sidebar:

| Folder | What the task saves there automatically |
|---|---|
| `results/Generate Table List/` | `list_of_tables.xlsx` |
| `results/Find Archiving Objects for Tables/` | `archiving_objects_scored.xlsx`; after a reference-document review also `archiving_objects_with_reference.xlsx` |
| `results/Find Header Tables for Archiving Objects/` | `header_tables.xlsx`; after a reference-document review also `header_tables_with_reference.xlsx` |
| `results/Table Analysis/` | `Table analysis.xlsx` |
| `results/Archiving Object Analysis/` | `Archiving object analysis.xlsx` |
| `results/Open Item Analysis/` | `Open item analysis.xlsx` |
| `results/Final report generation/` | `Final report - Overview.xlsx` and `Final report - Object details.xlsx` |

There is no Save or Download button anywhere: a task writes its workbook the moment its
results are populated (the three analysis tasks keep updating theirs as objects/tables
finish) and the panel shows `Results saved automatically to <full path>`. If the file is
open in Excel, a numbered copy (`name (2).xlsx`, …) is written instead and the message shows that path; the
next save tries the original name again. The
folder names live in one place, `RESULT_FOLDERS` in `backend/config.py`.

A task that builds on an earlier one reads that task's folder (`GET /api/results/{task}/files`,
where `{task}` is the sidebar id such as `BATCH`, lists it and returns its full path): Find
Archiving Objects reads *Generate Table List*; Find Header Tables, Archiving Object Analysis
and Open Item Analysis read *Find Archiving Objects for Tables*; Table Analysis reads *Find
Header Tables for Archiving Objects*. Every one of these panels also lets you upload a file
from anywhere on disk instead. `debug/` (git-ignored) only holds the screenshot a failed SAP
for Me sign-in leaves behind.

## Find Archiving Objects for Tables

The **Find Archiving Objects for Tables** tab (`frontend/src/components/BatchArchivingPanel.tsx`) automates DB15 across a whole list of tables in one go. The tcode itself is an implementation detail — the UI only presents the task ("look up the archiving object(s) for these tables"), by design, so end users don't need to know which transaction does the work:

1. **Choose the input file.** The panel lists all `.xlsx`/`.xls` files in the `results/Generate Table List/` folder as radio buttons — typically `list_of_tables.xlsx`, saved by **Generate Table List**. If you want a different file, click **Upload a different file** to pick one from anywhere on disk; the uploaded filename replaces the radio list until you click **Use a saved table list** to go back.
2. Click **Find, Score & Recommend** — one click runs the whole pipeline: step 1 looks up the archiving objects in SAP, step 2 scores them and finds housekeeping programs (see [Score & Recommend Objects](#score--recommend-objects-claude-api)). A progress bar fills in for each step as each table finishes (e.g. "12 of 40 — BSEG"), since this can take a while on a large list.
3. The backend navigates to DB15 once, selects the **Archiving Objects** radio button, then for each table types it into **Objects for Table**, presses Enter, and reads the resulting archiving-object grid.
4. When both steps finish, two sheets are shown as tabs (20 rows per page, with ← → navigation): **All objects identified** (Table Name, Table Description, Volume (GB), Volume (MB), Archiving Object, Object Description, Housekeeping Program, Score) and **Recommended objects** (the top pick per table, plus the Rationale). If the lookup succeeds but scoring fails (for example a model rate limit), the lookup results stay on screen with a **Retry scoring** button, so the SAP lookup does not have to be repeated.
5. The scored workbook is saved automatically to `results/Find Archiving Objects for Tables/archiving_objects_scored.xlsx` when scoring finishes (and again if the chat assistant changes the results), and the panel shows the full path. The lookup-only result is not saved separately because the scored sheet contains the same columns plus the score.

Backend implementation: `db15.run_batch()` in `backend/transactions/db15.py`, plus `/api/transactions/db15/batch` in `backend/main.py` (see [API Reference](#api-reference), and [Progress polling](#progress-polling-for-long-running-batches) for how the progress bar works).

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
LLM by hand). Right after the lookup, in the same run as the lookup above (no second button), the tool
automates that judgment call:

1. The backend groups the results by table and sends each table's candidate list to the selected AI model
   (Claude `claude-haiku-4-5` by default) asking for a 0–100 relevance score per candidate; the model also names
   its best pick and gives **one short rationale for that pick only** (nothing in the app shows a rationale for the other candidates, and output tokens
   are what free-tier models are short of). Tables with only one candidate skip the model entirely (score is
   trivially 100). A table with more candidates than one answer can carry is scored in parts (see
   [Big tables and limits](#big-tables-and-limits)). Claude scores about 8 tables at a time (Groq 2, Ollama 1) since a large batch
   (e.g. 300 tables from the DB02 tool) can mean hundreds of calls — a progress bar tracks tables resolved, not
   individual calls, so it advances one table at a time even though several are scored in parallel underneath;
   the message under it names the step of a big table ("CDHDR: scoring part 3 of 10").
2. The results render as two tabs with ← → pagination: **All objects identified** (every table/object pair with its score) and **Recommended objects** (the highest-scoring object per table, one row per table). The recommended list is also rolled up by shared archiving object/housekeeping program on the workbook's "Grouped by Object" sheet (see below). The results are saved automatically as `results/Find Archiving Objects for Tables/archiving_objects_scored.xlsx`, a 3-sheet workbook: "All Scored Objects" (every table/object pair with its Score), "Recommended" (the top pick per table, plus the Rationale), and "Grouped by Object".
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
`/api/transactions/db15/score` in `backend/main.py`, which also saves the scored workbook when the run finishes.

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

#### Big tables and limits

DB15 can return hundreds of candidate objects for a table (CDHDR and CDPOS had 374 each on the test system, because almost every archiving object
uses change documents). One answer for that many candidates would need ~15,000 output tokens, which no free-tier model can give and which is
beyond even Claude's 16,000 cap. So (`backend/scoring.py`):

- **Parts, then a playoff.** A table above the model's part size (`score_chunk` in `llm.budget()`: 120 for Claude, 40 for Groq and Ollama) is scored
  part by part, scores only. Then the best 8 candidates (and up to 4 the DVM Guide excerpt names) are scored **together** with the rationale, because scores
  from different parts are not comparable. Nothing outside the playoff keeps a score above a finalist, so "highest score" still means "best".
- **Complete or asked again.** A part whose answer comes back cut off or malformed is split in two and asked again; candidates the model left out
  are asked for once more; whatever is still missing stays unscored instead of being guessed. A request never asks for more output than the model can give
  (`llm.max_output_tokens`).
- **Groq's output limit.** Some free-tier models also cap *output* tokens per minute, which is not in Groq's general limits table: Qwen 3.8 27B
  answered `Request too large ... on output tokens per minute (OTPM): Limit 1000`. `llm.GROQ_MODEL_LIMITS` knows that one, and a model that is not listed
  teaches itself from the text of its first refusal (`_learned_otpm`). The rate window (`llm._RateWindow`) now paces output tokens too (by what the model actually produced — checked live: Groq does not charge the unused part of
  `max_completion_tokens`), requests are capped at the limit, a "request too large" refusal is never re-sent unchanged, and the error shown to the user quotes Groq's own words. Reasoning is switched off for Qwen
  (thinking tokens would use up the small allowance); if Groq refuses that parameter the app goes on without it.
- **Nothing is lost or guessed.** When a rate or daily limit stops the run, the tables that are finished keep their scores and the rest are listed under
  `unscored` with the reason; the results screen says so and offers **Retry the unscored tables**, which re-scores only those and merges them into the
  earlier result (`scoring.merge_results`, `_run_scoring` in `main.py`; a retry that is refused again keeps the earlier result). A table where no candidate
  could be scored has **no recommended object** (Object Description "(not scored)", the reason as Rationale) instead of the first candidate by accident, so it
  does not feed Open Item Analysis or the final report until it is scored. Only a rejected key, or a limit reached before a single table was scored, ends the
  whole run with an error.

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
2. **SAP for Me portal search (implemented):** for a table the DVM Guide doesn't
   cover (or covers but names no program for), `backend/sap_for_me.py` drives a
   headless Chrome browser (via Selenium) to search SAP for Me for `"<table name>
   housekeeping program"`, keeps only **SAP Knowledge Base Article** and **SAP Note**
   results, reads the top 3, and sends their text to the selected AI model to extract a
   specific program, grounded and cited the same way the DVM Guide path is.
   **SAP Community fallback:** if that search yields no Note or Knowledge Base Article (or
   none can be read), the same browser searches SAP Community (`community.sap.com`, public,
   no sign-in) and reads the top 3 forum posts instead. They are labelled "SAP Community" to
   the model, which is told they are less authoritative and must clearly name a program for the
   table before one is used; a rationale that came from a forum post says so. Notes and
   Knowledge Base Articles always win: Community is only searched when there are none. This requires SAP for
   Me sign-in credentials — enter and save them once in the sidebar's **SAP for Me** row
   under Settings (stored in `sap_for_me_credentials.json` at the project root,
   git-ignored, same plaintext-JSON pattern as `sap_credentials.json`); every scoring
   run that needs this fallback logs in once and reuses that single browser session
   across every table needing it, since logging in per table would be far too slow.
   Requires **Chrome for Testing** and its matching chromedriver (see setup below).
   Selenium, not Playwright, specifically because this backend's virtualenv is 32-bit
   Python (for the SAP GUI COM scripting) and Playwright's `greenlet` dependency has no
   prebuilt wheel for that. To debug/iterate on the portal's selectors directly, run
   `backend\.venv\Scripts\python.exe backend\debug_sap_for_me.py TABLE_NAME` (from the project root; the venv's Python is needed for the installed packages) — it runs headed (visible browser)
   and prints what it found at each step, including a separate probe of the SAP Community
   search and its first post.

   **One-time setup — Chrome for Testing (each user).** Managed Chrome/Edge installs
   often have the IT policy `RemoteDebuggingAllowed = 0`, which stops any automation tool
   from controlling them. The scraper therefore uses Google's separate *Chrome for
   Testing* build, which needs IT's approval on the machine:
   1. From the [Chrome for Testing downloads page](https://googlechromelabs.github.io/chrome-for-testing/),
      download the Stable **`chrome`** and **`chromedriver`** zips for **win64**. The two
      must be the same version.
   2. Extract both into `C:\tools\cft`, giving
      `C:\tools\cft\chrome-win64\chrome.exe` and
      `C:\tools\cft\chromedriver-win64\chromedriver.exe`.
   3. Add these to `backend/.env`:
      ```env
      SAP_FOR_ME_CHROME_PATH=C:\tools\cft\chrome-win64\chrome.exe
      SAP_FOR_ME_CHROMEDRIVER_PATH=C:\tools\cft\chromedriver-win64\chromedriver.exe
      ```
   4. Verify with `backend\.venv\Scripts\python.exe backend\debug_sap_for_me.py <TABLE_NAME>` (set
      `SAP_FOR_ME_HEADLESS=false` to watch it).

   If the browser can't start (paths unset or wrong, or blocked by policy — the launch
   fails with `session not created: DevToolsActivePort file doesn't exist`), the lookup
   fails gracefully: the window is closed, scoring still completes with the DVM Guide
   results, and the affected tables get a blank Housekeeping Program with a Rationale such
   as "SAP for Me sign-in failed: could not start the browser (…)". On a failed sign-in
   the scraper also writes `debug/sap_for_me_debug.png` and `.txt` (screenshot, page
   text, console errors, failed requests) to help diagnose it. Notes on the live portal:
   it shows a cookie dialog (the scraper clicks "Deny All"), and article pages load slowly.

A table with neither an archiving object nor a housekeeping program still gets a row in
every result, with both columns blank and a Rationale explaining that nothing was found
(or why the SAP for Me lookup couldn't run, e.g. credentials not configured or the
browser couldn't start).

#### Grouped by Object

Open the "Grouped by Object" sheet in the scored workbook (the app no longer has a button
for it; Find Header Tables, Open Item Analysis and Final report generation read this sheet) to see which tables share the same archiving object or housekeeping program,
and how much data that represents together — useful for prioritizing which
object/program to tackle first. `backend/grouping.py`'s `build_object_groups()`:

- Groups tables by Archiving Object (if set), else Housekeeping Program (if set), else
  a "nothing found" bucket.
- Computes a `Cumulative Size (GB)`/`Cumulative Size (MB)`/`Table Count` per group,
  repeated on every member row in the data (so the rows stay directly sortable and can be
  read back programmatically); the Excel export then merges them visually, see below.
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
- The sheet deliberately has no Rationale column — it's already on the "Recommended"
  sheet, and repeating it per group member here would just be noise.

## Reference document analysis

After scoring, a **Reference Document Analysis** panel appears in
**Find Archiving Objects for Tables** (`frontend/src/components/ReferenceDocPanel.tsx`).
It lets the user cross-check the recommendations against one or more documents from past
project experience — Excel (`.xlsx`), PowerPoint (`.pptx`, including text in slide tables and speaker notes), Word (`.docx`), PDF, or CSV / text files (the old binary `.xls`, `.ppt` and `.doc` formats must be saved in the current format first). The same
panel is used for the header-table review in **Find Header Tables**.

**Priority is unchanged:** the DVM Guide comes first, then SAP for Me, and the reference
document last. The original recommendation is never overwritten automatically — where the
reference document disagrees, the difference is recorded in a **Comments** column and the
user decides what to do.

Flow:

1. Click **Upload Reference Document(s)** and select one or several files. A dialog then says the text will be sent to the AI model and asks you to tick that the
   documents contain no client data or other sensitive information (Cancel or Esc sends nothing; see [Data sent to the AI model](#data-sent-to-the-ai-model)). The backend (`backend/reference_doc.py`,
   `POST /api/reference-doc/analyze`) extracts the document's text (`openpyxl` for Excel,
   `pypdf` for PDF, `python-pptx` for PowerPoint) and asks the selected AI model to pull out
   explicit table → archiving object pairs. Only mappings stated in the document are used;
   nothing is guessed. Only the first part of each document (a character budget set by the model in
   use) is sent to the model.
   **Several documents** are read in the order the browser lists them and merged: the first
   document to mention a table (or archiving object) wins. If a later document names a different
   value, the Comments say so ("… (from a.xlsx). Other documents differ: X in b.pdf"). A file that
   can't be read or has no text is skipped and reported under the summary; the analysis fails only
   if no document is usable.
2. Each recommended table is compared with the document and put in one of three groups:
   - **Matched** — the document names the same object (Comments: "Matches reference document")
   - **Mismatch** — the document names a different object (Comments: "Reference document
     suggests: X"), **or** the app found no object for the table at all and the document
     names one — so the document can fill the gap and the override can be applied
   - **Not in reference document** — the table isn't mentioned
3. The panel shows the three counts, a table of mismatches with a checkbox on each row
   (plus Select all / Deselect all), and collapsible lists for the other two groups.
4. The user clicks **Apply N overrides**, or **Keep as-is** if none are ticked. A preview
   shows the final recommended list with the Comments column. An overridden row gets
   "Updated from X per reference document"; a mismatch left unticked keeps its original
   object and its "Reference document suggests" comment. An override also fixes the fields
   that described the old object: the Score comes from the scored list
   if that table was scored against the new object (otherwise it is left blank rather than
   showing the old object's value), the Object Description is **never blank** (see
   [Object descriptions](#object-descriptions)), the Rationale says it was chosen from the
   reference document, and a housekeeping program is cleared when a table receives an
   archiving object.
5. Pressing *Apply* (or *Keep as-is*) saves `results/Find Archiving Objects for Tables/archiving_objects_with_reference.xlsx` automatically (sheets:
   "All Scored Objects", "Recommended (with Ref Doc)" — which includes Comments — and
   **"Grouped by Object"**, built from the final list after your overrides, with the same
   columns, order and merged group cells as in `archiving_objects_scored.xlsx`). Grouped by
   Object is the last sheet on purpose: Find Header Tables reads a results file's last sheet,
   so this file can be used as its input too, and then reflects the SME corrections.
   The panel shows the full path. **Back to comparison** returns to the previous step.

### Object descriptions

A description belongs to the archiving *object*, not to a table, so it is reused wherever the
object appears. After an override, the new object's description is found through four layers
(`backend/object_descriptions.py`), and the cell is never blank:

1. **This run** — the description the same object already has on any table (DB15 results).
2. **Remembered list** — `backend/resources/archiving_object_descriptions.json` (git-ignored,
   built up from every DB15 run and SAP lookup, so an object seen once is covered later).
3. **SAP** — when SAP is connected, the archiving-object text table is read through SE16N
   (`se16n.get_archiving_object_texts`, English preferred, falling back to the first row). The
   table is **`ARCH_TXT`** ("Description of archive objects", columns `LANGU`/`OBJECT`/`OBJTEXT`),
   confirmed on a live system for `FI_DOCUMNT` (one German and one English row) and verified
   end to end. If the lookup fails the stage is skipped quietly and the
   other stages still apply. SAP texts are added to the remembered list.
4. **AI-suggested** — the selected AI model's best guess for whatever is left, shown as
   `(AI-suggested) <text>` and **never** added to the remembered list (it may not match SAP's
   wording). If no model is available the value is `(description not found)`.

The lookup runs once, during **Upload Reference Document** (it may call SAP and the model, so
that step takes a few seconds longer when objects are unknown). Saving (which happens automatically when the preview
opens) only uses layers 1 and 2 to fill any remaining gap, so it never calls SAP or the model. In the Grouped by
Object sheet a group shows the real description in preference to an AI-suggested one.

Requires `ANTHROPIC_API_KEY`. PowerPoint support needs `python-pptx` (now in
`requirements.txt`; run `pip install -r requirements.txt` after pulling).

**Known gaps:** the comparison only checks the recommended list, not every candidate;
overrides are applied in the panel's own preview and are **not** pushed back into the main
scored results or the chat's view of them; a document longer than 16 model-sized parts is read only as far as that (and the screen says
how much was read, see [Model limits](#model-limits-complete-or-says-what-is-missing)); scanned (image-only) PDFs yield no text and are rejected.

## AI model selection (Claude, Groq or Ollama)

The sidebar's **AI model** row (under Settings) chooses which model every AI step uses — scoring, the
housekeeping lookup (DVM Guide and SAP for Me stages), reference-document analysis and the
chat assistant. There is nothing to change per step: pick a model once and all of them
follow it. The header shows the active model (e.g. "AI: Groq · GPT-OSS 120B").

- **Claude** (default) — `claude-haiku-4-5` (`SCORING_MODEL`), billed to your Anthropic
  account; needs `ANTHROPIC_API_KEY` in `backend/.env`. Behaves exactly as before.
- **Groq (free tier)** — pick **GPT-OSS 120B**, **GPT-OSS 20B** or **Qwen 3.8 27B**. Each model has its own daily allowance, so when one is used up
  you can carry on with another. (GPT-OSS 20B is the smaller, faster sibling of the 120B: same free-tier limits, checked live with a structured answer;
  its scoring quality has not yet been compared with the other two.) The Groq API key is
  **not** entered in the app: put `GROQ_API_KEY=gsk_...` in `backend/.env` and restart the
  backend (the panel warns if it is missing). **Save** applies your choice (stored in
  `llm_settings.json`, git-ignored, which holds no keys); **Test** makes one tiny call to
  check the key and model.
- **Ollama (runs on this computer)** — a model served by [Ollama](https://ollama.com) on this machine. Nothing is sent
  over the network, there is no API key and no rate limit. The **Local model** list shows what is installed; **Auto — best
  installed** (the default) ranks the installed models and picks the best one that (1) fits in 60% of the computer's memory,
  (2) can call tools (the chat needs them), (3) isn't very small (under 3B parameters), then the one with most parameters.
  **Refresh models** re-reads the list after you `ollama pull` something. If the best model is too small or can't use tools,
  the panel says so and suggests one to pull (e.g. `ollama pull qwen3:8b`). Nothing is downloaded automatically.
  - *Always local:* the address (`OLLAMA_URL` in `backend/.env`, default `http://127.0.0.1:11434`) must be this computer
    (localhost, 127.x or ::1) — anything else is refused on every call; local traffic bypasses any corporate proxy; and Ollama
    **cloud** models (names ending `-cloud`/`:cloud`, or listed with a remote host) are hidden and rejected, because they run on
    Ollama's servers, not here.
  - *Tuned for local use:* a fixed 8,192-token context window (Ollama's default of 4,096 is too small for this tool's prompts),
    the same smaller prompt budgets as Groq, one request at a time, thinking switched off for reasoning models (it is slow on a
    CPU; GPT-OSS can only be set to "low"), JSON answers constrained by the schema with one retry, and a generous
    `OLLAMA_TIMEOUT` (default 900 s) per answer.
  - *What to expect:* answers come from a much smaller model than Claude's, so quality for scoring, housekeeping and reference
    documents will be lower — and a 270M-parameter model (the only one installed when this was added) is not usable for real work.
    A CPU-only machine needs roughly 1–3 minutes per scored table with an 8B model. Prefer Claude when quality matters; use Ollama
    when data must not leave the computer.

How it works: `backend/llm.py` is the only module that talks to an AI provider. The other
modules call `llm.structured()` (JSON answers: scoring, housekeeping, SAP for Me),
`llm.text()` (reference document) and `llm.run_tool_loop()` (chat, with the SAP tools); the
selection is read from `llm_settings.json` on each call. A scoring run reads it once at the
start, so switching mid-run can't mix two models in one result set. Endpoints:
`GET/POST /api/llm/settings` (GET also returns the Ollama models; `?refresh_ollama=true` re-reads them), `GET /api/llm/usage`, `POST /api/llm/test`.

**What to expect on the Groq free tier** (limits per model, from Groq's docs: 30 requests/min,
**8,000 tokens/min**, 1,000 requests/day, **200,000 tokens/day**):
- The per-minute token cap is a real limit. Groq mode paces itself, waits out per-minute
  limits automatically (the progress bar says "Waiting for the Groq rate limit…"), runs 2
  requests at a time instead of 8, and uses smaller prompts (shorter DVM Guide excerpts, SAP
  for Me articles and reference-document text; fewer tables and history in chat).
- **Qwen 3.8 27B also allows only 1,000 output tokens per minute** (found when scoring failed with "kept rate-limiting" — the app did not know about this
  limit). Scoring is shaped around it ([Big tables and limits](#big-tables-and-limits)): small answers, rationale for the top pick only, and pacing of output.
  **Measured live (2026-10-13):** a table with 374 candidates (CDHDR) was scored completely in about 10 minutes and ~32,000 tokens (10 parts, then a playoff of the best 8),
  and a table with 34 candidates in a single call. A batch with two such tables (CDHDR and CDPOS) plus a score of smaller ones therefore takes roughly 30–45 minutes and
  about half of the 200,000 daily tokens (an estimate from those measurements, not a measured full run); on Claude it takes a minute or two.
- The daily token cap therefore allows about 1–2 such batches per day. Chat is usable only lightly (about one tool round per minute).
- When a **daily** limit is reached, the run **stops** — it never silently switches to Claude. Tables already scored are kept and the rest are listed as
  unscored (**Retry the unscored tables** after the reset, or switch the model in the panel). If the limit is reached before any table is scored the run ends with a
  clear message. The panel shows today's requests and tokens used (counted locally in `llm_usage.json`).
- Answers can differ from Claude's: the prompts were written and tuned on Claude Haiku.
  Compare both models on the same ~10 tables before relying on Groq results.
- **Data handling:** in Groq mode, table names and descriptions, DVM Guide excerpts, SAP for Me
  article text and the uploaded reference documents you confirm are sent to Groq instead of
  Anthropic (see [Data sent to the AI model](#data-sent-to-the-ai-model); table contents never are).
  Check this against the client's data-handling rules and Groq's free-tier data terms first.

Ollama verification: checked **live** against Ollama 0.34 with `gemma3:270m` (the only model installed): model listing,
automatic pick, test call, schema-constrained JSON, usage count, cloud/uninstalled models rejected. Ranking with several models,
cloud filtering, thinking switch-off, retry on bad JSON, the tool loop, the non-local address refusal and Ollama being down were
checked offline with a fake server. **Not yet run:** a real 7–8B model, the chat's tool loop against a real model, and a full
scoring run — pull a model and compare before relying on it.

Verification status: the Groq path works with a live key (confirmed by the team after the
first run). The request shape, strict JSON-schema output, retries, daily-limit abort and
tool calls were also checked offline with fake clients. Still open: how Groq's answers
compare with Claude's on the same tables, and Qwen 3.8's exact reasoning behaviour and the
free tier's admission rules under a long batch — watch the first big run.

## Model limits: complete, or says what is missing

Every model has an input window and an output limit, and free tiers add per-minute and per-day caps. The app is built so that a limit never *silently* costs a
result. It promises two things: work is split to fit the active model, so a task completes on any model that can carry the smallest unit of it (one candidate,
one part of a document); and whatever still cannot be completed, or had to be shortened, is reported, never guessed. (Literal completeness on an arbitrarily
tiny model is not possible; a request that cannot fit even one unit is reported.)

- **A cut-off answer is always noticed** (`llm.LLMCutOffError`): Groq `finish_reason` "length", Ollama `done_reason` "length", Claude `stop_reason` "max_tokens",
  in structured, plain-text and chat calls. Claude's structured calls are parsed by the SDK itself, which fails on cut-off JSON before the stop reason can be read,
  so that error is treated as a cut-off (other format errors stay ordinary format errors).
- **Work is done in parts** (`backend/batching.py`): `run_batched` asks for as many items as fit, splits a batch in two when its answer is cut off or unreadable,
  asks once more for items the model left out and returns what still failed with the reason; `run_text_parts` reads a long text part by part (a single very long
  line is cut at a space, so no part exceeds its size). A rate-limit stop, rejected key or unconfigured model ends the work but keeps what was finished.
  Scoring has its own, older version of this (parts, then a playoff of the best candidates; see [Big tables and limits](#big-tables-and-limits)).
- **What could not be done is reported** (`backend/completeness.py`): a `Tally` records text that was shortened before it was sent and items that were not
  processed; tasks return it as `completeness` and/or in their warnings. The UI shows it in one notice (`CompletenessNotice.tsx`, used on the scoring result) and
  in each review's own warnings.
- **Token estimates and budgets follow the model** (`llm.chars_per_token`, `llm.budget`, `llm.input_window_tokens`): the estimate uses a per-model characters-per-token
  ratio (measured seeds for GPT-OSS and Qwen, refined from each reply's real prompt token count; the default 3.2 ran 1.2-1.5x too high on Groq and shrank
  answers for no reason). Groq's models report a 131,072-token context, but the free tier allows 8,000 tokens per minute, which is the window used; a local Ollama
  model gets proportionally smaller caps if its context is below 8,000 tokens. Claude's and Groq's caps are unchanged.

| Step | How it copes with the model's limits | What you see when it cannot |
|---|---|---|
| Scoring (Find Archiving Objects) | parts + playoff, re-ask for missing candidates, retry of unscored tables | tables listed as not scored with the reason and a **Retry the unscored tables** button; a note when a DVM Guide excerpt was shortened |
| Reference review (table to object, object to header table) | whole document read in up to 16 parts, asked as a list of pairs; a cut-off part is split | warnings: "only X of Y characters could be sent", "part N not processed (reason)"; nothing readable is an error, not an empty result |
| Reference check of Archiving Object Analysis | up to 8 parts per document; conditions compared in batches | a "could not be compared" list per object (never shown as gaps) and warnings |
| Condition extraction (Help Portal, DVM Guide, SAP for Me) | text beyond one request read in parts (up to 8 per source); a part with more conditions than one answer holds is read in pieces | the source's note says "INCOMPLETE" and the warnings list what was not read |
| Object descriptions (AI fallback) | every object asked about, in requests of up to 40 | the usual "(description not found)" placeholder for what stays unknown |
| Chat | cut-off answers continued; tools query up to 120 tables | a note ending the answer, or how many tables were not queried |
| Housekeeping lookup | small answers; reports when a DVM section was cut | "(Only the first N characters of the DVM Guide section were read.)" |

**Bounds that are deliberate and not reported:** passages chosen *by relevance* are selections, not truncations: the windows around a mention in the DVM Guide, the
parts of a SAP for Me article around the table name (`sap_for_me._relevant_excerpt`, 3,500 characters per article in chat) and the evidence assembled for a
header-table decision. The caps on parts (8 for a source or document check, 16 for a mapping sheet) and on a Help Portal page (60,000 characters) are stated in the
warnings when they are reached.

**Cost:** working in parts means more requests on a small model. On Groq's free tier a mapping sheet of 360 rows took about two minutes and 14,000 tokens; a long
Help Portal page needs several requests per object where it used to be cut to one. Claude takes the same work in far fewer requests.

**Not built:** a "retry the incomplete items" button for these tasks (only scoring has one); to redo a reference review upload the document again, or pick a model with
larger limits. Per-model limits other than Groq's published ones and Qwen's output limit are learned only from refusals.
Two further ideas were considered and deliberately left for later, because nothing needs them yet: a **preflight estimate** (before a long Groq run, the expected
requests, tokens and time against today's remaining allowance) and **reading spreadsheet mappings with code** when a sheet has recognisable table and archiving
object columns (the model would then only handle free text, which would also save tokens and time).

## Data sent to the AI model

The rule is that **no table data and no client data goes to the AI model.** What that means in the app:

- **Table contents are never read for the model.** No tool can return the rows, records or field values of any SAP
  table. The chat assistant has counts, sizes and structure only (see [Chat assistant](#chat-assistant)); the earlier row-browsing and table-statistics tools
  were removed. A message asking for table contents is refused before any model is called (`wants_table_contents()` in
  `backend/chat.py`), with a fixed reply, and the system prompt carries the same rule.
- **Reference documents are the one place document content reaches the model**, because the model has to read them. Before the text of a document
  is sent, a dialog (`frontend/src/components/SendToModelDialog.tsx`) says it will go to the AI model (naming it, and whether it is an online service or runs on this
  computer), tells the user to make sure it holds no client data or other sensitive information, and only enables **Send to the AI model** once the user
  ticks that they checked. Cancel or Esc sends nothing. It covers the table and header-table reviews and the Archiving Object Analysis check. The app cannot
  inspect a document for sensitive content, and the check is in the user interface only: the upload endpoints themselves do not ask.
- **Metadata that does reach the model:** table names and descriptions (including custom `Z*` tables), archiving object names and descriptions, the archiving
  definition structure (segment names) when settling a header table, public SAP text (DVM Guide, SAP Notes and Knowledge Base Articles, SAP Community, Help Portal),
  the chat's table names, sizes and entry counts, archiving-session facts (dates, record counts, file sizes), and whatever the user types in the chat.
- **Never sent:** table sizes during scoring, results workbooks, SAP and SAP for Me credentials, and anything from Table Analysis, Open Item Analysis or Final
  report generation (those tasks do not use the model).
- **Where it goes:** Claude and Groq are online services; Ollama runs on this computer and refuses any other address. Choose Ollama if even the metadata must
  stay on the machine.

## Chat assistant

The **SAP Assistant** is a chat column on the right of the app (`frontend/src/components/AssistantPanel.tsx`),
available on every task and whether or not SAP is connected; it can be collapsed to a floating button and
opened as a pop-up (see [Collapsible side panels](#collapsible-side-panels)). It answers from, in order of
authority: (1) the **DVM Guide**, (2) **SAP for Me**, (3) the **live SAP system**, (4) the user's current
scored results, and last (5) the model's own SAP knowledge, which it must label as general knowledge. It uses
whichever AI model is selected in the sidebar (see [AI model selection](#ai-model-selection-claude-groq-or-ollama)).
Example questions are offered as buttons, and **Clear** resets the conversation.

When **Find Archiving Objects for Tables** has scored results, they are shared with the assistant
(`frontend/src/assistantContext.tsx`; the panel publishes them with `usePublishResults`), so it can discuss
them and change them ("use FI_DOCUMNT for BKPF"). The preview in that panel refreshes. With no results loaded
the assistant simply has no `update_table_result` tool and answers general and live-system questions.

How it works (`backend/chat.py`, endpoint `POST /api/chat`):

1. The frontend sends the message, the scored rows and recommended rows (empty if there are none) and the
   chat history. The backend gives the model a summary of the results (best object per table, a limit set
   by the model in use), the tools below, and a system prompt that explains the sources and the SAP
   transactions it can run or only explain, and lets it make several rounds of tool calls per message.
2. Claude picks a tool based on the question:

   | Tool | SAP transaction | Used for |
   |---|---|---|
   | `lookup_archiving_objects` | DB15 | Which archiving objects cover a table |
   | `check_archiving_object` | AOBJ | Whether an object name exists, or listing by prefix (`MM_*`) |
   | `get_table_counts` | DB02 | How many entries named tables have (HANA statistics; a number only, never rows or values) |
   | `get_table_definition` | SE11 | A table's fields, types and key fields (first 30 fields) |
   | `get_archiving_sessions` | SARA | Whether and when an object has been run (latest 10 sessions) |
   | `get_largest_tables` | DB02 | The biggest tables by size (top N, default 10, max 50) — the same HANA size query as Generate Table List; column-store tables only |
   | `get_table_sizes` | DB02 | Size in GB/MB of named tables |
   | `search_dvm_guide` | — (DVM Guide) | Which object / cleanup program fits a table, by table name or keywords (`dvm_guide.search`); fast, offline |
   | `search_sap_for_me` | — (SAP for Me) | SAP Notes / Knowledge Base Articles (SAP Community as a fallback); **slow** - opens the browser and signs in with the active SAP for Me account |
   | `update_table_result` | — (in memory) | Change a table's archiving object, score or rationale (only offered when results are loaded) |

   **Table contents are never read.** There is no tool that returns rows, records or field values, and a request to see them ("show me rows
   of T001", a SQL query, "what is in table X") is refused before the model is called, with: "I'm not allowed to read or show table data. I can tell
   you how many entries a table has, its size, its structure, which archiving objects cover it and its archiving history." A count for a subset (one year,
   one company code) is not available either; Table Analysis gives such breakdowns without the model.

3. For `update_table_result`, the backend edits a copy of the rows, recomputes the
   Recommended list (highest score per table) and returns both; the frontend swaps them
   into the preview without re-running scoring.

4. **Source attribution:** every answer says where it came from ("DVM Guide: …", "SAP for Me (Note …): …",
   "Checked in AOBJ: …", "DB15 shows …", "From my general SAP knowledge: …"), so users can tell live data
   and SAP's own documents from the model's recollection. This is a rule in the system prompt in
   `backend/chat.py`.

5. **Streaming.** The panel calls `POST /api/chat/stream` (same body as `/api/chat`), so an answer appears word by word as the model writes it,
   with a blinking cursor, instead of after the whole answer is finished. While the assistant is *not* writing, one line says what it is doing:
   the tool it is running ("Searching the DVM Guide…", "Reading the table's structure…", never a transaction code) or, with Groq, "Waiting for the
   Groq rate limit (~58s)…". The reply is a stream of JSON lines, one event each: `token` (a piece of text), `status` (a sentence), then one `done`
   (the full reply plus `updated_rows` / `updated_recommended`) or an `error`. Text the model writes before a tool call stays in the answer, followed
   by a blank line. All three providers stream (`llm.run_tool_loop(..., on_token=...)`: Claude through `messages.stream`, Groq with `stream=True`,
   Ollama line by line with `<think>` blocks hidden); a streamed Groq answer is counted against the same limits as before. A refusal for table contents
   and a "not configured" message arrive as the single `done` event, because no model is called. If the model fails part-way (for example a rate limit),
   the text already shown stays and the reason follows it. Leaving the page or closing the app stops the model at its next piece of text.
   `POST /api/chat` still exists and returns the whole reply at once.

6. **Answers the model's limit cuts off are continued.** If the model stops because it reached its output limit, the loop asks it to "continue exactly where you
   stopped" (up to twice, streamed like any other text); an answer that is still cut off ends with "[The answer was cut off by the model's length limit.]". A tool
   request that was cut off is an error, never run with guessed arguments. The counts and sizes tools query up to 120 tables per request, in groups of 30, and say how
   many were not queried; when the conversation is longer than the model is given (6 messages on Groq) the model is told so and asks instead of guessing; on Groq the
   answer allowance follows the calibrated token estimate (it dropped to 200 tokens after a SAP for Me search with three articles; it is 1,500 now).

The seven SAP tools (DB15, AOBJ, SE11, SARA and three for DB02) use the shared SAP GUI session, so SAP must be connected for them; without it the
assistant says live checks are unavailable and still uses the DVM Guide, SAP for Me and its general knowledge.
Other transactions (SE16N, TAANA, SE38/SA38, SM36/SM37, SARI, …) it can explain but not run. It cannot run arbitrary
transactions or read everything in the system, only what these tools expose.

**Number formats:** DB02 returns each table's size as a raw byte count formatted with the SAP user's separators (`2,823,527,885` or `2.823.527.885`). `db02._read_result_grid()` keeps only the digits, so sizes are no longer blank for users whose format uses dots.

**Popup handling:** AOBJ shows an information dialog ("Caution: The table is cross-client")
as soon as it opens, which blocks the screen until its green tick is pressed. The shared
`dismiss_popup()` in `sap_connector.py` now presses the dialog's toolbar button
(`wnd[1]/tbar[0]/btn[0]`) first, falling back to Enter, and `dismiss_all_popups()` clears
stacked dialogs. `navigate_to()` clears any leftover popup before entering a transaction,
and `aobj.py` clears popups right after opening AOBJ and again after executing.

**Known gaps** (found by reading the code; not yet fixed):
- Rows added or promoted through chat contain only Table Name, Table Description,
  Archiving Object, Object Description, Score and Rationale — they lack Volume (GB),
  Volume (MB) and Housekeeping Program, so exports and the Grouped sheet can show blanks
  for them.
- A chat edit is saved to `archiving_objects_scored.xlsx` straight away, but it does not reach
  `archiving_objects_with_reference.xlsx` or the later tasks' results; run those steps again to pick it up.
- There is no guard against asking a live-SAP question while a DB15 batch is running;
  both drive the same SAP GUI session.
- Anything the user types or pastes into the chat is sent to the model as written. A note under the question box
  says not to paste client data, but nothing checks it.
- The scored rows sent to the chat have no Rationale field, so Claude never sees the
  reasons behind the scores.
- A SAP for Me search signs in and loads articles every time (no session is kept between messages), so it can
  take a minute or more; the assistant is told to try the DVM Guide first.

## Progress polling for long-running batches

Generate Table List, the DB15 lookup, Scoring, the Header Table batch lookup and the reference-document reviews can all take
minutes on a large batch (e.g. 300 tables from the DB02 tool, or 20 archiving objects each needing a
separate SE16N round trip), so all of them use a start-then-poll pattern instead of blocking the HTTP request until
the work finishes:

1. `POST /api/transactions/db15/batch` (or `/db02/top-tables`, `/header-tables/batch`, `/reference-doc/analyze`, …)
   validates the input, kicks off the real work on a background `threading.Thread`, and
   returns immediately with `{"status": "started", "total": N}`.
2. The frontend polls `GET .../batch/progress` (or `/db02/top-tables/progress`, `.../score/progress`,
   `/header-tables/batch/progress`, `/reference-doc/progress`) every ~700ms (`pollUntilDone()` in
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

Scoring has one extra phase after the bar reaches 100%: the housekeeping-program lookup
for tables with no archiving object. If any table needs the SAP for Me stage, the bar
stays full while its message cycles through "Searching SAP for Me for TABLE (i of n)…"
(`housekeeping.find_housekeeping_programs(on_progress=…)`), since each table involves
real browser navigation and can take a while.

Table Analysis, Archiving Object Analysis and Open Item Analysis use the same start-then-poll idea with their own
job state (`/api/table-analysis/progress`, `/api/object-analysis/progress`, `/api/open-item-analysis/progress`), polled by
their panels every second or two; Final report generation is a single quick request.

Generate Table List and the reference-document reviews have no sub-steps to count (one SQL query, one reading of the documents), so their panels
show an indeterminate animated bar (`<ProgressBar mode="indeterminate" />`) instead of a real percentage.

### Leaving a task and coming back

Every task keeps its state in the backend, not in the screen, so switching to another task (or refreshing the page) never loses what you were doing. This
is how the analysis tasks always worked; Generate Table List, Find Archiving Objects for Tables, Find Header Tables for Archiving Objects and their
reference-document reviews now work the same way:

- **Runs go on without the screen.** A started run keeps going in its backend thread. The old screen only stops *waiting*
  (`useScreenEpoch()` in `frontend/src/useScreenEpoch.ts` ends its poll loops), and the screen asks the backend for the state when it is opened
  (`GET .../progress`): a run still going shows its live progress bar, a finished one shows its results and saved path, a failed one shows why.
- **Find Archiving Objects is one run in the backend.** The lookup thread continues into the scoring by itself (`_run_db15_batch_job()` in `main.py`),
  and marks the scoring as started *before* it marks the lookup done, so a screen that sees the lookup finished never sees the previous run's scoring.
  Leaving during the lookup therefore no longer stops the scoring from starting. **Retry scoring** / **Retry the unscored tables** (`POST /api/transactions/db15/score`)
  is only for a scoring step that failed or left tables unscored.
- **Chat edits are remembered.** When the assistant changes the scored results they are saved to `archiving_objects_scored.xlsx` and also replace the
  remembered result, so a screen opened later shows the changed version.
- **Reference-document reviews are jobs too** (`/api/reference-doc/analyze`, `/api/header-reference/analyze`: start, then `/progress`). What the user
  did with the result (the step they are on, the ticked mismatches, the preview after *Apply*) is kept through `GET/PUT/DELETE /api/ui-state/{key}`
  (keys `ref-doc-tables` and `ref-doc-headers`) and restored with it.
- **A new run replaces the old state.** Starting a new lookup clears the scoring and the review of the earlier run; a new header-table lookup clears the
  header review; starting a new review clears its remembered step.
- **Header badges.** While a table list, an archiving-object lookup or scoring, a header-table lookup or a reference check is running, a badge such as
  "⏳ Scoring 3/40" shows in the header on every task (`RunningJobsBadge.tsx`), next to the analysis tasks' badges.
- **What is not remembered:** form inputs (the "Top N" number, the selected input file) reset when a screen opens, as in the analysis tasks. The state is
  held in the backend process's memory, so restarting the backend clears it; the saved Excel files are untouched either way.

## Generate Table List

Don't have a starting list of tables yet? The **Generate Table List** tab (`frontend/src/components/GenerateTableListPanel.tsx`) automates DB02/DBACOCKPIT's SQL Editor to build one:

1. Pick "Top N tables" (default 300) and click **Generate List**. The query runs in the background (an animated progress bar, no percentage — see [Progress polling](#progress-polling-for-long-running-batches) for why), so you can switch to another task and come back to the progress or the finished table (see [Leaving a task and coming back](#leaving-a-task-and-coming-back)).
2. The backend navigates to DB02, opens the **Diagnostics → SQL Editor** tree node, pastes in a canned HANA SQL query that lists the system's largest tables by total memory size, presses F8 (Execute), switches to the **Result** tab, and reads the grid.
3. Results include **Table Name**, **Description**, **Volume (GB)**, and **Volume (MB)** (both rounded to 2 decimal places; MB is included because many small tables round to `0.00` GB). The first 20 rows are shown as a preview with ← → pagination; the full list is in the saved file.
4. The list is saved automatically to `results/Generate Table List/list_of_tables.xlsx` (immediately available in the Find Archiving Objects file picker) and the panel shows the full path.

The saved file's columns (Table Name in column A, Description in column B) match what the **Find Archiving Objects for Tables** tool expects, so the saved file can be fed straight in without any edits. The Volume (GB)/Volume (MB) columns are included in the saved file but are not required by DB15 — see [Table size (Volume GB / MB)](#table-size-volume-gb--mb) for how they carry through.

Backend implementation: `db02.run_get_top_tables()` in `backend/transactions/db02.py`, plus `/api/transactions/db02/top-tables` (start) and `/api/transactions/db02/top-tables/progress` in `backend/main.py`; the job also saves the file.

### DB02 navigation robustness

DB02's SQL Editor node is a toggle — double-clicking it when already open closes it, leaving the input shell absent (SAP error 619). The backend guards against this: it navigates to the SAP main menu first (ensuring a clean DB02 start), then checks whether the SQL Editor shell already exists before double-clicking the tree node. This makes repeated runs within the same session reliable regardless of what screen DB02 was left on.

## Find Header Tables for Archiving Objects

Once archiving objects are scored and grouped, the next question for planning an
archiving run is: for each top object, which table is its **header table** — the root
segment in its structure, with no parent? The **Find Header Tables for Archiving
Objects** tab automates this via `SE16N` against the control table `ARCH_DEF`:

1. **Choose the input file.** The panel lists `.xlsx`/`.xls` files from the
   `results/Find Archiving Objects for Tables/` folder as radio buttons, preferring `archiving_objects_scored.xlsx` if present —
   only its **last sheet** ("Grouped by Object", already sorted by descending
   cumulative size) is read. Upload a different file instead if you want to use one
   with a differently-shaped last sheet; it just needs an "Archiving Object" column.
2. Pick "Top N archiving objects" (default 20) — `_parse_top_archiving_objects()` in
   `backend/main.py` collects the first N *distinct*, non-blank values from that column
   in row order, so housekeeping-program-only and "nothing found" rows are naturally
   skipped.
3. Click **Submit**. For each object, the backend navigates to SE16N, enters
   `ARCH_DEF`, sets the "Arch. Object" filter, executes, and reads the result grid —
   reusing one SE16N screen across all N objects (pressing Back between each)
   (`run_batch_find_header_tables()` in `backend/transactions/se16n.py`). It keeps every
   segment of each object, not just one, and classifies it:
   - **Exactly one top-level segment** (blank "Parent Segment") → that "Segment" is the
     header table, chosen automatically (Source `ARCH_DEF`, Confidence `High`).
   - **Several top-level segments, or none** → the object is *ambiguous*; the candidate
     tables are settled in a second stage below (previously the first one was silently
     picked).
   - **No ARCH_DEF entries at all, or the SE16N lookup failed** → there are no candidates, so
     the object also goes to the second stage, which then has to name the table itself.
     Either way **every object is meant to end with a header table** — nothing is left blank
     for the reference-document step.
4. **Settling ambiguous objects** (`backend/header_tables.py`, after the SAP GUI part is
   finished, so SAP isn't held up). For each ambiguous object:
   1. *DVM Guide pass.* The Guide section of each candidate table, plus the few sections that
      mention the archiving object, go to the selected AI model, which must pick one of the
      candidates and say how sure it is. A `high` answer stops here (Source `DVM Guide`).
   2. *SAP for Me pass*, for anything not high-confidence: search `"<object> header table"`,
      read the top SAP Notes / Knowledge Base Articles (one browser session for all objects,
      same setup as the housekeeping lookup), and ask the model again with that evidence plus
      the Guide's (Source `SAP for Me` or `DVM Guide + SAP for Me`). Both search phrases are
      tried against Notes / Knowledge Base Articles first; only if neither finds one are
      **SAP Community** posts searched (with the same two phrases), and the Source then reads
      `SAP Community` or `DVM Guide + SAP Community`.
   The first pass always runs: with nothing to read, the model answers from its own SAP
   knowledge (Source `AI model knowledge`), which is never reported as more than **Low**
   confidence, so the SAP for Me check still follows. SAP for Me tries `"<object> header
   table"` and then `"<object> archiving object tables"`. If the evidence is inconclusive
   the best guess is still filled in, marked **Low** confidence with the other candidates
   (if any) in Comments — a reviewer can then correct it. When ARCH_DEF gave candidates the
   model can only choose among them (an answer outside them is rejected); when it gave none,
   the answer must at least look like a table name. If SAP for Me can't run (no
   credentials, browser can't start) the object keeps its best guess and Comments say why.
   A header table is left blank only when the AI model is unavailable *and* ARCH_DEF gave no
   candidates — a table name can't be invented without a model — and Comments say so.

   **How useful is the DVM Guide here?** Only as supporting evidence. It is organised by
   table, not by archiving object, never states "the header table of X is Y" consistently,
   has no sections for several major header tables (MKPF, LIKP, VBRK, BSEG), and many
   sections are clipped at 6,000 characters. It does say so explicitly for some objects
   (e.g. MM_EKKO → EKKO, FI_DOCUMNT → BKPF, BC_SBAL → BALHDR, WORKITEM → SWWWIHEAD), so it
   helps choose between candidates ARCH_DEF already gave us. SAP for Me is the stronger
   tie-breaker, but its results for `"<object> header table"` have not yet been judged on a
   live run.
5. **Results** have five columns: Archiving Object, Header Table, **Source** (`ARCH_DEF`,
   `DVM Guide`, `SAP for Me`, `SAP Community`, …), **Confidence** (High / Medium / Low) and **Comments**.
   The table is saved automatically to `results/Find Header Tables for Archiving Objects/header_tables.xlsx`
   and the panel shows the full path. Resolving ambiguous objects takes extra time (an AI call
   each, plus a browser search and up to 3 article loads for the unsure ones) — the progress
   bar stays full while its message shows which object is being worked on.
6. **Reference document review** (below the results; same flow as the one in Find
   Archiving Objects). Upload an SME-written reference document (Excel, PDF or PowerPoint)
   listing each archiving object's header table. The selected AI model extracts the
   pairs and the app compares them with its own list into **matched**, **mismatched** and
   **not in the reference document**. Tick the mismatches to override with the document's
   value (a blank or low-confidence header table with a reference value counts as a
   mismatch, so the document can fill gaps), press *Apply*, review the preview (a
   *Reference Check* column records "Matches reference document", "Reference document
   suggests: X" or "Updated from X per reference document"; overridden rows become Source
   `Reference document`, Confidence `High`), and the result is saved
   automatically (`results/Find Header Tables for Archiving Objects/header_tables_with_reference.xlsx`:
   sheets "Header Tables" and "Header Tables (with Reference)"). Backend: `analyze_header_reference()` in
   `backend/reference_doc.py`; UI: `HeaderReferencePanel.tsx`, which shares its
   upload/compare/override/preview component (`ReferenceReviewPanel.tsx`) with the
   archiving-object review.

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
`wnd[0]` dump as a last resort). Add `&table=ARCH_TXT` to run the same query against another
table — that is how to confirm the archiving-object text table behind
[Object descriptions](#object-descriptions).

## Archiving Object Analysis

The **Archiving Object Analysis** task (`frontend/src/components/ObjectAnalysisPanel.tsx`) takes the recommended archiving objects and, for each
one, finds (1) its **archiving conditions** and (2) which other archiving objects **must be archived before it**, and writes everything to
`results/Archiving Object Analysis/Archiving object analysis.xlsx`. It works in **two steps** because SAP for Me is slow:

- **Step 1 — Start analysis (information page + DVM Guide + dependencies)**, about 1.5 minutes per object. The results are previewed on screen as each object
  finishes: a summary table, and below it an expandable preview per object with its **Archive these objects BEFORE …** block and its conditions table
  (each condition tagged with its source).
- **Step 2 — "Also check SAP for Me (runs in the background)"**, a button that appears once step 1 is done, for when the first results don't answer your
  questions. It runs in the background and adds the SAP for Me conditions to the same results and the same Excel file as each object finishes; the
  preview updates by itself. It needs only its own browser and the AI model — **not the SAP GUI** — so you can switch to other tasks meanwhile (a ⏳
  indicator stays in the page header; coming back to the task shows the live progress). *Stop SAP for Me* cancels it; running it again replaces the SAP for
  Me conditions rather than duplicating them. While step 2 runs a new analysis cannot be started (it would reset the results).

**Input.** By default the recommended objects saved by **Find Archiving Objects for Tables** (the panel lists `results/Find Archiving Objects for Tables/` and preselects `archiving_objects_with_reference.xlsx`,
else `archiving_objects_scored.xlsx`); **Upload a different file** to use another. From the workbook it reads the last sheet whose title
contains "Recommended" and that has an "Archiving Object" column (otherwise the last sheet with that column). Blank cells, placeholders such as
"(no archiving object found)" and duplicates are dropped.

**Reference documents (optional, after step 1).** Below the results, **Reference document(s)** lets you upload any readable document —
**Excel (.xlsx), PowerPoint (.pptx), Word (.docx), PDF, CSV or text/Markdown**, one or several — with past analysis or SME notes, to fill gaps and
spot mismatches (`backend/object_reference.py`; runs in the background, AI model only; the same confirmation dialog asks you to confirm the documents hold no client data before anything is sent). The tool reads, per archiving object, what the document
says about its **archiving conditions** and the **objects to archive first**, and compares that with the results:
- **Objects to archive first** are compared by name: *confirmed* (in both), *only in the document* (a **gap**: SAP's network doesn't list it) and *only in the
  tool* (in SAP's network but the document doesn't mention it; shown for information).
- **Conditions** are compared by meaning by the AI model, which sees only the two lists: *covered* (the tool already has it), *new* (a **gap**) and
  *conflict* (a **mismatch**: same subject, different requirement, e.g. another residence period).
Nothing changes until you tick items — each gap and mismatch has a checkbox (plus *Select all gaps and mismatches*) — and press **Apply N selected**:
a gap is added (conditions get the source "Reference document - <file>"; a document-only object is added to the archive-first list marked "From the
reference document", with no step number because SAP's network doesn't place it), and a mismatch replaces the tool's wording with the document's (the old
wording is kept in the Detail column). Applying the same item again does nothing. The preview and the Excel file update at once; the workbook gets a
**Reference check** column on the conditions and archive-first tables (matches / differs / not mentioned / added from the document), a **REFERENCE DOCUMENT
CHECK** block per object listing every gap and mismatch with "Applied to the results" or "Not applied", and a **Reference check** column on the Summary
sheet. Objects the document doesn't mention are marked as such. A file that cannot be read is skipped with a warning (an old `.xls`/`.ppt`/`.doc` must be
saved as `.xlsx`/`.pptx`/`.docx` first; a scanned PDF has no text). A document is read part by part (a part whose answer is cut off is split in two); one longer
than eight parts (about 112,000 characters with Claude, less with Groq) is read only as far as that, and the warnings say so. The comparison with the tool's
conditions is done in batches; a document condition that could not be compared (the answer was cut off, a limit was reached, or the verdict was missing or
unusable) is listed as **could not be compared** with the reason and is never shown as a gap.

**Conditions — three sources, each named on every condition** (`backend/object_analysis.py`). The AI model in use reads each source's text separately
and returns only conditions that are *in that text* (it is told never to add any from its own knowledge); a condition is a requirement that must
hold or be done before the object can be archived or deleted: checks of the write/delete program, required status, residence times, required
Customizing, other objects or documents to process first, blockers.
1. **SARA information button.** The tool opens SARA, enters the object and presses the `i` icon (*Documentation*, F6). SAP GUI answers by
   starting the machine's browser on the object's **SAP Help Portal** page (it shows nothing inside SAP GUI). The tool reads that page's address
   from the browser's address bar through Windows UI Automation (`help_browser.py`) and **closes that tab again** — each object's tab is closed
   before the next object's page is opened, and only the tab SAP opened is touched (matched by its page address; your own tabs are never closed).
   **The default browser must be Google Chrome:** Microsoft Edge shows the app no address bar or tabs through UI Automation (checked on this machine; Edge
   showed only empty panes), so with Edge the page opens but its address cannot be read, the tab is not closed, and tabs pile up. Browsers hide `https://`
   in the address bar, so the address is completed with it before the page is read. The
   page is then read **headlessly** with Chrome for Testing (`helpportal.py`; same setup as SAP for Me, no sign-in needed). Every page is kept in full (a page over 60,000
   characters is cut and reported) and its text is sent to the model in parts, up to eight per source (more calls on a small model, but nothing is cut to fit a
   small budget any more). Besides the page itself it
   reads sub-pages that describe conditions — child pages in the contents tree and pages the article names — whose title contains check, condition,
   prerequisite, precondition, archivability, residence, criteria, restriction or requirement (up to four), e.g. "Checks and Variants (SD-SLS)" or
   "Deletion Criteria for MM_EKKO". The portal's "Out of Maintenance" notice is dismissed automatically.
2. **DVM Guide** — the guide sections that mention the object (`dvm_guide.sections_naming`).
3. **SAP for Me** (step 2) — SAP Notes / Knowledge Base Articles for "<object> archiving conditions prerequisites" (SAP Community as the usual fallback),
   with the account saved in the SAP for Me panel. Until step 2 has run, each sheet says "SAP for Me: not checked yet". If no credentials are saved or
   sign-in fails, the sheet says so and nothing else is affected.

**Dependencies — which objects to archive first.** SARA's **Network Graphic** button is *not* used: on this SAP GUI it raises "Frontend version is
older than R/3 version" from the SAPnetz control and freezes the whole session, and a graphic cannot be read by scripting anyway. SAP keeps exactly
that network in table **`ARCH_NET`** ("Table with archiving objects for network display": `OBJECT`, `PREVOBJECT`, one row per "PREVOBJECT must be
archived before OBJECT"; 351 rows on the test system), which the tool reads once with a SQL query through DB02's SQL editor. For each object it follows
the predecessors through **every level** and puts them in **archiving order** (step 1 can be archived first; objects in the same step are independent
of each other); each entry says whether the object requires it directly or indirectly and which object in the chain needs it. Cycles in the data
cannot loop. Descriptions come from the same lookup the rest of the app uses (`object_descriptions`). If `ARCH_NET` cannot be read the sheets say
the dependencies are unknown rather than "none".

**Output workbook.** A **Summary** sheet (one row per object: description, **ARCHIVE BEFORE (in order)** highlighted, directly required objects, condition
counts per source, result/notes) and one sheet per object: a *Sources checked* block (what was read from each source, or why not, plus the Help Portal
address), an orange **"ARCHIVE THESE OBJECTS BEFORE <object>"** block (step, object, description, direct/indirect, required before — or an explicit "None"),
and a blue **ARCHIVING CONDITIONS** table (number, condition, source, detail). The file is rewritten after each object, so finished objects survive a
failure or cancel; if it is open in Excel, a numbered copy is written instead.

**Good to know.**
- **Time:** step 1 takes about 1.5 minutes per object; adding SAP for Me roughly 3 more minutes per object (two objects took about ten minutes in total on
  the test system) — which is why it is a separate, background step. A free Groq model also waits for its per-minute limit.
- During **step 1** SAP opens and closes a browser tab per object and uses your SAP GUI session — leave the browser and SAP alone until it finishes. Step 2
  does not touch SAP GUI or your browser.
- Needs the machine's default browser to be **Microsoft Edge or Google Chrome** with a visible window (not minimised); otherwise the address cannot be read and
  the SARA source is reported as unavailable (the DVM Guide, SAP for Me and the dependencies still run).
- An object SARA does not know, or for which no page opens, is marked **failed** with the reason; the others continue.
- The Help Portal page SAP opens is for the SAP release of the system (S/4HANA On-Premise 2020 here). Some "Checks" pages are not reachable from the page (the
  contents tree only lists expanded branches — FI_DOCUMNT's "Checks (FI-GL, FI-AR, FI-AP)" is an example), so their conditions come from the other sources.
- Conditions are model extractions of the source text, not authoritative SAP rules: verify important ones against the named source.

## Open Item Analysis

The **Open Item Analysis** task (`frontend/src/components/OpenItemAnalysisPanel.tsx`, `backend/open_item_analysis.py`) runs an open-item query for each
archiving object of the *Grouped by Object* sheet in DB02's SQL Editor and collects the results in one Excel file
(a Summary sheet and one sheet per object).

**Input.** By default the archiving objects on the **"Grouped by Object" sheet** saved by **Find Archiving Objects for Tables** (the panel lists `results/Find Archiving Objects for Tables/` and preselects `archiving_objects_with_reference.xlsx`,
else `archiving_objects_scored.xlsx`); **Upload a different file** to use another. Each object is taken once, in the sheet's order (largest cumulative size first); housekeeping-program-only groups have no object and are skipped. A file without a "Grouped by Object" sheet falls back to its "Recommended" sheet (`parse_objects(contents, prefer="grouped")` in `object_analysis.py`; Archiving Object Analysis keeps reading the "Recommended" sheet).

**The query sheet.** `backend/resources/Open Closed Item details.xlsx`, sheet *Open Item Analysis*, is a grid of cells that each read `OBJECT:` followed
by the SQL; the heading above a column (Sales Documents, Material Management, FINANCE, TECHNICAL) becomes the object's *Area*. The sheet is re-read
whenever the file changes. For each object, in order:
1. **Query in the sheet?** If not, the object is **skipped with the note "No query available…"** and the run moves on.
2. **Run it.** The query runs in DB02's SQL Editor (Diagnostics > SQL Editor). The sheet's queries return one row per document (year, month, category), which would be
   millions of rows, so the tool wraps them to **count the documents per year, month and category** (`SELECT Y, M, CAT, COUNT(*) AS ITEMS FROM (…) GROUP BY Y, M, CAT`).
   A query that doesn't have the Y / M / CAT shape, or already aggregates, runs unchanged.
3. **Result** goes to `results/Open Item Analysis/Open item analysis.xlsx` (written after every object, so finished objects survive a failure): there is **one sheet per archiving object** (named after it, holding only its result columns —
   Year, Month, Category, Items — so there is no "Archiving Object" column); objects without a result (skipped or failed) have no sheet. *Summary*, the first sheet, lists every object with its result
   (done / skipped / failed), rows, whether the query came from the sheet (and with which client) or was edited, the note and the SQL that ran. If it is open in Excel a numbered copy is written.

**The query box.** While a query runs the panel shows **which object and query is running** and for how long, in an editable box. Pick any object in the table
(*View / edit query*) to see or change its query, then **Run this query**: an object still waiting gets your query in place; for others the run is queued **right after
the query now running** (SAP cannot be interrupted mid-query). Your query runs exactly as typed and replaces that object's result; if it fails, the previous result is kept.
*Reset to the sheet's query* restores the original, and an object the sheet skipped can be given a query this way. The result's first 500 rows are previewed under the table.
The same works after the run has finished.

**Safety.** Only a **single read-only statement** (`SELECT` / `WITH`) is accepted — the check ignores text in quotes and comments and rejects `INSERT`, `UPDATE`, `DELETE`,
`DROP`, `ALTER`, `TRUNCATE`, `CREATE`, `CALL`, `MERGE`, `FOR UPDATE` and similar, and a second statement after `;`. Comments are removed and the query is sent on one line.

**Things to know.**
- **Client.** The sheet's queries hard-code `MANDT = '300'`. The tool always uses the client **you are logged on to**: it replaces the `MANDT = '300'` value in the sheet's queries
  with your client (the box shows the query with your client) and the panel says so — "Client changed to 100 … in N queries". It is reversible per object: *View / edit query*
  shows the change and a **Revert to client 300** button that runs the sheet's own query (and **Use client 100 again** to go back). The Summary sheet's *Query* column says which
  was used. Only `MANDT`, `MANDANT` or `CLIENT` `= '<3 digits>'` comparisons are replaced (joins like `K.MANDT = V.MANDT` are left alone), and queries you edit yourself are never altered.
- The category codes (`O`, `E`, `R`) are the ones the sheet's queries produce (O is the open bucket, E the closed one; the sheet doesn't say what R means), and the tool keeps them as they are.
- A query can run for minutes on large tables (waits up to 30 minutes each) and SAP is busy meanwhile, so the SAP Assistant's live-SAP tools wait too. A ⏳ indicator stays in the header if you switch tasks.
- **SQL errors.** SAP does not show HANA's error message in the SQL Editor: a failing statement leaves a success-style status bar message "Exception CX_DBA_ADBC in class CL_HDB_SQL_EXECUTOR" and an empty result grid with no columns (a valid query with zero rows still has its columns). The runner recognises both and reports "SAP could not run the query - usually a SQL error such as an unknown table or column…" instead of an empty result.
- **Row limit.** The SQL Editor cuts a result off at its *Max. No. of Rows* field (1,000 by default). The runner raises it to 10,000 and notes when a result reaches that.
- **Sheet queries that fail.** WORKITEM's query filters SWWWIHEAD on `MANDT`, but that table's client column is `CLIENT`, so it fails on this system until the sheet says `W.CLIENT = '300'` (checked live — 35 result rows with client 100). BC_SBAL's query used to have the same problem with BALHDR (`MANDANT`); the sheet now has `L.MANDANT = '300'` and it runs. The client substitution handles those column names too. An edit in the query box also fixes a single run.

## Table Analysis

The **Table Analysis** task (`frontend/src/components/TableAnalysisPanel.tsx`) runs a TAANA analysis for each header table and
collects the results in one Excel file.

**Input.** By default the file saved by **Find Header Tables for Archiving Objects** (the panel preselects the newest `header_tables*.xlsx` in the
`results/Find Header Tables for Archiving Objects/` folder); **Upload a different file** to use another. From a workbook with several sheets it reads the *last* sheet that
has a "Header Table" column — for `header_tables_with_reference.xlsx` that is the final list after the reference-document
overrides. Blank cells and duplicates are dropped. A file with only a "Table Name"/"Table" column also works.

**Two tracks run side by side** (`backend/table_analysis.py`), so you never wait for an analysis before the next table's questions: the
*question track* asks about each table in turn and schedules its SAP job at once; the *collector track* watches the running jobs and writes
each result to Excel as soon as its job completes. Per table:
1. The tool opens TAANA, starts *Table Analysis > Perform* for the table and opens **Create Ad Hoc Variant**, and reads every field TAANA
   offers. The ABAP dictionary types (table `DD03L`, one query for all tables through DB02's SQL editor) tell date, year and month fields
   apart: `DATS` = date; `ACCP`, or a 6-character field named/labelled like a period (e.g. `SPMON`) = year-month period; 4-digit `NUMC`
   named/labelled as a year (e.g. `GJAHR`) = year; 2- or 3-character `NUMC`/`CHAR` named/labelled as a month or posting period (e.g. `MONAT`,
   `POPER`) = month. The lookup takes roughly 30 seconds for five tables. If it fails, field names and labels are used instead and a
   warning is shown. The dialog is then cancelled, so SAP is not held while you decide.
2. **The Fields for Table Analysis sheet is checked first.** `backend/resources/Fields for Table Analysis.xlsx` lists, per header table, the field(s) to
   analyse (columns *Archiving Object | Header Table | Primary Date Field | Description*; any column with "Field" in its title is used, a
   cell may hold several fields separated by commas, and the file is re-read whenever it changes). If the table is listed and TAANA offers
   at least one of its fields, those fields are used and **you are not asked anything** for that table; date fields are grouped by year
   unless you untick *Group date fields by year* on the start form. Fields the sheet names that TAANA does not offer are left out and
   noted. Only if the table is not in the sheet — or none of its fields exists in TAANA — does the tool ask the questions below (with a
   remark explaining why, in the second case). A missing or unreadable sheet just means every table is asked, with a warning.
   The Summary sheet's *Fields chosen by* column shows which tables came from the sheet.
3. **Question 1 — date/year/month fields.** The date, year and month fields are listed with checkboxes, each tagged with its kind. A
   **Group dates by year** box (on by default) gives each selected date or year-month field the sub-field "first 4 characters", so the result has one row per year instead of one per day
   (the variant shows e.g. `WI_CD(4)`). Year and month fields are added as they are (a month field gives one row per month number).
4. **Question 2 — other fields?** Yes/No. If the table has no date, year or month field, this says so.
5. **Question 3 — other fields** (only on Yes). All remaining fields are listed (a filter box appears for long lists; commonly useful
   ones such as company code and document type are tagged *suggested* and listed first; the client field `MANDT` is hidden).
6. The tool creates the ad hoc variant with the chosen fields and schedules the analysis **in the background starting immediately**
   (Start Time dialog: Immediate, Save). **It then moves straight on to the next table's questions** — nothing waits for the analysis.
7. Meanwhile the collector checks TAANA every 10 seconds, for *all* running tables in one pass, until each run shows status **Completed**
   (a run is identified by its start date/time, because earlier ad hoc runs of the table stay in the tree), and copies the result grid into
   `results/Table Analysis/Table analysis.xlsx`. Results arrive in the order the jobs finish; the sheets are kept in your table order.

The panel shows two progress lines — *Questions* (tables answered) and *Analyses* (tables finished, with the ones still running in SAP
named) — and a **Tables processed** list where a running table shows *running* and a **Stop waiting** button. When the last question
is answered the tool keeps waiting for the remaining jobs and finishes when they are all done.

**Result.** All fields chosen for a table form **one combined analysis**: the grid has a column per field plus "No. Entr." (e.g. Status ×
Year), not one result per field. The workbook has a **Summary** sheet (table, result, fields analysed, rows, start time, note) and one
sheet per table (named after the table) with the grid, counts as numbers, and a **Total** row. It is rewritten after every table, so
finished tables are kept if a later one fails or the run is cancelled. Each run replaces the file; if it is open in Excel, a numbered copy
such as `Table analysis (2).xlsx` is written instead.

**Re-run with additional fields.** Once the whole run is done, every analysed table has an **Add more fields…** button. It lists the
fields not yet used (dates, years and months first, then suggested ones); tick the ones to add and the analysis is repeated with the
earlier fields **plus** the new ones. The table's sheet and Summary row in the workbook are replaced (marked *re-run*). If you cancel, tick
nothing, or the re-run's SAP job fails, the previous result is kept. A table can be re-run again and again.

**Controls.** *Skip current table* (also on each question) skips the table being asked about, until the last question is answered.
*Stop waiting* (on a running table) stops waiting for that table's job. *Cancel* stops asking and collecting. None of these stops a job
already scheduled in SAP, which keeps running there (cancelling reports how many). A table is marked **failed** if its job ends with an
error status, TAANA rejects the table, or it does not finish within 4 hours (`MAX_WAIT_SECONDS`); the other tables carry on. A table
where nothing was selected is marked skipped.

**Side effects in SAP.** Every run creates a background job and an analysis entry named `AD-HOC` under the table in TAANA; repeated
runs pile up there, newest first. Remove old ones in TAANA with *Table Analysis > Delete*. Several analyses can run in SAP at the same time (they
queue on the system's background work processes). While a run is active the SAP GUI session is in use — don't run other SAP features
or chat live-SAP questions at the same time; each of the tool's own SAP steps queues behind the previous one, so a table's field list can
take a little longer to appear while the collector is reading results.

**Verified screens** (`backend/transactions/taana_analysis.py`, documented in its docstring): TAANA opens an administration screen (analysis
tree + message grid); *Table Analysis > Perform* opens the "Start Table Analyses" popup; F4 on the variant field then **Ad Hoc Variant** (F5) opens the
variant dialog whose right grid lists the table's fields (`FIELDNAME`, `FIELDDESCR`) and whose left grid holds the chosen ones
(offset/length columns `PARTOFFSET`/`PARTLENGTH`); *Move left* adds the selected row; Continue, Continue, Continue, then Start Time > **Immediate** > Save.
The result is shown by selecting a field node under the run and choosing *Table Analysis > Display* (status field `D0100_O_STATUS`, result grid in
`cntlCUSTOM_CONTROL`).

**Known limits.** Reading a very large result grid cell by cell can take a while. The number of rows is whatever TAANA produces, so selecting
a day-level date together with several other fields on a big table can give a very large sheet — group by year and add fields sparingly.
The step that reads the field types assumes SAP HANA, like the other DB02 SQL features.

## Final report generation

The **Final report generation** task (`frontend/src/components/FinalReportPanel.tsx`, `backend/final_report.py`) combines the important
results of the other tasks into two Excel files, saved automatically in `results/Final report generation/` (no save or download button;
the panel shows both full paths). It only reads files the earlier tasks saved, so it does not talk to SAP. The panel first lists the
files it will use and whether each is found, using the plain file as a fallback, or missing; **Generate final report** then builds both.

**Where the data comes from.** Nothing is recomputed; each value is read from a result file:

| Content | File | Fallback |
|---|---|---|
| Archiving object, description, tables, the four size columns | `Find Archiving Objects for Tables/archiving_objects_with_reference.xlsx`, sheet *Grouped by Object* (columns A, B, D and F-I) | `archiving_objects_scored.xlsx` |
| Housekeeping program, its tables, the four size columns | the same sheet (columns C, D and F-I), for the groups that have no archiving object | the same fallback |
| Header table | `Find Header Tables for Archiving Objects/header_tables_with_reference.xlsx`, sheet *Header Tables (with Reference)*, column B, matched to the object by column A | `header_tables.xlsx` |
| Archiving conditions | `Archiving Object Analysis/Archiving object analysis.xlsx`, the Condition column of each object's sheet | none (column left blank) |
| Archive before | the same file, sheet *Summary*, column *ARCHIVE BEFORE (in order)* | none (column left blank) |
| Table analysis | `Table Analysis/Table analysis.xlsx`, the sheet of the object's header table | none |
| Open item analysis | `Open Item Analysis/Open item analysis.xlsx`, the object's sheet | none (the object's sheet simply has no open item part) |

If both files of a pair exist the "with reference" one is used; a warning appears when it is older than the plain one (it may be from an
earlier run). Of the analysis files the newest `<name>*.xlsx` is used, so a numbered copy written while the original was open in Excel
counts. Only the archiving objects file is required; anything else that is missing is left out and the panel says so.

**Overview** (`Final report - Overview.xlsx`), sheet *Overview*. One row per table, in the order of the *Grouped by Object* sheet (largest
cumulative size first): Archiving Object, Archiving Object Description, Header Table, Tables, Archiving Conditions (numbered, one per
line), Archive Before (the objects to archive first, numbered in archiving order; "None" when there are none), Volume (GB), Volume (MB), Cumulative Size (GB), Cumulative Size (MB). What is common to an object, which is its name,
description, header table, conditions, archive-before list and cumulative sizes, is merged into one cell across its tables, and a heavier line separates objects.
Merged cells do not auto-size, so each group's row height is set from its longest wrapped text.

The second sheet, *Housekeeping Programs*, holds the groups of the *Grouped by Object* sheet that have a housekeeping program instead of an
archiving object: Housekeeping Program, Tables, Volume (GB), Volume (MB), Cumulative Size (GB), Cumulative Size (MB), with the program and the
cumulative sizes merged across its tables, in the same style and order. (A program that merely appears on an archiving object's rows is not
listed: a table has either an archiving object or a housekeeping program.) The sheet is always present; with no such groups it only has its header.

**Object details** (`Final report - Object details.xlsx`, one sheet per archiving object). Each sheet has the object, its description and
header table at the top and, side by side below, the object's *Table analysis* (green banner, the fields analysed, the grid with its Total
row highlighted) and its *Open item analysis* (counts as real numbers). When the object has no open item analysis there is no mention of
it. An object that has neither analysis gets no sheet, and the panel's warning says how many were left out (the analyses normally cover
only the top N objects). Several objects sharing one header table each get that table's analysis. The older single-sheet Open item
analysis file is still understood.

API: `GET /api/final-report/sources` (the files that would be used and their status) and `POST /api/final-report/generate` (builds and saves both
files; returns `{files: [{name, path}], objects: [...], warnings: [...]}`; HTTP 400 if the archiving objects file is missing).

## Session persistence

The tool keeps the SAP session alive in the backend process — closing or refreshing the browser tab does **not** disconnect from SAP. On page load the frontend calls `GET /api/sap/info`; if the backend is still connected it restores the connected state (system name, username) without requiring the user to re-enter credentials or re-authenticate.

If the SAP session is still live but the browser shows "Not Connected", click **Connect** with the same details — the backend detects the already-authenticated session, navigates back to the SAP main menu to ensure a clean screen state, and marks the session as connected without logging in again.

## Saved connection details

Click **Save** (next to **Connect** in the login form) to persist all connection fields — including the password — to `sap_credentials.json` at the project root. Saves **accumulate**: saving a different system/client/user adds another entry, while saving the same one again updates it. A **Saved connections** dropdown above the form lists every saved entry (`user @ system (client N)`); choosing one fills the form, and **Delete** removes it. The most recently saved or chosen entry is the one the form opens with after a page reload or backend restart, so you only need to click **Connect**.

The **SAP for Me Credentials** panel works the same way (a **Saved accounts** dropdown, keyed by email). The account that is currently saved or selected there is the one the SAP for Me lookups sign in with, so switching accounts in the dropdown switches which login the scraper uses.

The credential files are handled by `backend/credentials_store.py`. A file from an older version (one flat entry) is read as a single saved entry and converted the next time you save.

The credentials file is stored locally on the machine running the backend; it is not transmitted anywhere. It is plain JSON (the password is not encrypted) and is already listed in `.gitignore`, as is `sap_for_me_credentials.json`, which holds the SAP for Me email and password in the same way (saved from the "SAP for Me Credentials" panel).

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
| `GET /api/transactions/se16n/debug-arch-def-query?archiving_object=X[&table=T]` | Queries `ARCH_DEF` (or table `T`, e.g. `ARCH_TXT`) for the given object and reports every step independently (filter readback, status bar text after Execute, each candidate grid path's found/row_count/column_order/first_row, and a full `wnd[0]` dump as a last resort) — for diagnosing a wrong or empty result. |

**Known SAP GUI ALV grid gotchas** (discovered while wiring up DB15, likely relevant to the other transactions too, since they share `sap_connector.py`'s grid-reading helper):
- `grid.GetColumnTitles(col_id)` is not a valid method on every system's ActiveX grid control — calling it can leave the grid object unable to serve subsequent `RowCount`/`GetCellValue` calls, even though the bad call itself is caught. Prefer hardcoding known column IDs (see `DB15_COLUMN_LABELS` in `db15.py`) over relying on that method.
- The grid only renders rows currently scrolled into its visible window — `GetCellValue` on a table with more results than fit in that window (e.g. CDHDR's ~24 archiving objects) silently returns `""` for the rows outside it. Set `grid.FirstVisibleRow = row_idx` before reading each row to force it into view first.
- A result grid isn't always nested under `wnd[0]/usr` — SE16N's `ARCH_DEF` result grid is a direct child of the window (`wnd[0]/shellcont/shell`), the same pattern DB02's tree control turned out to have. If a screen dump of `wnd[0]/usr` shows no grid/container at all, widen the dump to the whole `wnd[0]` window before assuming the feature is broken.

## Tests

`backend/tests/` holds tests that need no network, no SAP and no AI quota: a simulated model (`tests/fakes.py`, `TinyModel`) with a configurable window and output limit
cuts answers off and refuses oversized requests like a real model would, and answers deterministically so completeness can be asserted exactly. They cover the
cut-off detection per provider, `batching.py`, every task that sends text to a model (tiny model vs big model: same results, or every difference reported), the
token estimate and budgets, and the earlier suites for scoring, the retry endpoints, the streaming chat and the chat data policy (`tests/suites/`).

    cd backend
    .venv\Scripts\python.exe tests\run_all.py            # everything (about 15 seconds)
    .venv\Scripts\python.exe tests\run_all.py batching   # only files whose name contains "batching"

The tests are plain `test_*` functions, so `pytest` runs them too once installed (`pip install -r requirements-dev.txt`). Claude and Ollama are covered with simulated
responses only; the live checks listed in the project status were run by hand.

## Configuration

Create a `backend/.env` file to override defaults:

```env
SAPLOGON_EXE=C:\Program Files (x86)\SAP\FrontEnd\SAPgui\saplogon.exe
SAP_SCREEN_WAIT=1.5
API_HOST=127.0.0.1
API_PORT=8000

# Needed for every AI step while Claude is the selected model (backend/llm.py)
ANTHROPIC_API_KEY=sk-ant-...
SCORING_MODEL=claude-haiku-4-5

# Only needed if you select Groq in the app's AI Model panel
# GROQ_API_KEY=gsk_...

# Only if Ollama is not on its default address (must be this computer); seconds to wait for one answer
# OLLAMA_URL=http://127.0.0.1:11434
# OLLAMA_TIMEOUT=900

# SAP for Me scraper (backend/sap_for_me.py): Chrome for Testing + matching
# chromedriver (see the housekeeping section for setup); headless by default,
# set SAP_FOR_ME_HEADLESS=false to watch the browser while debugging
SAP_FOR_ME_CHROME_PATH=C:\tools\cft\chrome-win64\chrome.exe
SAP_FOR_ME_CHROMEDRIVER_PATH=C:\tools\cft\chromedriver-win64\chromedriver.exe
SAP_FOR_ME_HEADLESS=true
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
| GET | `/api/sap/credentials` | Return the active saved connection at top level plus every saved one under `profiles` |
| POST | `/api/sap/credentials` | Add or update a saved connection (identified by system + client + username) in `sap_credentials.json` and make it active |
| POST | `/api/sap/credentials/select` | Make a saved connection active (body: `{system, client, username}`) |
| POST | `/api/sap/credentials/delete` | Remove a saved connection (same body) |
| GET | `/api/sap-for-me/credentials` | Return the active SAP for Me account at top level plus every saved one under `profiles` |
| POST | `/api/sap-for-me/credentials` | Add or update a saved SAP for Me account (identified by email) and make it the active one used for lookups |
| POST | `/api/sap-for-me/credentials/select` | Make a saved SAP for Me account active (body: `{email}`) |
| POST | `/api/sap-for-me/credentials/delete` | Remove a saved SAP for Me account (same body) |
| GET | `/api/llm/settings` | Active AI provider/model, the Groq models offered and Groq's free-tier limits (API keys are never returned, only whether each is set in `backend/.env`) |
| POST | `/api/llm/settings` | Choose the AI model for every AI step (body: `{provider: "anthropic"\|"groq"\|"ollama", model}` (Ollama: empty model = Auto); API keys come from `backend/.env`, not this call |
| GET | `/api/llm/usage` | Today's request/token count for the active model |
| POST | `/api/llm/test` | One tiny call to check the saved key and model work |
| POST | `/api/chat` | One chat turn over scored results (body: `{message, scored_rows, recommended, history}`); returns `{reply, updated_rows, updated_recommended}` — the last two are `null` unless Claude changed a result |
| POST | `/api/chat/stream` | The same turn, streamed as newline-delimited JSON (`application/x-ndjson`): `{"type":"token","text"}` per piece of the reply, `{"type":"status","text"}` while a tool runs or a limit is waited out, then `{"type":"done","reply","updated_rows","updated_recommended"}` or `{"type":"error","message"}`. This is what the chat window uses |
| GET | `/api/results/{task}/files` | List the `.xlsx`/`.xls` files in a task's `results/` sub-folder (newest first) and return the folder's full path; `{task}` is `TOP_TABLES`, `BATCH`, `HEADER_TABLES`, `TABLE_ANALYSIS`, `OBJECT_ANALYSIS`, `OPEN_ITEM_ANALYSIS` or `FINAL_REPORT` |
| POST | `/api/results/archiving-objects/save` | Re-save the scored rows + recommended list + grouped list (body: `{rows, recommended}`) to `results/Find Archiving Objects for Tables/archiving_objects_scored.xlsx` — used after the chat assistant changed them; the first save happens automatically when scoring finishes |
| POST | `/api/object-analysis/start` | Start step 1 of the Archiving Object Analysis (information page, DVM Guide, dependencies) for the objects in an uploaded `file` or in `filename` (a file in `results/Find Archiving Objects for Tables/`); runs in the background |
| GET | `/api/object-analysis/progress` | Step 1 status, current object/step, per-object records (conditions per source, objects to archive first, notes), output path, and the `sap_for_me` step's status/progress |
| GET | `/api/object-analysis/results` | The full results for the preview: per object, the conditions (with sources), the objects to archive first, what each source returned |
| POST | `/api/object-analysis/reference/analyze` | Multipart upload of one or more reference documents (`files`; Excel, PowerPoint, Word, PDF, CSV or text); starts the reference check in the background (allowed once step 1 is done) |
| GET | `/api/object-analysis/reference/review` | The reference check's state and, when done, its review: per object, what is confirmed, the gaps and the mismatches, each selectable one with an id |
| POST | `/api/object-analysis/reference/apply` | Apply the selected gaps / mismatches: body `{ids: [...]}`; returns how many were applied |
| POST | `/api/object-analysis/sap-for-me` | Start step 2 (SAP for Me) in the background; allowed once step 1 is done |
| POST | `/api/object-analysis/sap-for-me/cancel` | Stop step 2; objects already finished keep their conditions |
| POST | `/api/open-item-analysis/start` | Start the Open Item Analysis for the archiving objects in an uploaded `file` or in `filename` (a file in `results/Find Archiving Objects for Tables/`); objects without a query in the sheet are skipped; runs in the background |
| GET | `/api/open-item-analysis/progress` | Status, the query now running (object, SQL, start time), per-object records (state, rows, note, query, the sheet's query) and the output path |
| GET | `/api/open-item-analysis/result?object=` | One object's result (columns and the first 500 rows) for the preview |
| POST | `/api/open-item-analysis/query` | Run the user's own or edited query for an object: body `{object, query}` (read-only SELECT only; HTTP 400 otherwise); it replaces that object's result |
| POST | `/api/open-item-analysis/cancel` | Stop after the query now running |
| GET | `/api/final-report/sources` | The result files the final report would be built from, with each one's status (found / fallback / missing) |
| POST | `/api/final-report/generate` | Build `Final report - Overview.xlsx` and `Final report - Object details.xlsx` from the saved results and save them in `results/Final report generation/`; returns the two full paths, one summary row per archiving object and warnings |
| POST | `/api/object-analysis/cancel` | Stop after the current object; finished objects stay in the workbook |
| POST | `/api/table-analysis/start` | Start the Table Analysis for the header tables in an uploaded `file` or in `filename` (a file in `results/Find Header Tables for Archiving Objects/`); form field `group_by_year` (default true) applies to tables taken from the Fields for Table Analysis sheet; returns the tables found and runs in the background |
| GET | `/api/table-analysis/progress` | Status, current table/step, the open question (if any), the per-table records and the output path |
| POST | `/api/table-analysis/answer` | Answer the open question: body `{prompt_id, answer}` — `{selected, group_by_year}` (date fields), `{add}` (other fields?), `{selected}` (other fields) |
| POST | `/api/table-analysis/redo` | After the run is done, re-run one table with additional fields: body `{table}`; the question comes back through `/progress` as a `redo_fields` prompt answered with `{selected, group_by_year}` (HTTP 409 if the run isn't done or the table has no finished analysis) |
| POST | `/api/table-analysis/skip` | Skip the table being worked on |
| POST | `/api/table-analysis/stop-waiting` | Stop waiting for one table whose SAP job is running: body `{table}` (the job keeps running in SAP; HTTP 409 if that table is not waiting) |
| POST | `/api/table-analysis/cancel` | Stop after the current step; finished tables stay in the workbook |
| POST | `/api/reference-doc/analyze` | Multipart upload: `files` (one or more Excel/PDF/PowerPoint) + `recommended` (JSON string of the recommended rows); starts the comparison in the background and returns `{status: "started"}` (HTTP 409 if one is running) |
| GET | `/api/reference-doc/progress` | The comparison's state; `result` (matches, mismatches, not-in-reference rows, the annotated recommended list) is set once `status` is `"done"` |
| POST | `/api/reference-doc/save` | Save the reference-annotated results (body: `{rows, recommended}`) to `results/Find Archiving Objects for Tables/archiving_objects_with_reference.xlsx`; returns the full path |
| POST | `/api/transactions/taana` | Run TAANA |
| POST | `/api/transactions/db15` | Run DB15 for a single table |
| POST | `/api/transactions/se16n` | Run SE16N |
| POST | `/api/transactions/se11` | Run SE11 |
| POST | `/api/transactions/aobj` | Run AOBJ |
| POST | `/api/transactions/sara` | Run SARA |
| POST | `/api/transactions/db15/batch` | Upload an Excel file of tables; starts the DB15 batch lookup in the background and returns `{status: "started", total}` immediately |
| POST | `/api/transactions/db15/batch-from-input` | Start DB15 batch using a file already in `results/Generate Table List/` (body: `{filename}`) |
| GET | `/api/transactions/db15/batch/progress` | Poll for batch lookup progress; `result` is populated once `status` is `"done"` |
| GET | `/api/transactions/db15/debug-screen` | Diagnostic: dump DB15 selection-screen elements |
| GET | `/api/transactions/db15/debug-grid` | Diagnostic: filter DB15 by a table and report grid-read details |
| POST | `/api/transactions/db15/score` | (Re)starts scoring every table/object row for archiving relevance in the background (including the housekeeping-program lookup for zero-candidate tables); returns `{status: "started", total}` immediately. A normal run needs no call to this: the lookup continues into the scoring by itself, and this is for **Retry scoring**. Body `{rows, only_unscored}`: with `only_unscored: true` just the tables the last run could not score are re-scored (from that run's own result, so `rows` may be empty) and merged into it (HTTP 400 if there are none). When done, the scored workbook is saved automatically and `result.saved_path` holds its full path (`result.save_error` if saving failed) |
| GET | `/api/transactions/db15/score/progress` | Poll for scoring progress; `result` (rows, recommended, `unscored: [{table, reason}]`, `message`) is populated once `status` is `"done"` |
| POST | `/api/transactions/db02/top-tables` | Start the "top tables by size" SQL query via DB02's SQL Editor in the background; returns `{status: "started"}` (HTTP 409 if one is running) |
| GET | `/api/transactions/db02/top-tables/progress` | The query's state; `result` has Table Name, Description, Volume (GB), Volume (MB) and `saved_path` / `save_error` for `results/Generate Table List/list_of_tables.xlsx` once `status` is `"done"` |
| GET | `/api/transactions/db02/debug-screen` | Diagnostic: dump DB02 screen elements under a given container |
| GET | `/api/transactions/db02/debug-tree` | Diagnostic: dump a tree control's node keys + display text |
| POST | `/api/transactions/db02/debug-click` | Diagnostic: perform an action on a tree node and dump the result |
| GET | `/api/transactions/db02/debug-probe-run` | Diagnostic: dry-run the whole SQL Editor flow step by step |
| POST | `/api/transactions/header-tables/batch` | Upload an Excel file; starts the SE16N/ARCH_DEF header-table lookup for the top N archiving objects on its last sheet in the background |
| POST | `/api/transactions/header-tables/batch-from-output` | Start the header-table lookup using a file already in `results/Find Archiving Objects for Tables/` (body: `{filename, max_objects}`); the result is saved automatically to `results/Find Header Tables for Archiving Objects/header_tables.xlsx` (`result.saved_path`) |
| GET | `/api/transactions/header-tables/batch/progress` | Poll for header-table lookup progress; `result` is populated once `status` is `"done"` |
| POST | `/api/header-reference/analyze` | Multipart upload: `files` (one or more Excel/PDF/PowerPoint) + `rows` (JSON string of the header-table rows); starts the comparison in the background and returns `{status: "started"}` |
| GET | `/api/header-reference/progress` | The comparison's state; `result` (matches, mismatches, not-in-reference rows, the annotated rows) is set once `status` is `"done"` |
| GET, PUT, DELETE | `/api/ui-state/{key}` | The remembered step of a reference review (`key` is `ref-doc-tables` or `ref-doc-headers`; PUT body `{state: {...}}`), so a screen that is opened again carries on where it was |
| POST | `/api/header-reference/save` | Save the reference-reviewed header tables (body: `{rows, final}`) to `results/Find Header Tables for Archiving Objects/header_tables_with_reference.xlsx`; returns the full path |
| GET | `/api/transactions/se16n/debug-arch-def-screen` | Diagnostic: dump `ARCH_DEF`'s Selection Criteria screen elements |
| GET | `/api/transactions/se16n/debug-arch-def-query` | Diagnostic: query `ARCH_DEF` (or the table given by `table`) for an archiving object and report every step independently |
