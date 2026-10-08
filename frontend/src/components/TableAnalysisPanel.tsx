import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import ProgressBar from "./ProgressBar";
import type { TableAnalysisField, TableAnalysisPrompt, TableAnalysisSnapshot } from "../types";

const KIND_TAG: Record<string, string> = {
  date: "date",
  period: "year-month",
  year: "year",
  month: "month",
};

/** A scrollable, filterable list of fields with checkboxes. Must stay a top-level component: defined inside
 *  another component it would be re-created on every render and lose its scroll position. */
function FieldList({
  fields,
  picked,
  onToggle,
  filter,
  onFilter,
}: {
  fields: TableAnalysisField[];
  picked: Set<string>;
  onToggle: (name: string) => void;
  filter: string;
  onFilter: (value: string) => void;
}) {
  const needle = filter.trim().toLowerCase();
  const shown = fields.filter(
    (f) => !needle || f.name.toLowerCase().includes(needle) || f.label.toLowerCase().includes(needle)
  );
  return (
    <>
      {fields.length > 12 && (
        <input
          type="text"
          className="chat-input"
          style={{ resize: "none" }}
          placeholder="Filter fields by name or description…"
          value={filter}
          onChange={(e) => onFilter(e.target.value)}
        />
      )}
      <div className="ta-field-list">
        {shown.length === 0 && <div className="ta-field-row">No field matches.</div>}
        {shown.map((f) => (
          <label key={f.name} className="ta-field-row">
            <input type="checkbox" checked={picked.has(f.name)} onChange={() => onToggle(f.name)} />
            <span className="ta-field-name">{f.name}</span>
            <span className="ta-field-label">{f.label || "—"}</span>
            {f.kind && <span className="ta-tag">{KIND_TAG[f.kind] ?? f.kind}</span>}
            {f.suggested && <span className="ta-tag suggested">suggested</span>}
          </label>
        ))}
      </div>
    </>
  );
}

/** One of the questions the analysis asks for the table it is working on. Keyed by prompt id so its
 *  selections start fresh with every question. */
