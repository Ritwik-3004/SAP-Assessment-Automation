import { useState, useEffect, useRef } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import type { Db15BatchResult, ProgressSnapshot, ScoredResult } from "../types";

export default function BatchArchivingPanel() {
  // ── Input source ────────────────────────────────────────────────────────
  const [inputFiles, setInputFiles] = useState<string[]>([]);
  const [serverFile, setServerFile] = useState<string | null>(null);
  const [localFile, setLocalFile] = useState<File | null>(null);
  const [mode, setMode] = useState<"folder" | "upload">("folder");
  const [loadingFiles, setLoadingFiles] = useState(false);

  // ── Batch job ────────────────────────────────────────────────────────────
  const [result, setResult] = useState<Db15BatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [lookupProgress, setLookupProgress] = useState<ProgressSnapshot<Db15BatchResult> | null>(null);
  const [exporting, setExporting] = useState(false);
  const [savingArchiving, setSavingArchiving] = useState(false);
  const [savedArchivingPath, setSavedArchivingPath] = useState<string | null>(null);
  const [error, setError] = useState("");

  // ── Scoring ──────────────────────────────────────────────────────────────
  const [scored, setScored] = useState<ScoredResult | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoreProgress, setScoreProgress] = useState<ProgressSnapshot<ScoredResult> | null>(null);
  const [showRecommended, setShowRecommended] = useState(false);
  const [scoredExporting, setScoredExporting] = useState(false);
  const [savingScored, setSavingScored] = useState(false);
  const [savedScoredPath, setSavedScoredPath] = useState<string | null>(null);

  const fileInputRef = useRef<HTMLInputElement>(null);

  async function fetchInputFiles() {
    setLoadingFiles(true);
    try {
      const { files } = await api.getInputFiles();
      setInputFiles(files);
      // Auto-select the first (newest) file only if nothing is already chosen
      if (files.length > 0 && !serverFile) {
        setServerFile(files[0]);
      }
    } catch {
      // Non-fatal — user can still upload manually
    } finally {
      setLoadingFiles(false);
    }
  }

  useEffect(() => {
    fetchInputFiles();
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
    setLoading(true);
    setError("");
    setResult(null);
    setScored(null);
    setShowRecommended(false);
    setLookupProgress(null);
    try {
      let started: import("../types").JobStarted;
      if (mode === "upload" && localFile) {
        started = await api.db15Batch(localFile);
      } else if (mode === "folder" && serverFile) {
        started = await api.db15BatchFromInput(serverFile);
      } else {
        return;
      }
      setLookupProgress({ status: "running", completed: 0, total: started.total, message: null, result: null });
      const final = await api.pollDb15Batch(setLookupProgress);
      if (final.status === "error") {
        setError(final.message ?? "Lookup failed");
      } else {
        setResult(final.result);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Lookup failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    if (!result?.rows?.length) return;
    setExporting(true);
    setError("");
    try {
      const blob = await api.db15Export(result.rows);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "archiving_objects_by_table.xlsx";
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

  async function handleSaveArchiving() {
    if (!result?.rows?.length) return;
    setSavingArchiving(true);
    setError("");
    setSavedArchivingPath(null);
    try {
      const res = await api.saveArchivingToOutput(result.rows);
      setSavedArchivingPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingArchiving(false);
    }
  }

  async function handleScore() {
    if (!result?.rows?.length) return;
    setScoring(true);
    setError("");
    setScoreProgress(null);
    try {
      const started = await api.db15Score(result.rows);
      setScoreProgress({ status: "running", completed: 0, total: started.total, message: null, result: null });
      const final = await api.pollDb15Score(setScoreProgress);
      if (final.status === "error") {
        setError(final.message ?? "Scoring failed");
      } else {
        setScored(final.result);
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Scoring failed");
    } finally {
      setScoring(false);
    }
  }

  async function handleScoredExport() {
    if (!scored?.rows?.length) return;
    setScoredExporting(true);
    setError("");
    try {
      const blob = await api.db15ScoreExport(scored.rows, scored.recommended ?? []);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "archiving_objects_scored.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setScoredExporting(false);
    }
  }

  async function handleSaveScored() {
    if (!scored?.rows?.length) return;
    setSavingScored(true);
    setError("");
    setSavedScoredPath(null);
    try {
      const res = await api.saveScoredToOutput(scored.rows, scored.recommended ?? []);
      setSavedScoredPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSavingScored(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Find Archiving Objects for Tables</strong> — select a table list from the
        input folder, or upload your own Excel file (Table Name in column A, Description
        in column B, header row first). For each table, this looks up the archiving
        object(s) that reference it.
      </div>

      <form className="tx-form" onSubmit={handleSubmit}>
        <div className="file-picker">
          {mode === "folder" ? (
            <>
              <div className="file-picker-header">
                <span className="file-picker-title">Input folder</span>
                <button
                  type="button"
                  className="btn-refresh"
                  onClick={fetchInputFiles}
                  disabled={loadingFiles}
                  title="Refresh file list"
                >
                  ↻
                </button>
              </div>

              {loadingFiles ? (
                <p className="tx-hint">Loading…</p>
              ) : inputFiles.length === 0 ? (
                <p className="tx-hint">
                  No Excel files in the input folder. Run <em>Generate Table List</em> first,
                  or upload a file below.
                </p>
              ) : (
                <div className="file-radio-list">
                  {inputFiles.map((name) => (
                    <label
                      key={name}
                      className={`file-radio-item ${serverFile === name ? "selected" : ""}`}
                    >
                      <input
                        type="radio"
                        name="inputFile"
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
                ✕ Use input folder
              </button>
            </div>
          )}
        </div>

        <button
          type="submit"
          className="btn btn-primary"
          disabled={loading || !canSubmit}
        >
          {loading ? "Looking up archiving objects…" : "Submit"}
        </button>

        {loading && (
          <ProgressBar
            mode="determinate"
            completed={lookupProgress?.completed ?? 0}
            total={lookupProgress?.total ?? 0}
            label={lookupProgress?.message ?? undefined}
          />
        )}
      </form>

      {error && <p className="tx-error">{error}</p>}

      {result?.errors && result.errors.length > 0 && (
        <div className="tx-error">
          <p>Some tables failed:</p>
          <ul>
            {result.errors.map((e, i) => (
              <li key={i}>
                {e.table_name}: {e.message}
              </li>
            ))}
          </ul>
        </div>
      )}

      {result?.rows && result.rows.length > 0 && (
        <>
          <ResultsTable
            rows={result.rows}
            caption={`Archiving objects — ${result.rows.length} row${result.rows.length !== 1 ? "s" : ""}`}
          />
          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSaveArchiving}
              disabled={savingArchiving}
            >
              {savingArchiving ? "Saving…" : "Save to output folder"}
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
          {savedArchivingPath && (
            <div className="saved-notice">
              Saved to <code>output/archiving_objects_by_table.xlsx</code>
            </div>
          )}

          <div className="tx-description" style={{ marginTop: "1.5rem" }}>
            <strong>Score &amp; Recommend</strong> — have Claude score each candidate
            object per table for archiving relevance (0–100, approximate), referring to
            SAP's official Data Management Guide (DVM Guide) where it covers that table,
            then pick the highest-scoring object per table as the recommended setup.
            Scores and rationale are AI-generated best-effort judgments, not guaranteed
            SAP guidance — treat them as a starting point.
          </div>
          <button
            type="button"
            className="btn btn-primary"
            onClick={handleScore}
            disabled={scoring}
          >
            {scoring ? "Scoring objects…" : "Score & Recommend Objects"}
          </button>

          {scoring && (
            <ProgressBar
              mode="determinate"
              completed={scoreProgress?.completed ?? 0}
              total={scoreProgress?.total ?? 0}
              label={scoreProgress?.message ?? undefined}
            />
          )}
        </>
      )}

      {scored?.rows && scored.rows.length > 0 && (
        <>
          <ResultsTable
            rows={scored.rows}
            caption={`Scored objects — ${scored.rows.length} row${scored.rows.length !== 1 ? "s" : ""}`}
          />

          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSaveScored}
              disabled={savingScored}
            >
              {savingScored ? "Saving…" : "Save to output folder"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleScoredExport}
              disabled={scoredExporting}
            >
              {scoredExporting ? "Preparing…" : "Download Excel (Scored)"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={() => setShowRecommended((v) => !v)}
            >
              {showRecommended ? "Hide Recommended" : "Show Recommended"}
            </button>
          </div>
          {savedScoredPath && (
            <div className="saved-notice">
              Saved to <code>output/archiving_objects_scored.xlsx</code>
            </div>
          )}

          {showRecommended && scored.recommended && (
            <ResultsTable
              rows={scored.recommended}
              caption="Recommended — highest-scoring object per table"
            />
          )}
        </>
      )}
    </div>
  );
}
