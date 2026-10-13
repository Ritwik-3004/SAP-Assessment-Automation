import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import { useScreenEpoch } from "../useScreenEpoch";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import SavedNotice from "./SavedNotice";
import HeaderReferencePanel from "./HeaderReferencePanel";
import type { HeaderTableBatchResult, ProgressSnapshot } from "../types";

export default function HeaderTablePanel() {
  // ── Input source ────────────────────────────────────────────────────────
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [sourceFolder, setSourceFolder] = useState("");
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [maxObjectsStr, setMaxObjectsStr] = useState("20");

  // ── Batch job ────────────────────────────────────────────────────────────
  const [result, setResult] = useState<HeaderTableBatchResult | null>(null);
  // Changes with every new result so the reference-document panel below starts fresh.
  const [runId, setRunId] = useState(0);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<ProgressSnapshot<HeaderTableBatchResult> | null>(null);
  const [error, setError] = useState("");

  const fileInputRef = useRef<HTMLInputElement>(null);
  // Bumped when the screen is left, so polls started by this screen stop (the run itself goes on in the backend)
  const epoch = useScreenEpoch();

  async function fetchOutputFiles() {
    setLoadingFiles(true);
    try {
      const { files, folder } = await api.getResultFiles("BATCH");
      setOutputFiles(files);
      setSourceFolder(folder);
      if (!serverFile) {
        // Prefer the scored workbook if present, otherwise the newest file.
        setServerFile(files.find((f) => f === "archiving_objects_scored.xlsx") ?? files[0] ?? null);
      }
    } catch {
      // Non-fatal — user can still upload manually
    } finally {
      setLoadingFiles(false);
    }
  }

  /** Follow the lookup in the backend until it ends. Used after Submit and when the screen is opened again. */
  async function follow(isCurrent: () => boolean) {
    setLoading(true);
    setError("");
    try {
      const final = await api.pollHeaderTablesBatch(setProgress, () => !isCurrent());
      if (!isCurrent()) return;
      if (final.status === "error") {
        setError(final.message ?? "Header-table lookup failed");
      } else {
        setResult(final.result);
        setRunId((n) => n + 1);
      }
    } catch (err: unknown) {
      if (isCurrent()) setError(err instanceof Error ? err.message : "Header-table lookup failed");
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }

  // Pick up a run that is going or finished (the state lives in the backend, like the analysis tasks)
  useEffect(() => {
    fetchOutputFiles();
    const isCurrent = epoch.begin();
    api
      .headerTablesBatchProgress()
      .then((s) => {
        if (isCurrent() && s.status !== "idle") follow(isCurrent);
      })
      .catch(() => {});
    return () => {
      epoch.leave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
  }

  const canSubmit = mode === "upload" ? !!localFile : !!serverFile;

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    const maxObjects = Math.max(1, parseInt(maxObjectsStr, 10) || 20);
    setLoading(true);
    setError("");
    setResult(null);
    setProgress(null);
    try {
      let started: import("../types").JobStarted;
      if (mode === "upload" && localFile) {
        started = await api.headerTablesBatch(localFile, maxObjects);
      } else if (mode === "folder" && serverFile) {
        started = await api.headerTablesBatchFromOutput(serverFile, maxObjects);
      } else {
        setLoading(false);
        return;
      }
      setProgress({ status: "running", completed: 0, total: started.total, message: null, result: null });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Header-table lookup failed");
      setLoading(false);
      return;
    }
    follow(epoch.current());
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Find Header Tables for Archiving Objects</strong> — select
        "archiving_objects_scored.xlsx" saved by Find Archiving Objects for Tables (its last sheet, already
        sorted by descending cumulative size, is used), or upload your own Excel file
        with an "Archiving Object" column on its last sheet. For each of the top N
        archiving objects, this looks up its header table — the segment with no parent
        segment. Whenever SAP's archiving definition doesn't settle it (several top-level segments, none,
        or no entry at all), the DVM Guide and SAP for Me are consulted so that every
        object gets a header table, marked with its source and a confidence level. You can then upload a reference document to cross-check the
        results.
      </div>

      <form className="tx-form" onSubmit={handleSubmit}>
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Saved by Find Archiving Objects for Tables</span>
                <button
                  type="button"
                  className="btn-refresh"
                  onClick={fetchOutputFiles}
                  disabled={loadingFiles}
                  title="Refresh file list"
                >
                  ↻
                </button>
              </div>

              {sourceFolder && (
                <p className="tx-hint" style={{ margin: 0 }}>
                  Folder: <code>{sourceFolder}</code>
                </p>
              )}
              {loadingFiles ? (
                <p className="tx-hint">Loading…</p>
              ) : outputFiles.length === 0 ? (
                <p className="tx-hint">
                  Nothing saved yet. Run <em>Find Archiving Objects for Tables</em>{" "}
                  first, or upload a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {outputFiles.map((name) => (
                    <label
                      key={name}
                      className={`file-radio-item ${serverFile === name ? "selected" : ""}`}
                    >
                      <input
                        type="radio"
                        name="outputFile"
                        value={name}
                        checked={serverFile === name}
                        onChange={() => setServerFile(name)}
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
                  />
                </label>
              </div>
            </>
          ) : (
            <div className="uploaded-file-row">
              <span className="file-icon-lg">📄</span>
              <span className="uploaded-file-name">{localFile?.name}</span>
              <button
                type="button"
                className="btn-clear-upload"
                onClick={handleClearUpload}
              >
                ✕ Use a saved file
              </button>
            </div>
          )}
        </div>

        <div className="form-row">
          <label>Top N archiving objects</label>
          <input
            type="number"
            min={1}
            max={100}
            value={maxObjectsStr}
            onChange={(e) => setMaxObjectsStr(e.target.value)}
          />
        </div>

        <button
          type="submit"
          className="btn btn-primary"
          disabled={loading || !canSubmit}
        >
          {loading ? "Looking up header tables…" : "Submit"}
        </button>

        {loading && (
          <ProgressBar
            mode="determinate"
            completed={progress?.completed ?? 0}
            total={progress?.total ?? 0}
            label={progress?.message ?? undefined}
          />
        )}
      </form>

      {error && <p className="tx-error">{error}</p>}

      {result?.errors && result.errors.length > 0 && (
        <div className="tx-error">
          <p>Some archiving objects failed:</p>
          <ul>
            {result.errors.map((e, i) => (
              <li key={i}>
                {e.archiving_object}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result?.rows && result.rows.length > 0 && (
        <>
          <ResultsTable
            rows={result.rows}
            caption={`Header tables — ${result.rows.length} row${result.rows.length !== 1 ? "s" : ""}`}
          />
          <SavedNotice path={result.saved_path} error={result.save_error} />
          {result.rows.some((r) => r["Confidence"] && r["Confidence"] !== "High") && (
            <p className="tx-hint">
              Rows marked Medium or Low confidence are best guesses made from the DVM Guide
              and SAP for Me — see Comments for the other candidates, and use the reference
              document below to confirm them.
            </p>
          )}

          <HeaderReferencePanel key={runId} rows={result.rows} />
        </>
      )}
    </div>
  );
}