function PromptCard({
  prompt,
  onAnswer,
  onSkip,
}: {
  prompt: TableAnalysisPrompt;
  onAnswer: (answer: Record<string, unknown>) => void;
  onSkip: () => void;
}) {
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [groupByYear, setGroupByYear] = useState(true);
  const [filter, setFilter] = useState("");

  const toggle = (name: string) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });

  if (prompt.kind === "date_fields") {
    return (
      <div className="ta-card">
        <div className="ta-card-header">{prompt.table} — which date / year / month fields should be analysed?</div>
        <div className="ta-card-body">
          {prompt.note && <p className="tx-error">{prompt.note}</p>}
          <p className="tx-hint">
            These are the date, year and month (posting period) fields of the table. Tick the ones to add to the
            analysis variant (you will be asked about other fields next).
          </p>
          <FieldList
            fields={prompt.fields ?? []}
            picked={picked}
            onToggle={toggle}
            filter={filter}
            onFilter={setFilter}
          />
          {prompt.can_group_by_year && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <input type="checkbox" checked={groupByYear} onChange={(e) => setGroupByYear(e.target.checked)} />
              Group dates by year (first 4 characters) — far fewer rows than one per day
            </label>
          )}
          <div className="action-row">
            <button
              className="btn btn-primary"
              onClick={() => onAnswer({ selected: Array.from(picked), group_by_year: groupByYear })}
            >
              {picked.size > 0 ? `Continue with ${picked.size} field${picked.size !== 1 ? "s" : ""}` : "Continue without a date/year/month field"}
            </button>
            <button className="btn btn-secondary" onClick={onSkip}>Skip this table</button>
          </div>
        </div>
      </div>
    );
  }

  if (prompt.kind === "more_fields") {
    return (
      <div className="ta-card">
        <div className="ta-card-header">{prompt.table} — add other fields to the analysis?</div>
        <div className="ta-card-body">
          {prompt.note && <p className="tx-error">{prompt.note}</p>}
          <p className="tx-hint">
            {prompt.no_date_fields
              ? "This table has no date, year or month fields. "
              : prompt.selected && prompt.selected.length > 0
              ? `Selected so far: ${prompt.selected.join(", ")}. `
              : "No date/year/month field selected. "}
            Other fields such as company code or document type break the counts down further.
            {!prompt.has_other_fields && " (There are no other fields to choose from.)"}
          </p>
          <div className="action-row">
            <button
              className="btn btn-primary"
              disabled={!prompt.has_other_fields}
              onClick={() => onAnswer({ add: true })}
            >
              Yes, choose other fields
            </button>
            <button className="btn btn-secondary" onClick={() => onAnswer({ add: false })}>
              No, run with the selection
            </button>
            <button className="btn btn-secondary" onClick={onSkip}>Skip this table</button>
          </div>
        </div>
      </div>
    );
  }

  if (prompt.kind === "redo_fields") {
    return (
      <div className="ta-card">
        <div className="ta-card-header">{prompt.table} — re-run with additional fields</div>
        <div className="ta-card-body">
          <p className="tx-hint">
            Already analysed (these are kept): <strong>{(prompt.previous ?? []).join(", ")}</strong>. Tick the fields to add;
            the analysis is repeated with all of them and replaces this table's result in the Excel file. Date, year and month
            fields are listed first, then suggested ones.
          </p>
          <FieldList
            fields={prompt.fields ?? []}
            picked={picked}
            onToggle={toggle}
            filter={filter}
            onFilter={setFilter}
          />
          {(prompt.fields ?? []).some((f) => f.kind === "date" || f.kind === "period") && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <input type="checkbox" checked={groupByYear} onChange={(e) => setGroupByYear(e.target.checked)} />
              Group any added dates by year (first 4 characters)
            </label>
          )}
          <div className="action-row">
            <button
              className="btn btn-primary"
              disabled={picked.size === 0}
              onClick={() => onAnswer({ selected: Array.from(picked), group_by_year: groupByYear })}
            >
              {picked.size > 0 ? `Re-run with ${picked.size} more field${picked.size !== 1 ? "s" : ""}` : "Choose at least one field"}
            </button>
            <button className="btn btn-secondary" onClick={onSkip}>Cancel re-run</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="ta-card">
      <div className="ta-card-header">{prompt.table} — choose the other fields</div>
      <div className="ta-card-body">
        <p className="tx-hint">Suggested fields (company code, document type, …) are listed first.</p>
        <FieldList
          fields={[...(prompt.fields ?? [])].sort((a, b) => Number(!!b.suggested) - Number(!!a.suggested))}
          picked={picked}
          onToggle={toggle}
          filter={filter}
          onFilter={setFilter}
        />
        <div className="action-row">
          <button className="btn btn-primary" onClick={() => onAnswer({ selected: Array.from(picked) })}>
            {picked.size > 0 ? `Run with ${picked.size} more field${picked.size !== 1 ? "s" : ""}` : "Run without other fields"}
          </button>
          <button className="btn btn-secondary" onClick={onSkip}>Skip this table</button>
        </div>
      </div>
    </div>
  );
}

