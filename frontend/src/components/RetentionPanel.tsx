import { Fragment, useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import ProgressBar from "./ProgressBar";
import SavedNotice from "./SavedNotice";
import CompletenessNotice from "./CompletenessNotice";
import SendToModelDialog from "./SendToModelDialog";
import type { RetentionEntry, RetentionSnapshot } from "../types";

const DOC_TYPES = ".xlsx,.xlsm,.pdf,.pptx,.docx,.csv,.txt,.md";

const confidenceClass = (c: string) => (c === "high" ? "oa-chip ok" : c === "low" ? "oa-chip muted" : "oa-chip");

export default function RetentionPanel() {
  // ── The archiving objects (default: the Grouped by Object sheet saved by Find Archiving Objects for Tables) ──
  const [savedFiles, setSavedFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);
  const objectsInputRef = useRef<HTMLInputElement>(null);

  // ── The retention document(s) ──
  const [documents, setDocuments] = useState<File[]>([]);
  const [confirming, setConfirming] = useState(false);
  const docInputRef = useRef<HTMLInputElement>(null);

  const [snapshot, setSnapshot] = useState<RetentionSnapshot | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [entries, setEntries] = useState<RetentionEntry[] | null>(null);
  const [showEntries, setShowEntries] = useState(false);

  const active = snapshot?.status === "running";
  const records = snapshot?.records ?? [];

  const preferred = (files: string[]) =>
    files.find((f) => f === "archiving_objects_with_reference.xlsx") ??
    files.find((f) => f === "archiving_objects_scored.xlsx") ??
    files.find((f) => f.startsWith("archiving_objects")) ??
    files[0] ??
    null;

  async function fetchSavedFiles() {
    setLoadingFiles(true);
    try {
      const { files } = await api.getResultFiles("BATCH");
      setSavedFiles(files);
      if (!serverFile) setServerFile(preferred(files));
    } catch {
      // Non-fatal: the user can still upload a file
    } finally {
      setLoadingFiles(false);
    }
  }

  // On opening the panel (including coming back to it while a run continues in the background)
  useEffect(() => {
    fetchSavedFiles();
    api.retentionProgress().then(setSnapshot).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      api.retentionProgress().then(setSnapshot).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [active]);

  // What was read from the document(s): fetched when the user opens it, and again when a new run finishes
  useEffect(() => {
    if (!showEntries || !snapshot || snapshot.entry_count === 0) return;
    api.retentionEntries().then((r) => setEntries(r.entries)).catch(() => setEntries(null));
  }, [showEntries, snapshot?.entry_count, snapshot?.status]);

  function handleObjectsFile(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0] ?? null;
    if (f) {
      setLocalFile(f);
      setMode("upload");
      setServerFile(null);
    }
  }

  function clearObjectsUpload() {
    setLocalFile(null);
    setMode("folder");
    if (objectsInputRef.current) objectsInputRef.current.value = "";
    if (!serverFile) setServerFile(preferred(savedFiles));
  }

  function handleDocuments(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length) setDocuments(picked);
  }

  function removeDocument(index: number) {
    setDocuments((docs) => docs.filter((_, i) => i !== index));
    if (docInputRef.current) docInputRef.current.value = "";
  }

  const haveObjects = mode === "upload" ? !!localFile : !!serverFile;
  const canStart = !active && !starting && haveObjects && documents.length > 0;

  async function startAnalysis() {
    setConfirming(false);
    setStarting(true);
    setError("");
    setOpen(null);
    setEntries(null);
    try {
      await api.retentionStart(mode === "upload" && localFile ? { file: localFile } : { filename: serverFile ?? "" }, documents);
      setSnapshot(await api.retentionProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the analysis");
    } finally {
      setStarting(false);
    }
  }

  const doneCount = records.filter((r) => r.state === "done").length;
  const finished = snapshot && (snapshot.status === "done" || snapshot.status === "error") && records.length > 0;

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Retention Requirements</strong> — upload a retention reference document (a records retention schedule) and the
        tool looks for the <strong>retention period of each recommended archiving object</strong> in it. A retention schedule
        lists business records, not SAP objects, so for each object the AI model picks the schedule entry whose records the
        object holds — or says that none fits. The period is <strong>copied from the document</strong>, never written by the
        model, and every result has a <strong>Reference</strong> note saying where it was found (the file, the table row or page,
        the series code). Tables in Word, Excel and CSV files are read exactly, row by row; PDFs, slides and plain text are read
        by the AI model, and the reference says so. By default the tool uses the objects on the <em>Grouped by Object</em> sheet
        saved by <em>Find Archiving Objects for Tables</em>; you can choose another file. The results are saved automatically in
        the <em>Retention Requirements</em> results folder. Please check the matches, especially the ones marked medium or low
        confidence.
      </div>

      <form
        className="tx-form"
        onSubmit={(e) => {
          e.preventDefault();
          if (canStart) setConfirming(true);
        }}
      >
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Archiving objects — saved by Find Archiving Objects for Tables</span>
                <button
                  type="button"
                  className="btn-refresh"
                  onClick={fetchSavedFiles}
                  disabled={loadingFiles || active}
                  title="Refresh file list"
                >
                  ↻
                </button>
              </div>
              {loadingFiles ? (
                <p className="tx-hint">Loading…</p>
              ) : savedFiles.length === 0 ? (
                <p className="tx-hint">
                  Nothing saved yet. Run <em>Find Archiving Objects for Tables</em> first, or upload a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {savedFiles.map((name) => (
                    <label key={name} className={`file-radio-item ${serverFile === name ? "selected" : ""}`}>
                      <input
                        type="radio"
                        name="retentionObjectsFile"
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
                    onChange={handleObjectsFile}
                    style={{ display: "none" }}
                    ref={objectsInputRef}
                    disabled={active}
                  />
                </label>
              </div>
            </>
          ) : (
            <div className="uploaded-file-row">
              <span className="file-icon-lg">📄</span>
              <span className="uploaded-file-name">{localFile?.name}</span>
              <button type="button" className="btn-clear-upload" onClick={clearObjectsUpload} disabled={active}>
                ✕ Use a saved file
              </button>
            </div>
          )}
        </div>

        <div className="file-picker">
          <div className="file-picker-header">
            <span className="file-picker-title">Retention reference document</span>
          </div>
          {documents.length === 0 ? (
            <p className="tx-hint">
              Choose the retention schedule: Excel, Word, PDF, PowerPoint, CSV or text. You can choose several documents.
            </p>
          ) : (
            <ul className="modal-files" style={{ margin: "0 0 8px" }}>
              {documents.map((d, i) => (
                <li key={d.name + d.size}>
                  <span>📄 {d.name}</span>
                  <button type="button" className="btn-link" onClick={() => removeDocument(i)} disabled={active}>
                    remove
                  </button>
                </li>
              ))}
            </ul>
          )}
          <label className="btn btn-secondary upload-trigger">
            {documents.length === 0 ? "Choose retention document(s)" : "Choose different document(s)"}
            <input
              type="file"
              multiple
              accept={DOC_TYPES}
              onChange={handleDocuments}
              style={{ display: "none" }}
              ref={docInputRef}
              disabled={active}
            />
          </label>
        </div>

        <button type="submit" className="btn btn-primary" disabled={!canStart}>
          {active ? "Analysis running…" : starting ? "Starting…" : "Find the retention periods"}
        </button>
      </form>

      {confirming && <SendToModelDialog files={documents} onCancel={() => setConfirming(false)} onConfirm={startAnalysis} />}

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
                <button className="btn btn-secondary" onClick={() => api.retentionCancel().catch(() => {})}>
                  Cancel
                </button>
              </div>
            </>
          )}
          {snapshot.status === "error" && <p className="tx-error">{snapshot.message}</p>}
          {snapshot.warnings.map((w, i) => (
            <p key={i} className="tx-error">{w}</p>
          ))}

          {snapshot.documents.length > 0 && (
            <p className="tx-hint" style={{ margin: "8px 0" }}>
              Read: {snapshot.documents.map((d) => `${d.name} (${d.entries} entries, ${d.method === "table" ? "read from its table" : "read by the AI model"})`).join("; ")}.
            </p>
          )}

          {finished && snapshot.status === "done" && (
            <div className="saved-notice" style={{ marginTop: 12 }}>
              <strong>{snapshot.message ?? "Finished."}</strong> A retention period was found for {doneCount} of {records.length} archiving objects.
            </div>
          )}
          <SavedNotice path={snapshot.output_path} />
          <CompletenessNotice completeness={snapshot.completeness} />

          {records.length > 0 && (
            <div className="table-wrapper" style={{ marginTop: 16 }}>
              <p className="table-caption">Retention periods</p>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Archiving object</th>
                      <th>Retention period</th>
                      <th>Trigger event</th>
                      <th>Matched record series</th>
                      <th>Confidence</th>
                      <th>Reference — where the period was found</th>
                      <th></th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((r) => (
                      <Fragment key={r.object}>
                        <tr>
                          <td>
                            <strong>{r.object}</strong>
                            {r.description && <div className="tx-hint">{r.description}</div>}
                          </td>
                          {r.state === "done" ? (
                            <>
                              <td>
                                <strong>{r.retention}</strong>
                                {r.years !== null && r.period_type === "After an event" && (
                                  <div className="tx-hint">≈ {r.years} years after the trigger</div>
                                )}
                              </td>
                              <td>{r.trigger || "—"}</td>
                              <td>
                                {r.code && <strong>{r.code} </strong>}
                                {r.series}
                                {r.function && <div className="tx-hint">{r.function}</div>}
                              </td>
                              <td><span className={confidenceClass(r.confidence)}>{r.confidence}</span></td>
                              <td className="ref-doc-comment">{r.reference}</td>
                            </>
                          ) : (
                            <>
                              <td colSpan={4}>
                                <span className={`ta-status ${r.state}`}>{r.state === "no_match" ? "no match" : r.state}</span>{" "}
                                <span className="ref-doc-comment">{r.state === "no_match" ? r.reason || r.note : r.note}</span>
                              </td>
                              <td className="ref-doc-comment">—</td>
                            </>
                          )}
                          <td>
                            {(r.reason || r.alternatives.length > 0) && r.state === "done" && (
                              <button className="btn-link" onClick={() => setOpen(open === r.object ? null : r.object)}>
                                {open === r.object ? "Hide details" : "Why / other matches"}
                              </button>
                            )}
                          </td>
                        </tr>
                        {open === r.object && (
                          <tr>
                            <td colSpan={7} className="ref-doc-comment">
                              <div><strong>Why this match:</strong> {r.reason}</div>
                              {r.basis && <div><strong>Basis / regulation in the document:</strong> {r.basis}</div>}
                              {r.alternatives.length > 0 && (
                                <div style={{ marginTop: 6 }}>
                                  <strong>Other entries that could also apply:</strong>
                                  <ul style={{ margin: "4px 0 0 18px" }}>
                                    {r.alternatives.map((a, i) => (
                                      <li key={i}>
                                        {a.series}: <strong>{a.retention}</strong>
                                        {a.trigger ? ` (from ${a.trigger})` : ""} — {a.reference}
                                      </li>
                                    ))}
                                  </ul>
                                </div>
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {snapshot.entry_count > 0 && (
            <details className="oa-details" onToggle={(e) => setShowEntries((e.target as HTMLDetailsElement).open)}>
              <summary>
                <strong>What was read from the document</strong>
                <span className="tx-hint"> · {snapshot.entry_count} retention entries — open to check them</span>
              </summary>
              <div className="oa-body">
                {!entries ? (
                  <p className="tx-hint">Loading…</p>
                ) : (
                  <div className="table-scroll" style={{ maxHeight: 360 }}>
                    <table className="results-table">
                      <thead>
                        <tr><th>#</th><th>Location</th><th>Function</th><th>Record series</th><th>Code</th><th>Trigger</th><th>Retention</th></tr>
                      </thead>
                      <tbody>
                        {entries.map((e) => (
                          <tr key={e.id}>
                            <td>{e.id}</td>
                            <td>{e.file}{e.location ? ` — ${e.location}` : ""}</td>
                            <td>{e.function}</td>
                            <td>{e.name}</td>
                            <td>{e.code}</td>
                            <td>{e.trigger}</td>
                            <td><strong>{e.retention}</strong></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </details>
          )}
        </div>
      )}
    </div>
  );
}
