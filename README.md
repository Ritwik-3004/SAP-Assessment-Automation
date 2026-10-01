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
(housekeeping-program lookup), `grouping.py` (Grouped by Object), `object_descriptions.py` (object descriptions), `chat.py` (chat
assistant), `reference_doc.py` (reference document analysis), `progress.py` (progress
polling), `debug_sap_for_me.py` (manual scraper diagnostics).

## Project status

| Area | Status |
|---|---|
| Generate Table List (DB02), Find Archiving Objects (DB15), table sizes | Working |
| Score & Recommend Objects (Claude, grounded in the DVM Guide) | Working |
| Housekeeping program lookup — DVM Guide stage | Working |
| Housekeeping program lookup — SAP for Me stage | Working in `debug_sap_for_me.py` (COSP → `RK_PLAN_DEL_ZERO_RECORDS`); **not yet verified through a full scoring run in the app**. Needs Chrome for Testing + IT approval. SAP Community fallback **not implemented**. |
| Grouped by Object sheet | Working (no Rationale column — it is on the Recommended sheet) |
| Object descriptions always filled (run → remembered list → SAP → AI-suggested) | Working; verified live (override from a reference document, SAP lookup of the description) and offline with fake SAP/AI. The SAP stage reads table `ARCH_TXT`, whose layout was confirmed live with `debug-arch-def-query?archiving_object=FI_DOCUMNT&table=ARCH_TXT`; the other stages guarantee a value even if that lookup fails — see [Object descriptions](#object-descriptions) |
| Find Header Tables (SE16N/`ARCH_DEF`) | Working for objects with one top-level segment |
| Header tables: DVM Guide + SAP for Me resolution of ambiguous objects, and reference-document review | Ran live once on ~20 objects; most resolved from ARCH_DEF, the rest via DVM Guide / SAP for Me. That run exposed objects left blank (no ARCH_DEF entries) and a search failing on `/` in namespaced names — both fixed since (every object now gets a header table; see below), checked offline with fake SAP/AI/browser sessions and then **re-tested live on the same ~20 objects**. Low-confidence rows are best guesses to be confirmed by the reference document |
| Chat assistant over scored results | Implemented; documented from the code and not run end to end — see [Chat assistant](#chat-assistant) for known gaps |
| AI model switch (Claude or Groq free tier) | Working with a live Groq key; also checked offline with fake clients (all steps, rate limits, tool calls). Groq answer quality vs Claude **not yet compared** — see [AI model selection](#ai-model-selection-claude-or-groq) |
| Reference document analysis (Excel / PDF / PowerPoint) | Implemented; frontend builds cleanly, not yet run end to end against a real document — see [Reference document analysis](#reference-document-analysis) |

## Prerequisites

| Requirement | Notes |
|---|---|
| SAP GUI for Windows | Must be installed (standard SAP Logon) |
| SAP GUI Scripting enabled (client) | Options → Accessibility & Scripting → Scripting tab → **Enable Scripting** |
| SAP GUI Scripting allowed (server) | Profile parameter `sapgui/user_scripting = TRUE` (RZ11, set by Basis) and authorization object `S_SCR` for your user. If Connect times out while the login window looks normal, check this first. |
| Python 3.11+ | `python --version`. The project's `.venv` is **32-bit** Python 3.12, which suits the SAP GUI COM scripting (and is why Selenium is used instead of Playwright). |
| Node.js 18+ | `node --version` |
| `ANTHROPIC_API_KEY` | In `backend/.env`; needed for every AI step while Claude is the selected model |
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
detail documented here for developers, not exposed in the app itself. (The one
exception is the [Chat assistant](#chat-assistant): users can ask it questions that
Claude answers by running DB15, AOBJ, SE16N, SE11, TAANA or SARA on their behalf, and its
replies always name the transaction it queried.)

## Look and feel

The UI uses the Deloitte Green palette (charcoal header with the green line, green actions,
Open Sans). All colours are tokens at the top of `frontend/src/App.css`. Two rules to keep
when changing styles: Deloitte Green (`--primary`, `#86BC25`) is a *fill* colour, so text on it
is black (`--primary-text`) because white fails contrast, and green used *as text* on a light
background must be `--accent-text` (`#4C7A09`). The font is self-hosted through the
`@fontsource/open-sans` npm package (Latin only, weights 300/400/600/700), so it also works on
machines with no internet access.

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
2. **SAP for Me portal search (implemented):** for a table the DVM Guide doesn't
   cover (or covers but names no program for), `backend/sap_for_me.py` drives a
   headless Chrome browser (via Selenium) to search SAP for Me for `"<table name>
   housekeeping program"`, keeps only **SAP Knowledge Base Article** and **SAP Note**
   results (a SAP Community fallback is not implemented yet), reads the top 3, and
   sends their text to Claude to extract a specific
   program, grounded and cited the same way the DVM Guide path is. This requires SAP for
   Me sign-in credentials — enter and save them once in the sidebar's "SAP for Me
   Credentials" panel (stored in `sap_for_me_credentials.json` at the project root,
   git-ignored, same plaintext-JSON pattern as `sap_credentials.json`); every scoring
   run that needs this fallback logs in once and reuses that single browser session
   across every table needing it, since logging in per table would be far too slow.
   Requires **Chrome for Testing** and its matching chromedriver (see setup below).
   Selenium, not Playwright, specifically because this backend's virtualenv is 32-bit
   Python (for the SAP GUI COM scripting) and Playwright's `greenlet` dependency has no
   prebuilt wheel for that. To debug/iterate on the portal's selectors directly, run
   `python backend/debug_sap_for_me.py TABLE_NAME` — it runs headed (visible browser)
   and prints what it found at each step.

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
   4. Verify with `python backend/debug_sap_for_me.py <TABLE_NAME>` (set
      `SAP_FOR_ME_HEADLESS=false` to watch it).

   If the browser can't start (paths unset or wrong, or blocked by policy — the launch
   fails with `session not created: DevToolsActivePort file doesn't exist`), the lookup
   fails gracefully: the window is closed, scoring still completes with the DVM Guide
   results, and the affected tables get a blank Housekeeping Program with a Rationale such
   as "SAP for Me sign-in failed: could not start the browser (…)". On a failed sign-in
   the scraper also writes `output/sap_for_me_debug.png` and `.txt` (screenshot, page
   text, console errors, failed requests) to help diagnose it. Notes on the live portal:
   it shows a cookie dialog (the scraper clicks "Deny All"), and article pages load slowly.

A table with neither an archiving object nor a housekeeping program still gets a row in
every result, with both columns blank and a Rationale explaining that nothing was found
(or why the SAP for Me lookup couldn't run, e.g. credentials not configured or the
browser couldn't start).

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
- The sheet deliberately has no Rationale column — it's already on the "Recommended"
  sheet, and repeating it per group member here would just be noise.

## Reference document analysis

After scoring, a **Reference Document Analysis** panel appears (above the chat box) in
**Find Archiving Objects for Tables** (`frontend/src/components/ReferenceDocPanel.tsx`).
It lets the user cross-check the recommendations against a document from past project
experience — Excel (`.xlsx`/`.xls`), PDF, or PowerPoint (`.pptx`/`.ppt`).

**Priority is unchanged:** the DVM Guide comes first, then SAP for Me, and the reference
document last. The original recommendation is never overwritten automatically — where the
reference document disagrees, the difference is recorded in a **Comments** column and the
user decides what to do.

Flow:

1. Click **Upload Reference Document**. The backend (`backend/reference_doc.py`,
   `POST /api/reference-doc/analyze`) extracts the document's text (`openpyxl` for Excel,
   `pypdf` for PDF, `python-pptx` for PowerPoint) and asks the selected AI model to pull out
   explicit table → archiving object pairs. Only mappings stated in the document are used;
   nothing is guessed. The first ~14,000 characters of the document are sent to Claude.
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
   **Show Grouped by Object** in the preview groups the final list exactly as in the results
   above (same grouping, cumulative sizes and ordering), so you can check the groups after
   your overrides before saving.
5. **Save to output folder** writes `output/archiving_objects_with_reference.xlsx` (sheets:
   "All Scored Objects", "Recommended (with Ref Doc)" — which includes Comments — and
   **"Grouped by Object"**, built from the final list after your overrides, with the same
   columns, order and merged group cells as in `archiving_objects_scored.xlsx`). Grouped by
   Object is the last sheet on purpose: Find Header Tables reads an output file's last sheet,
   so this file can be used as its input too, and then reflects the SME corrections.
   **Download Excel** gives the same file in the browser. **Back to comparison** returns to the
   previous step.

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
that step takes a few seconds longer when objects are unknown). Saving and downloading only use
layers 1 and 2 to fill any remaining gap, so they never call SAP or the model. In the Grouped by
Object sheet a group shows the real description in preference to an AI-suggested one.

Requires `ANTHROPIC_API_KEY`. PowerPoint support needs `python-pptx` (now in
`requirements.txt`; run `pip install -r requirements.txt` after pulling).

**Known gaps:** the comparison only checks the recommended list, not every candidate;
overrides are applied in the panel's own preview and are **not** pushed back into the main
scored results or the chat's view of them; very long documents are truncated before being
sent to Claude; scanned (image-only) PDFs yield no text and are rejected.

## AI model selection (Claude or Groq)

The sidebar's **AI Model** panel chooses which model every AI step uses — scoring, the
housekeeping lookup (DVM Guide and SAP for Me stages), reference-document analysis and the
chat assistant. There is nothing to change per step: pick a model once and all of them
follow it. The header shows the active model (e.g. "AI: Groq · GPT-OSS 120B").

- **Claude** (default) — `claude-haiku-4-5` (`SCORING_MODEL`), billed to your Anthropic
  account; needs `ANTHROPIC_API_KEY` in `backend/.env`. Behaves exactly as before.
- **Groq (free tier)** — pick **GPT-OSS 120B** or **Qwen 3.8 27B**. The Groq API key is
  **not** entered in the app: put `GROQ_API_KEY=gsk_...` in `backend/.env` and restart the
  backend (the panel warns if it is missing). **Save** applies your choice (stored in
  `llm_settings.json`, git-ignored, which holds no keys); **Test** makes one tiny call to
  check the key and model.

How it works: `backend/llm.py` is the only module that talks to an AI provider. The other
modules call `llm.structured()` (JSON answers: scoring, housekeeping, SAP for Me),
`llm.text()` (reference document) and `llm.run_tool_loop()` (chat, with the SAP tools); the
selection is read from `llm_settings.json` on each call. A scoring run reads it once at the
start, so switching mid-run can't mix two models in one result set. Endpoints:
`GET/POST /api/llm/settings`, `GET /api/llm/usage`, `POST /api/llm/test`.

**What to expect on the Groq free tier** (limits per model, from Groq's docs: 30 requests/min,
**8,000 tokens/min**, 1,000 requests/day, **200,000 tokens/day**):
- The per-minute token cap is the real limit. Groq mode paces itself, waits out per-minute
  limits automatically (the progress bar says "Waiting for the Groq rate limit…"), runs 2
  requests at a time instead of 8, and uses smaller prompts (shorter DVM Guide excerpts, SAP
  for Me articles and reference-document text; fewer tables and history in chat).
- Scoring runs at roughly 2–3 tables per minute, and the daily token cap allows very roughly
  60–90 scored tables per day. A large batch will not finish in one day. Chat is usable only
  lightly (about one tool round per minute).
- When a **daily** limit is reached, the run **stops with a clear message** — it never
  silently switches to Claude. Switch the model in the panel to continue. The panel shows
  today's requests and tokens used (counted locally in `llm_usage.json`).
- Answers can differ from Claude's: the prompts were written and tuned on Claude Haiku.
  Compare both models on the same ~10 tables before relying on Groq results.
- **Data handling:** in Groq mode, table names, descriptions and sizes, DVM Guide excerpts,
  SAP for Me article text, uploaded reference documents and (via chat) SE16N/TAANA table
  rows are sent to Groq instead of Anthropic. Check this against the client's data-handling
  rules and Groq's free-tier data terms first.

Verification status: the Groq path works with a live key (confirmed by the team after the
first run). The request shape, strict JSON-schema output, retries, daily-limit abort and
tool calls were also checked offline with fake clients. Still open: how Groq's answers
compare with Claude's on the same tables, and Qwen 3.8's exact reasoning behaviour and the
free tier's admission rules under a long batch — watch the first big run.

## Chat assistant

Once **Find Archiving Objects for Tables** has scored results, an "Ask about these
results" chat box appears under them (`frontend/src/components/ChatPanel.tsx`, shown by
`BatchArchivingPanel.tsx`). Users can ask follow-up questions about the scored results,
check live SAP data, or ask it to change a result; four example questions are offered as
buttons. It uses whichever AI model is selected in the sidebar (see [AI model selection](#ai-model-selection-claude-or-groq)).

How it works (`backend/chat.py`, endpoint `POST /api/chat`):

1. The frontend sends the message, the scored rows, the recommended rows and the chat
   history. The backend gives Claude a summary of the results (best object per table, up
   to 60 tables) and a set of tools, and lets it make up to 8 rounds of tool calls per
   message.
2. Claude picks a tool based on the question:

   | Tool | SAP transaction | Used for |
   |---|---|---|
   | `lookup_archiving_objects` | DB15 | Which archiving objects cover a table |
   | `check_archiving_object` | AOBJ | Whether an object name exists, or listing by prefix (`MM_*`) |
   | `browse_table_contents` | SE16N | Actual rows in a table (default 50, max 200; Claude sees at most 10 rows and 8 columns) |
   | `get_table_definition` | SE11 | A table's fields, types and key fields (first 30 fields) |
   | `analyze_table` | TAANA | Row counts, size, archiving statistics |
   | `get_archiving_sessions` | SARA | Whether and when an object has been run (latest 10 sessions) |
   | `update_table_result` | — (in memory) | Change a table's archiving object, score or rationale |

3. For `update_table_result`, the backend edits a copy of the rows, recomputes the
   Recommended list (highest score per table) and returns both; the frontend swaps them
   into the preview without re-running scoring.

4. **Source attribution:** whenever Claude uses a SAP tool, its reply states which
   transaction it queried (e.g. "Checked in AOBJ: …", "DB15 shows …"), so users can see an
   answer came from live SAP data rather than the model's own recollection. This is a rule
   in the system prompt in `backend/chat.py`.

The SAP tools use the shared SAP GUI session, so SAP must be connected.

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
- Edits live only in the browser's state until the results are saved or downloaded.
- There is no guard against asking a live-SAP question while a DB15 batch is running;
  both drive the same SAP GUI session.
- SE16N and TAANA results (real table rows from the client system) are sent to the
  Anthropic API as part of the conversation — check this against the client's data-handling
  rules before using those tools.
- The scored rows sent to the chat have no Rationale field, so Claude never sees the
  reasons behind the scores.

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

Scoring has one extra phase after the bar reaches 100%: the housekeeping-program lookup
for tables with no archiving object. If any table needs the SAP for Me stage, the bar
stays full while its message cycles through "Searching SAP for Me for TABLE (i of n)…"
(`housekeeping.find_housekeeping_programs(on_progress=…)`), since each table involves
real browser navigation and can take a while.

`POST /api/transactions/db02/top-tables` is unchanged (still a single blocking
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
      the Guide's (Source `SAP for Me` or `DVM Guide + SAP for Me`).
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
   `DVM Guide`, `SAP for Me`, …), **Confidence** (High / Medium / Low) and **Comments**.
   Click **Save to output folder** to write `output/header_tables.xlsx`, or **Download
   Excel** for the file directly. Resolving ambiguous objects takes extra time (an AI call
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
   `Reference document`, Confidence `High`), then **Save to output folder**
   (`output/header_tables_with_reference.xlsx`: sheets "Header Tables" and "Header Tables
   (with Reference)") or **Download Excel**. Backend: `analyze_header_reference()` in
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

## Session persistence

The tool keeps the SAP session alive in the backend process — closing or refreshing the browser tab does **not** disconnect from SAP. On page load the frontend calls `GET /api/sap/info`; if the backend is still connected it restores the connected state (system name, username) without requiring the user to re-enter credentials or re-authenticate.

If the SAP session is still live but the browser shows "Not Connected", click **Connect** with the same details — the backend detects the already-authenticated session, navigates back to the SAP main menu to ensure a clean screen state, and marks the session as connected without logging in again.

## Saved connection details

Click **Save** (next to **Connect** in the login form) to persist all connection fields — including the password — to `sap_credentials.json` at the project root. The next time the page loads (or the backend restarts), the form is pre-filled automatically from that file, so you only need to click **Connect**.

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

# Only needed if you select Groq in the app's AI Model panel
# GROQ_API_KEY=gsk_...

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
| GET | `/api/sap/credentials` | Return saved connection details from `sap_credentials.json` |
| POST | `/api/sap/credentials` | Save connection details (including password) to `sap_credentials.json` |
| GET | `/api/sap-for-me/credentials` | Return saved SAP for Me sign-in details from `sap_for_me_credentials.json` |
| POST | `/api/sap-for-me/credentials` | Save SAP for Me email + password to `sap_for_me_credentials.json` |
| GET | `/api/llm/settings` | Active AI provider/model, the Groq models offered and Groq's free-tier limits (API keys are never returned, only whether each is set in `backend/.env`) |
| POST | `/api/llm/settings` | Choose the AI model for every AI step (body: `{provider: "anthropic"\|"groq", model}`); API keys come from `backend/.env`, not this call |
| GET | `/api/llm/usage` | Today's request/token count for the active model |
| POST | `/api/llm/test` | One tiny call to check the saved key and model work |
| POST | `/api/chat` | One chat turn over scored results (body: `{message, scored_rows, recommended, history}`); returns `{reply, updated_rows, updated_recommended}` — the last two are `null` unless Claude changed a result |
| GET | `/api/files/input` | List `.xlsx`/`.xls` files in the `input/` folder, newest first |
| GET | `/api/files/output` | List `.xlsx`/`.xls` files in the `output/` folder, newest first |
| POST | `/api/files/input/save` | Save table list rows to `input/list_of_tables.xlsx` |
| POST | `/api/files/output/save-archiving` | Save archiving-objects rows to `output/archiving_objects_by_table.xlsx` |
| POST | `/api/files/output/save-scored` | Save scored rows + recommended list + grouped list to `output/archiving_objects_scored.xlsx` |
| POST | `/api/files/output/save-header-tables` | Save header-table rows to `output/header_tables.xlsx` |
| POST | `/api/reference-doc/analyze` | Multipart upload: `file` (Excel/PDF/PowerPoint) + `recommended` (JSON string of the recommended rows); returns matches, mismatches, not-in-reference rows and the annotated recommended list |
| POST | `/api/reference-doc/save` | Save the reference-annotated results (body: `{rows, recommended}`) to `output/archiving_objects_with_reference.xlsx` |
| POST | `/api/reference-doc/export` | Same workbook as a browser download |
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
| POST | `/api/transactions/header-tables/export` | Export header-table rows (JSON) to a downloadable `.xlsx` (Archiving Object, Header Table, Source, Confidence, Comments) |
| POST | `/api/header-reference/analyze` | Multipart upload: `file` (Excel/PDF/PowerPoint) + `rows` (JSON string of the header-table rows); returns matches, mismatches, not-in-reference rows and the annotated rows |
| POST | `/api/header-reference/save` | Save the reference-reviewed header tables (body: `{rows, final}`) to `output/header_tables_with_reference.xlsx` |
| POST | `/api/header-reference/export` | Same workbook as a browser download |
| GET | `/api/transactions/se16n/debug-arch-def-screen` | Diagnostic: dump `ARCH_DEF`'s Selection Criteria screen elements |
| GET | `/api/transactions/se16n/debug-arch-def-query` | Diagnostic: query `ARCH_DEF` (or the table given by `table`) for an archiving object and report every step independently |