export default function TableAnalysisPanel() {
  // ── Input source (default: what Find Header Tables saved) ─────────────────
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);

  const [snapshot, setSnapshot] = useState<TableAnalysisSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [sheetGroupByYear, setSheetGroupByYear] = useState(true);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const active = snapshot?.status === "running" || snapshot?.status === "waiting";

  async function fetchOutputFiles() {
    setLoadingFiles(true);
    try {
      const { files } = await api.getOutputFiles();
      const spreadsheets = files.filter((f) => !f.startsWith("Table analysis"));
      setOutputFiles(spreadsheets);
      if (!serverFile) {
        setServerFile(spreadsheets.find((f) => f.startsWith("header_tables")) ?? spreadsheets[0] ?? null);
      }
    } catch {
      // Non-fatal: the user can still upload a file
    } finally {
      setLoadingFiles(false);
    }
  }

  useEffect(() => {
    fetchOutputFiles();
    // Pick up a run that is already going (e.g. after a page refresh)
    api.tableAnalysisProgress().then(setSnapshot).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Poll while a run is active
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      api.tableAnalysisProgress().then(setSnapshot).catch(() => {});
    }, 1000);
    return () => clearInterval(timer);
  }, [active]);

  function handleLocalFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    if (f) {
      setLocalFile(f);
      setMode("upload");
      setServerFile(null);
    }
  }

  function handleClearUpload() {
    setLocalFile(null);
    setMode("folder");
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!serverFile) setServerFile(outputFiles.find((f) => f.startsWith("header_tables")) ?? outputFiles[0] ?? null);
  }

  const canStart = !active && !starting && (mode === "upload" ? !!localFile : !!serverFile);

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!canStart) return;
    setStarting(true);
    setError("");
    try {
      await api.tableAnalysisStart(
        mode === "upload" && localFile ? { file: localFile } : { filename: serverFile ?? "" },
        sheetGroupByYear
      );
      setSnapshot(await api.tableAnalysisProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the analysis");
    } finally {
      setStarting(false);
    }
  }

  async function answer(a: Record<string, unknown>) {
    if (!snapshot?.prompt) return;
    try {
      await api.tableAnalysisAnswer(snapshot.prompt.id, a);
      setSnapshot(await api.tableAnalysisProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not send the answer");
    }
  }

  async function redoTable(table: string) {
    setError("");
    try {
      await api.tableAnalysisRedo(table);
      setSnapshot(await api.tableAnalysisProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the re-run");
    }
  }

  async function skipTable() {
    await api.tableAnalysisSkip().catch(() => {});
  }

  async function stopWaiting(table: string) {
    await api.tableAnalysisStopWaiting(table).catch(() => {});
    setSnapshot(await api.tableAnalysisProgress().catch(() => snapshot as TableAnalysisSnapshot));
  }

  async function cancelRun() {
    await api.tableAnalysisCancel().catch(() => {});
  }

  async function handleDownload() {
    try {
      const blob = await api.tableAnalysisDownload();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "Table analysis.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  }

  const records = snapshot?.records ?? [];

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Table Analysis</strong> — runs a table analysis (TAANA) for each header table. By default it uses the
        file saved by <em>Find Header Tables</em>; you can also upload a different one. For each table the tool first
        looks in the <em>Fields for TAANA</em> sheet: if the table is listed there, its fields are used and you are not
        asked anything. Only for tables that aren't in the sheet does it read the table's fields and ask you to choose the
        date, year and month fields, and whether to add other fields (such as company code or document type). When the
        analysis is done you can re-run any table with additional fields. It then creates an ad hoc analysis variant, runs it in the
        background in SAP, and copies the result into <code>output/Table analysis.xlsx</code> (one sheet per
        table) before moving on to the next table. You answer the questions for all tables in a row: each table's analysis starts in the background as soon as you've chosen its fields, and the results are added to the Excel file as the jobs finish. Don't use SAP at the same time while it runs.
      </div>

      <form className="tx-form" onSubmit={handleStart}>
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Output folder</span>
                <button
                  type="button"
                  className="btn-refresh"
                  onClick={fetchOutputFiles}
                  disabled={loadingFiles || active}
                  title="Refresh file list"
                >
                  ↻
                </button>
              </div>
              {loadingFiles ? (
                <p className="tx-hint">Loading…</p>
              ) : outputFiles.length === 0 ? (
                <p className="tx-hint">
                  No Excel files in the output folder. Run <em>Find Header Tables</em> first and save the result, or
                  upload a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {outputFiles.map((name) => (
                    <label key={name} className={`file-radio-item ${serverFile === name ? "selected" : ""}`}>
                      <input
                        type="radio"
                        name="analysisFile"
                        value={name}
                        checked={serverFile === name}
                        onChange={() => setServerFile(name)}
                        disabled={active}
                      />
                      <span className="file-radio-name">📄 {name}</span>
                    </label>
                  ))}
                </div>
              )}
              <div className="file-picker-or">
                <span className="or-divider">or</span>
                <label className="btn btn-secondary upload-trigger">
                  Upload a different file
                  <input
                    type="file"
                    accept=".xlsx,.xls"
                    onChange={handleLocalFileChange}
                    style={{ display: "none" }}
                    ref={fileInputRef}
                    disabled={active}
                  />
                </label>
              </div>
            </>
          ) : (
            <div className="uploaded-file-row">
              <span className="file-icon-lg">📄</span>
              <span className="uploaded-file-name">{localFile?.name}</span>
              <button type="button" className="btn-clear-upload" onClick={handleClearUpload} disabled={active}>
                ✕ Use output folder
              </button>
            </div>
          )}
        </div>

        {snapshot?.fields_sheet && (
          <p className="tx-hint" style={{ margin: 0 }}>
            {snapshot.fields_sheet.problem
              ? snapshot.fields_sheet.problem
              : `Fields for TAANA sheet: ${snapshot.fields_sheet.tables} tables listed — those run without questions.`}
          </p>
        )}
        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <input
            type="checkbox"
            checked={sheetGroupByYear}
            onChange={(e) => setSheetGroupByYear(e.target.checked)}
            disabled={active}
          />
          Group date fields by year for tables taken from the sheet (far fewer rows than one per day)
        </label>

        <button type="submit" className="btn btn-primary" disabled={!canStart}>
          {active ? "Analysis running…" : starting ? "Starting…" : "Start analysis"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}

      {snapshot && snapshot.status !== "idle" && (
        <div style={{ marginTop: 16 }}>
          {active && snapshot.mode === "redo" && (
            <>
              <div className="ref-doc-analyzing">
                <span className="ref-doc-spinner" />
                {snapshot.message ?? "Re-running with additional fields…"}
              </div>
            </>
          )}
          {active && snapshot.mode !== "redo" && (
            <>
              <ProgressBar
                mode="determinate"
                completed={snapshot.asked}
                total={snapshot.total}
                label={
                  snapshot.interactive_done
                    ? `Questions: all ${snapshot.total} tables answered`
                    : `Questions: ${snapshot.asked} of ${snapshot.total} tables answered` +
                      (snapshot.current ? ` — ${snapshot.current.table}: ${snapshot.message ?? snapshot.current.step}` : "")
                }
              />
              <div style={{ height: 8 }} />
              <ProgressBar
                mode="determinate"
                completed={snapshot.completed}
                total={snapshot.total}
                label={
                  `Analyses: ${snapshot.completed} of ${snapshot.total} finished` +
                  (snapshot.running.length > 0 ? ` · ${snapshot.running.length} running in SAP (${snapshot.running.join(", ")})` : "")
                }
              />
              {snapshot.interactive_done && snapshot.message && <p className="tx-hint" style={{ marginTop: 6 }}>{snapshot.message}</p>}
              <div className="action-row" style={{ marginTop: 8 }}>
                {!snapshot.interactive_done && (
                  <button className="btn btn-secondary" onClick={skipTable}>Skip current table</button>
                )}
                <button className="btn btn-secondary" onClick={cancelRun}>Cancel</button>
              </div>
            </>
          )}

          {snapshot.prompt && (
            <PromptCard
              key={snapshot.prompt.id}
              prompt={snapshot.prompt}
              onAnswer={answer}
              onSkip={async () => {
                await skipTable();
                setSnapshot(await api.tableAnalysisProgress());
              }}
            />
          )}

          {snapshot.warnings.map((w, i) => (
            <p key={i} className="tx-error">{w}</p>
          ))}

          {snapshot.status === "error" && <p className="tx-error">{snapshot.message}</p>}

          {records.length > 0 && (
            <div className="table-wrapper" style={{ marginTop: 16 }}>
              <p className="table-caption">Tables processed</p>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr><th>Table</th><th>Result</th><th>Fields analysed</th><th>Result rows</th><th>Note</th><th></th></tr>
                  </thead>
                  <tbody>
                    {records.map((r) => (
                      <tr key={r.table}>
                        <td><strong>{r.table}</strong></td>
                        <td><span className={`ta-status ${r.state}`}>{r.state}</span></td>
                        <td>
                          {r.fields.join(", ") || "—"}
                          {r.source === "sheet" && <span className="ta-tag" style={{ marginLeft: 6 }}>from sheet</span>}
                          {r.source === "re-run" && <span className="ta-tag" style={{ marginLeft: 6 }}>re-run</span>}
                        </td>
                        <td>{r.state === "done" ? r.rows : "—"}</td>
                        <td>{r.note}</td>
                        <td>
                          {r.state === "running" && (
                            <button className="btn-link" onClick={() => stopWaiting(r.table)}>Stop waiting</button>
                          )}
                          {r.state === "done" && snapshot.status === "done" && (
                            <button className="btn-link" onClick={() => redoTable(r.table)}>Add more fields…</button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {snapshot.status === "done" && (
            <>
              <div className="saved-notice">
                {snapshot.message} Results are in <code>{snapshot.output_path ?? "output/Table analysis.xlsx"}</code>
              </div>
              {snapshot.output_path && (
                <div className="action-row" style={{ marginTop: 8 }}>
                  <button className="btn btn-primary" onClick={handleDownload}>Download Excel</button>
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}
