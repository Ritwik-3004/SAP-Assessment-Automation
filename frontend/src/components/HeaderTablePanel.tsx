import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import type { HeaderTableBatchResult, ProgressSnapshot } from "../types";

export default function HeaderTablePanel() {
  // ── Input source ────────────────────────────────────────────────────────
  const [outputFiles, setOutputFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);
  const [maxObjectsStr, setMaxObjectsStr] = useState("20");

  // ── Batch job ────────────────────────────────────────────────────────────
  const [result, setResult] = useState<HeaderTableBatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [progress, setProgress] = useState<ProgressSnapshot<HeaderTableBatchResult> | null>(null);
  const [exporting, setExporting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [error, setError] = useState("");

  const fileInputRef = useRef<HTMLInputElement>(null);

  async function fetchOutputFiles() {
    setLoadingFiles(true);
    try {
      const { files } = await api.getOutputFiles();
      setOutputFiles(files);
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

  useEffect(() => {
    fetchOutputFiles();
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
    setSavedPath(null);
    setProgress(null);
    try {
      let started: import("../types").JobStarted;
      if (mode === "upload" && localFile) {
        started = await api.headerTablesBatch(localFile, maxObjects);
      } else if (mode === "folder" && serverFile) {
        started = await api.headerTablesBatchFromOutput(serverFile, maxObjects);
      } else {
        return;
      }
      setProgress({ status: "running", completed: 0, total: started.total, message: null, result: null });
      const final = await api.pollHeaderTablesBatch(setProgress);
      if (final.status === "error") {
        setError(final.message ?? "Header-table lookup failed");
      } else {
        setResult(final.result);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Header-table lookup failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    if (!result?.rows?.length) return;
    setExporting(true);
    setError("");
    try {
      const blob = await api.headerTablesExport(result.rows);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "header_tables.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  async function handleSave() {
    if (!result?.rows?.length) return;
    setSaving(true);
    setError("");
    setSavedPath(null);
    try {
      const res = await api.saveHeaderTablesToOutput(result.rows);
      setSavedPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Find Header Tables for Archiving Objects</strong> — select
        "archiving_objects_scored.xlsx" from the output folder (its last sheet, already
        sorted by descending cumulative size, is used), or upload your own Excel file
        with an "Archiving Object" column on its last sheet. For each of the top N
        archiving objects, this looks up its header table — the segment with no parent
        segment.
      </div>

      <form className="tx-form" onSubmit={handleSubmit}>
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Output folder</span>
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

              {loadingFiles ? (
                <p className="tx-hint">Loading…</p>
              ) : outputFiles.length === 0 ? (
                <p className="tx-hint">
                  No Excel files in the output folder yet. Run <em>Score & Recommend Objects</em>{" "}
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
                ✕ Use output folder
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
          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save to output folder"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleExport}
              disabled={exporting}
            >
              {exporting ? "Preparing…" : "Download Excel"}
            </button>
          </div>
          {savedPath && (
            <div className="saved-notice">
              Saved to <code>output/header_tables.xlsx</code>
            </div>
          )}
        </>
      )}
    </div>
  );
}
