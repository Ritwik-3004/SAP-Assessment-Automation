import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import ProgressBar from "./ProgressBar";
import SavedNotice from "./SavedNotice";
import type { OpenItemResult, OpenItemSnapshot } from "../types";

function elapsed(startedSeconds: number, now: number): string {
  const s = Math.max(0, Math.round(now / 1000 - startedSeconds));
  return s >= 60 ? `${Math.floor(s / 60)} min ${s % 60} s` : `${s} s`;
}

export default function OpenItemAnalysisPanel() {
  // ── Input source (default: the objects on the Grouped by Object sheet saved by Find Archiving Objects for Tables) ──
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);

  const [snapshot, setSnapshot] = useState<OpenItemSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  // ── The query box: follows the query that is running until the user picks an object or types ──
  const [selected, setSelected] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [dirty, setDirty] = useState(false);
  const [sending, setSending] = useState(false);
  const [preview, setPreview] = useState<OpenItemResult | null>(null);
  const [now, setNow] = useState(Date.now());

  const active = snapshot?.status === "running";
  const records = snapshot?.records ?? [];
  const focus = selected ?? snapshot?.current?.object ?? null;
  const focusRec = records.find((r) => r.object === focus) ?? null;

  const preferred = (files: string[]) =>
    files.find((f) => f === "archiving_objects_with_reference.xlsx") ??
    files.find((f) => f === "archiving_objects_scored.xlsx") ??
    files.find((f) => f.startsWith("archiving_objects")) ??
    files[0] ??
    null;

  async function fetchOutputFiles() {
    setLoadingFiles(true);
    try {
      const { files: spreadsheets } = await api.getResultFiles("BATCH");
      setOutputFiles(spreadsheets);
      if (!serverFile) setServerFile(preferred(spreadsheets));
    } catch {
      // Non-fatal: the user can still upload a file
    } finally {
      setLoadingFiles(false);
    }
  }

  // On opening the panel (including coming back to it while a run continues in the background)
  useEffect(() => {
    fetchOutputFiles();
    api.openItemProgress().then(setSnapshot).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      api.openItemProgress().then(setSnapshot).catch(() => {});
      setNow(Date.now());
    }, 1500);
    return () => clearInterval(timer);
  }, [active]);

  // The query box shows the query of the object in focus, unless the user is editing it
  const shownQuery = focusRec?.query ?? "";
  useEffect(() => {
    if (!dirty) setDraft(shownQuery);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focus, shownQuery, dirty]);

  // The preview of the focused object's result, refreshed whenever a query finishes
  const previewKey = `${focus}|${focusRec?.state}|${focusRec?.rows}|${snapshot?.completed}`;
  useEffect(() => {
    if (!focus || focusRec?.state !== "done") {
      setPreview(null);
      return;
    }
    api.openItemResult(focus).then(setPreview).catch(() => setPreview(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

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
    if (!serverFile) setServerFile(preferred(outputFiles));
  }

  const canStart = !active && !starting && (mode === "upload" ? !!localFile : !!serverFile);

  async function handleStart(e: React.FormEvent) {
    e.preventDefault();
    if (!canStart) return;
    setStarting(true);
    setError("");
    setSelected(null);
    setDirty(false);
    setPreview(null);
    try {
      await api.openItemStart(mode === "upload" && localFile ? { file: localFile } : { filename: serverFile ?? "" });
      setSnapshot(await api.openItemProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the analysis");
    } finally {
      setStarting(false);
    }
  }

  function pickObject(object: string) {
    setSelected(object);
    setDirty(false);
    setError("");
  }

  async function runQuery(query: string) {
    if (!focus) return;
    setSending(true);
    setError("");
    try {
      await api.openItemRunQuery(focus, query);
      setDirty(false);
      setSnapshot(await api.openItemProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not run the query");
    } finally {
      setSending(false);
    }
  }

  const current = snapshot?.current ?? null;
  const resetQuery = focusRec?.default_query ?? "";
  const runLabel =
    focus && focusRec?.state === "running" ? "Run edited query next" : focusRec?.state === "queued" ? "Use this query" : "Run this query";

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Open Item Analysis</strong> — for each archiving object on the <em>Grouped by Object</em> sheet this looks up the object's open-item
        query in the <em>Open Closed Item details</em> sheet (<code>backend/resources</code>) and runs it in SAP's SQL
        Editor. The sheet's queries list one row per document, so the tool <strong>counts the documents per year, month and
        category</strong> (that is the query you see below, so you can change it). You can see which query is running and
        edit it to get different results — the edited query runs next and replaces that object's result. An object with no
        query in the sheet is skipped with a note. The results are saved automatically in the <em>Open Item Analysis</em> results folder as an Excel file with a Summary sheet
        and <strong>one sheet per archiving object</strong> holding its result. By default the tool reads the <em>Grouped by Object</em> sheet
        (the objects with their tables grouped under them, largest first) saved by <em>Find Archiving Objects for Tables</em>; you can choose another file. Only read-only <code>SELECT</code>{" "}
        queries are accepted. The queries can take a while on large tables, and SAP is busy with them meanwhile.
      </div>

      <form className="tx-form" onSubmit={handleStart}>
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Saved by Find Archiving Objects for Tables</span>
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
                  Nothing saved yet. Run <em>Find Archiving Objects for Tables</em> first, or upload
                  a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {outputFiles.map((name) => (
                    <label key={name} className={`file-radio-item ${serverFile === name ? "selected" : ""}`}>
                      <input
                        type="radio"
                        name="openItemFile"
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
                ✕ Use a saved file
              </button>
            </div>
          )}
        </div>

        {snapshot?.sheet && (
          <p className="tx-hint" style={{ margin: 0 }}>
            {snapshot.sheet.problem
              ? snapshot.sheet.problem
              : `Query sheet: ${snapshot.sheet.objects.length} archiving objects have a query (${snapshot.sheet.objects.join(", ")}).`}
          </p>
        )}

        <button type="submit" className="btn btn-primary" disabled={!canStart}>
          {active ? "Analysis running…" : starting ? "Starting…" : "Start analysis"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}

      {snapshot && snapshot.status !== "idle" && (
        <div style={{ marginTop: 16 }}>
          {active && (
            <>
              <ProgressBar
                mode="determinate"
                completed={snapshot.completed}
                total={snapshot.total}
                label={snapshot.message ?? undefined}
              />
              <div className="action-row" style={{ marginTop: 8 }}>
                <button className="btn btn-secondary" onClick={() => api.openItemCancel().catch(() => {})}>
                  Cancel after the current query
                </button>
              </div>
            </>
          )}
          {snapshot.warnings.map((w, i) => (
            <p key={i} className="tx-error">{w}</p>
          ))}
          {snapshot.client_note && (
            <div className="saved-notice" style={{ marginTop: 12 }}>
              <strong>Client changed to {snapshot.client_note.client}.</strong> The sheet's queries read client{" "}
              {snapshot.client_note.sheet_client}; you are logged on to client {snapshot.client_note.client}, so the tool
              replaced it with {snapshot.client_note.client} in {snapshot.client_note.objects.length} quer
              {snapshot.client_note.objects.length !== 1 ? "ies" : "y"} (the <code>MANDT</code> value). To go back to the
              sheet's client for an object, open <em>View / edit query</em> and click{" "}
              <em>Revert to client {snapshot.client_note.sheet_client}</em>.
            </div>
          )}
          {!active && records.length > 0 && (
            <div className="saved-notice" style={{ marginTop: 12 }}>
              <strong>{snapshot.message ?? "Finished."}</strong>{" "}
              {records.filter((r) => r.state === "done").length} of {records.length} objects have a result.
            </div>
          )}
          <SavedNotice path={snapshot.output_path} />

          {/* The query box */}
          {records.length > 0 && (
            <div className="ta-card" style={{ marginTop: 16 }}>
              <div className="ta-card-header">
                {current ? (
                  <>
                    Running now: <strong>{current.object}</strong>
                    <span className="tx-hint"> · {elapsed(current.started, now)}</span>
                  </>
                ) : (
                  "Query"
                )}
                {focus && focus !== current?.object && (
                  <span className="tx-hint"> · showing {focus}</span>
                )}
              </div>
              <div className="ta-card-body">
                {!focus ? (
                  <p className="tx-hint">Pick an object in the table below to see or edit its query.</p>
                ) : (
                  <>
                    <p className="tx-hint" style={{ margin: 0 }}>
                      {focusRec?.state === "running"
                        ? "This query is running in SAP and cannot be stopped. Edit it and click the button to run your version next."
                        : focusRec?.default_query
                        ? "The query for this object. Edit it to get different results; it replaces this object's result."
                        : "The sheet has no query for this object. Type one to run it, or leave it skipped."}
                    </p>
                    {focusRec?.sheet_client && (
                      <p className="tx-hint" style={{ margin: 0 }}>
                        {focusRec.query_kind === "sheet_client" ? (
                          <>
                            This is the sheet's query with its own client ({focusRec.sheet_client}). You are logged on to client{" "}
                            {focusRec.client}.
                          </>
                        ) : (
                          <>
                            Client changed: the sheet's query reads client {focusRec.sheet_client}; the tool uses{" "}
                            {focusRec.client}, the client you are logged on to.
                          </>
                        )}{" "}
                        <button
                          type="button"
                          className="btn-link"
                          disabled={sending}
                          onClick={() =>
                            runQuery(
                              focusRec.query_kind === "sheet_client" ? focusRec.default_query : focusRec.sheet_query
                            )
                          }
                        >
                          {focusRec.query_kind === "sheet_client"
                            ? `Use client ${focusRec.client} again`
                            : `Revert to client ${focusRec.sheet_client}`}
                        </button>
                      </p>
                    )}
                    <textarea
                      className="oi-query"
                      value={draft}
                      spellCheck={false}
                      rows={12}
                      onChange={(e) => {
                        setDraft(e.target.value);
                        setDirty(true);
                      }}
                      placeholder="SELECT …"
                    />
                    <div className="action-row">
                      <button
                        className="btn btn-primary"
                        onClick={() => runQuery(draft)}
                        disabled={sending || !draft.trim()}
                      >
                        {sending ? "Sending…" : runLabel}
                      </button>
                      {resetQuery && (
                        <button
                          className="btn btn-secondary"
                          onClick={() => {
                            setDraft(resetQuery);
                            setDirty(true);
                          }}
                          disabled={draft === resetQuery}
                        >
                          Reset to the standard query
                        </button>
                      )}
                      {selected && (
                        <button className="btn btn-secondary" onClick={() => { setSelected(null); setDirty(false); }}>
                          Follow the running query
                        </button>
                      )}
                    </div>
                  </>
                )}
              </div>
            </div>
          )}

          {records.length > 0 && (
            <div className="table-wrapper" style={{ marginTop: 16 }}>
              <p className="table-caption">Archiving objects</p>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Archiving object</th>
                      <th>Area</th>
                      <th>Result</th>
                      <th>Rows</th>
                      <th>Query</th>
                      <th>Note</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((r) => (
                      <tr key={r.object} className={r.object === focus ? "selected-row" : ""}>
                        <td>
                          <strong>{r.object}</strong>
                          {r.description && <div className="tx-hint">{r.description}</div>}
                        </td>
                        <td>{r.area || "—"}</td>
                        <td><span className={`ta-status ${r.state}`}>{r.state}</span>{r.queued_edit && <span className="ta-tag" style={{ marginLeft: 6 }}>edit queued</span>}</td>
                        <td>{r.state === "done" ? r.rows : "—"}</td>
                        <td>
                          {r.query_kind === "edited" ? (
                            <span className="ta-tag suggested">edited</span>
                          ) : r.query_kind === "sheet_client" ? (
                            `sheet, client ${r.sheet_client}`
                          ) : r.default_query ? (
                            r.sheet_client ? `sheet, client ${r.client}` : "from sheet"
                          ) : (
                            "—"
                          )}
                        </td>
                        <td className="ref-doc-comment note-wrap">{r.note}</td>
                        <td>
                          <button className="btn-link" onClick={() => pickObject(r.object)}>
                            {r.default_query || r.edited ? "View / edit query" : "Add a query…"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {preview && focus && (
            <div className="table-wrapper" style={{ marginTop: 16 }}>
              <p className="table-caption">
                Result for {focus}
                {preview.total_rows > preview.rows.length ? ` — first ${preview.rows.length} of ${preview.total_rows} rows (all are in the Excel file)` : ` — ${preview.total_rows} rows`}
              </p>
              <div className="table-scroll" style={{ maxHeight: 360 }}>
                <table className="results-table">
                  <thead>
                    <tr>{preview.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                  </thead>
                  <tbody>
                    {preview.rows.map((row, i) => (
                      <tr key={i}>{row.map((v, j) => <td key={j}>{v}</td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
