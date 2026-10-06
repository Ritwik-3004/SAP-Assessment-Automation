import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import ProgressBar from "./ProgressBar";
import ObjectReferenceReview from "./ObjectReferenceReview";
import type { ObjectAnalysisDetail, ObjectAnalysisSnapshot } from "../types";

const SOURCES = ["SARA information (SAP Help Portal)", "DVM Guide", "SAP for Me"] as const;

const sourceClass = (label: string) =>
  label.startsWith("SARA")
    ? "sara"
    : label.startsWith("DVM")
    ? "dvm"
    : label.startsWith("SAP for Me")
    ? "sfm"
    : label.startsWith("Reference document")
    ? "ref"
    : "";

/** One object's preview: what to archive first, then the conditions with their sources. */
function ObjectPreview({ d }: { d: ObjectAnalysisDetail }) {
  return (
    <details className="oa-details">
      <summary>
        <strong>{d.object}</strong>
        {d.description && <span className="tx-hint"> — {d.description}</span>}
        <span className="tx-hint">
          {" "}
          · {d.conditions.length} condition{d.conditions.length !== 1 ? "s" : ""} ·{" "}
          {d.prerequisites.length > 0
            ? `archive first: ${d.prerequisites.map((p) => p.object).join(" → ")}`
            : d.network_known
            ? "nothing to archive first"
            : "dependencies unknown"}
        </span>
      </summary>
      <div className="oa-body">
        {d.error && <p className="tx-error">{d.error}</p>}

        <div className="oa-before">
          <h4>Archive these objects BEFORE {d.object}</h4>
          {!d.network_known ? (
            <p className="tx-hint">The archiving-object network could not be read, so this is unknown.</p>
          ) : d.prerequisites.length === 0 ? (
            <p>None — no archiving object has to be archived before {d.object}.</p>
          ) : (
            <div className="table-scroll">
              <table className="results-table">
                <thead>
                  <tr>
                    <th>Step</th><th>Archiving object</th><th>Description</th><th>Relationship</th><th>Required before</th>
                    {d.prerequisites.some((p) => p.ref) && <th>Reference check</th>}
                  </tr>
                </thead>
                <tbody>
                  {d.prerequisites.map((p) => (
                    <tr key={p.object}>
                      <td>{p.step ?? "—"}</td>
                      <td><strong>{p.object}</strong></td>
                      <td>{p.description ?? ""}</td>
                      <td>
                        {p.from_reference
                          ? "From the reference document — not in SAP's network, order unknown"
                          : p.direct
                          ? `Direct — ${d.object} requires it first`
                          : "Indirect — needed further up the chain"}
                      </td>
                      <td>{p.required_by.join(", ")}</td>
                      {d.prerequisites.some((q) => q.ref) && <td className="ref-doc-comment">{p.ref ?? ""}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
              <p className="tx-hint">Archive in step order (step 1 first); {d.object} itself comes after the last step.</p>
            </div>
          )}
        </div>

        <div className="oa-conds">
          <h4>Archiving conditions</h4>
          {d.conditions.length === 0 ? (
            <p className="tx-hint">No archiving conditions found in the sources checked so far.</p>
          ) : (
            <div className="table-scroll">
              <table className="results-table">
                <thead>
                  <tr>
                    <th>#</th><th>Condition</th><th>Source</th><th>Detail</th>
                    {d.conditions.some((c) => c.ref) && <th>Reference check</th>}
                  </tr>
                </thead>
                <tbody>
                  {d.conditions.map((c, i) => (
                    <tr key={i}>
                      <td>{i + 1}</td>
                      <td>{c.condition}</td>
                      <td><span className={`oa-src ${sourceClass(c.source)}`}>{c.source}</span></td>
                      <td className="ref-doc-comment">{c.detail}</td>
                      {d.conditions.some((x) => x.ref) && <td className="ref-doc-comment">{c.ref ?? ""}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div className="tx-hint" style={{ marginTop: 6 }}>
            {Object.entries(d.sources).map(([k, v]) => (
              <div key={k}>{k}: {v}</div>
            ))}
            {d.sara_url && <div>SAP Help Portal page: {d.sara_url}</div>}
          </div>
        </div>
      </div>
    </details>
  );
}

export default function ObjectAnalysisPanel() {
  // ── Input source (default: the recommended objects saved by Find Archiving Objects) ──
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);

  const [snapshot, setSnapshot] = useState<ObjectAnalysisSnapshot | null>(null);
  const [details, setDetails] = useState<ObjectAnalysisDetail[]>([]);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const fileInputRef = useRef<HTMLInputElement>(null);

  const mainRunning = snapshot?.status === "running";
  const sfm = snapshot?.sap_for_me;
  const sfmRunning = sfm?.status === "running";
  const active = mainRunning || sfmRunning;

  const preferred = (files: string[]) =>
    files.find((f) => f === "archiving_objects_with_reference.xlsx") ??
    files.find((f) => f === "archiving_objects_scored.xlsx") ??
    files.find((f) => f.startsWith("archiving_objects")) ??
    files[0] ??
    null;

  async function fetchOutputFiles() {
    setLoadingFiles(true);
    try {
      const { files } = await api.getOutputFiles();
      const spreadsheets = files.filter((f) => !f.startsWith("Table analysis") && !f.startsWith("Archiving object analysis"));
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
    api.objectAnalysisProgress().then(setSnapshot).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!active) return;
    const timer = setInterval(() => {
      api.objectAnalysisProgress().then(setSnapshot).catch(() => {});
    }, 1500);
    return () => clearInterval(timer);
  }, [active]);

  // The preview follows the run: refresh it whenever another object (or SAP for Me step) completes
  const previewKey = `${snapshot?.status}|${snapshot?.completed}|${sfm?.status}|${sfm?.completed}`;
  useEffect(() => {
    if (!snapshot || snapshot.completed === 0) return;
    api.objectAnalysisResults().then((r) => setDetails(r.objects)).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewKey]);

  function refreshAfterApply() {
    api.objectAnalysisResults().then((r) => setDetails(r.objects)).catch(() => {});
    api.objectAnalysisProgress().then(setSnapshot).catch(() => {});
  }

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
    setDetails([]);
    try {
      await api.objectAnalysisStart(mode === "upload" && localFile ? { file: localFile } : { filename: serverFile ?? "" });
      setSnapshot(await api.objectAnalysisProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the analysis");
    } finally {
      setStarting(false);
    }
  }

  async function handleSapForMe() {
    setError("");
    try {
      await api.objectAnalysisSapForMe();
      setSnapshot(await api.objectAnalysisProgress());
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the SAP for Me check");
    }
  }

  async function handleDownload() {
    try {
      const blob = await api.objectAnalysisDownload();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "Archiving object analysis.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Download failed");
    }
  }

  const records = snapshot?.records ?? [];
  const mainDone = snapshot?.status === "done" && records.length > 0;

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Archiving Object Analysis</strong> — for each recommended archiving object this finds its{" "}
        <strong>archiving conditions</strong> and which archiving objects must be <strong>archived before it</strong>.
        It works in two steps. <strong>Step 1</strong> (about a minute and a half per object) reads the object's SAP Help
        Portal page that SARA's information button opens (including its “Checks” pages) and the DVM Guide, and finds the
        objects to archive first from SARA's network (the data behind its Network Graphic), in archiving order. You see the
        results straight away. If they don't answer your questions, <strong>Step 2</strong> checks SAP for Me as well: it is
        slower, runs in the background, and adds its conditions to the same results and Excel file as it finds them, so you
        can keep using other tasks meanwhile. Results go to <code>output/Archiving object analysis.xlsx</code> (a Summary
        sheet and one sheet per object). By default the tool uses the recommended objects saved by{" "}
        <em>Find Archiving Objects</em>; you can upload a different file. After the results are in you can also upload
        reference documents (any type) to fill gaps and spot mismatches. During step 1 SAP opens and closes a browser tab
        for each object, so please leave the browser and SAP alone until it finishes.
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
                  No Excel files in the output folder. Run <em>Find Archiving Objects</em> and save the result, or upload
                  a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {outputFiles.map((name) => (
                    <label key={name} className={`file-radio-item ${serverFile === name ? "selected" : ""}`}>
                      <input
                        type="radio"
                        name="objectAnalysisFile"
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

        <button type="submit" className="btn btn-primary" disabled={!canStart}>
          {mainRunning ? "Step 1 running…" : starting ? "Starting…" : "Start analysis (SARA + DVM Guide)"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}

      {snapshot && snapshot.status !== "idle" && (
        <div style={{ marginTop: 16 }}>
          {mainRunning && (
            <>
              <ProgressBar
                mode="determinate"
                completed={snapshot.completed}
                total={snapshot.total}
                label={`Step 1: ${snapshot.completed} of ${snapshot.total} objects` + (snapshot.message ? ` — ${snapshot.message}` : "")}
              />
              <div className="action-row" style={{ marginTop: 8 }}>
                <button className="btn btn-secondary" onClick={() => api.objectAnalysisCancel().catch(() => {})}>
                  Cancel
                </button>
              </div>
            </>
          )}

          {snapshot.warnings.map((w, i) => (
            <p key={i} className="tx-error">{w}</p>
          ))}
          {snapshot.status === "error" && <p className="tx-error">{snapshot.message}</p>}

          {/* Step 2: SAP for Me, in the background */}
          {mainDone && (
            <div className="saved-notice" style={{ marginTop: 12 }}>
              <strong>Step 1 finished — {records.length} object{records.length !== 1 ? "s" : ""} analysed.</strong> Open an
              object below to see what to archive first and its conditions.{" "}
              {sfmRunning ? (
                <>
                  <div style={{ marginTop: 8 }}>
                    <ProgressBar
                      mode="determinate"
                      completed={sfm?.completed ?? 0}
                      total={sfm?.total ?? 0}
                      label={`SAP for Me (background): ${sfm?.completed ?? 0} of ${sfm?.total ?? 0}` + (sfm?.message ? ` — ${sfm.message}` : "")}
                    />
                  </div>
                  <p className="tx-hint" style={{ marginTop: 6 }}>
                    This keeps running if you switch to another task (a ⏳ indicator stays in the header), and new conditions
                    appear below as each object finishes.
                  </p>
                  <button className="btn btn-secondary" onClick={() => api.objectAnalysisSapForMeCancel().catch(() => {})}>
                    Stop SAP for Me
                  </button>
                </>
              ) : (
                <div className="action-row" style={{ marginTop: 8 }}>
                  <button className="btn btn-secondary" onClick={handleSapForMe}>
                    {sfm?.status === "done" ? "Check SAP for Me again" : "Also check SAP for Me (runs in the background)"}
                  </button>
                  {snapshot.output_path && (
                    <button className="btn btn-primary" onClick={handleDownload}>Download Excel</button>
                  )}
                </div>
              )}
              {!sfmRunning && sfm && sfm.status !== "idle" && (
                <p className="tx-hint" style={{ marginTop: 6 }}>
                  {sfm.status === "error" ? `SAP for Me failed: ${sfm.message}` : sfm.message}
                </p>
              )}
            </div>
          )}

          {snapshot.status !== "running" && records.length > 0 && (
            <ObjectReferenceReview enabled={mainDone} onApplied={refreshAfterApply} />
          )}

          {records.length > 0 && (
            <div className="table-wrapper" style={{ marginTop: 16 }}>
              <p className="table-caption">Objects analysed</p>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th>Archiving object</th>
                      <th>Result</th>
                      <th>Conditions</th>
                      {SOURCES.map((s) => (
                        <th key={s}>{s === "SARA information (SAP Help Portal)" ? "SARA info" : s}</th>
                      ))}
                      <th>Archive BEFORE (in order)</th>
                      <th>Notes</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records.map((r) => (
                      <tr key={r.object}>
                        <td>
                          <strong>{r.object}</strong>
                          {r.description && <div className="tx-hint">{r.description}</div>}
                        </td>
                        <td><span className={`ta-status ${r.state}`}>{r.state}</span></td>
                        <td>{r.conditions}</td>
                        {SOURCES.map((s) => (
                          <td key={s}>{r.by_source?.[s] ?? 0}</td>
                        ))}
                        <td className={r.archive_first.length > 0 ? "ref-doc-ref-obj" : ""}>
                          {r.archive_first.length > 0 ? r.archive_first.join(" → ") : "none"}
                        </td>
                        <td className="ref-doc-comment">{r.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {details.length > 0 && (
            <div style={{ marginTop: 16 }}>
              <p className="table-caption">Preview — open an object for its details</p>
              {details.map((d) => (
                <ObjectPreview key={d.object} d={d} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
