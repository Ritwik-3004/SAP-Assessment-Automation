import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import { usePublishResults } from "../assistantContext";
import ReferenceDocPanel from "./ReferenceDocPanel";
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
  const [error, setError] = useState("");

  // ── Scoring ──────────────────────────────────────────────────────────────
  const [scored, setScored] = useState<ScoredResult | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoreProgress, setScoreProgress] = useState<ProgressSnapshot<ScoredResult> | null>(null);
  // Which of the two result sheets is shown: every object found, or the recommended pick per table
  const [view, setView] = useState<"all" | "recommended">("all");
  const [scoredExporting, setScoredExporting] = useState(false);
  const [savingScored, setSavingScored] = useState(false);
  const [savedScoredPath, setSavedScoredPath] = useState<string | null>(null);

  // ── Grouped by Object ───────────────────────────────────────────────────
  const [groupedRows, setGroupedRows] = useState<Record<string, string>[] | null>(null);
  const [showGrouped, setShowGrouped] = useState(false);
  const [groupedLoading, setGroupedLoading] = useState(false);

  const fileInputRef = useRef<HTMLInputElement>(null);

  // Let the always-visible assistant see (and change) the scored results.
  const applyAssistantChange = useCallback(
    (rows: Record<string, string>[], recommended: Record<string, string>[]) =>
      setScored((prev) => (prev ? { ...prev, rows, recommended } : prev)),
    []
  );
  usePublishResults(
    scored?.rows && scored.rows.length > 0
      ? { rows: scored.rows, recommended: scored.recommended ?? [], apply: applyAssistantChange }
      : null
  );

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

  /** Score the looked-up objects and pick the recommended one per table (also finds housekeeping
   *  programs for tables with no archiving object). Returns true if scoring succeeded. */
  async function runScoring(rows: Record<string, string>[]): Promise<boolean> {
    setScoring(true);
    setError("");
    setScoreProgress(null);
    setGroupedRows(null);
    setShowGrouped(false);
    try {
      const started = await api.db15Score(rows);
      setScoreProgress({ status: "running", completed: 0, total: started.total, message: null, result: null });
      const final = await api.pollDb15Score(setScoreProgress);
      if (final.status === "error") {
        setError(final.message ?? "Scoring failed");
        return false;
      }
      setScored(final.result);
      setView("all");
      return true;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Scoring failed");
      return false;
    } finally {
      setScoring(false);
    }
  }

  /** One click: look up the archiving objects in SAP, then score and recommend them. */
  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit || loading || scoring) return;
    setLoading(true);
    setError("");
    setResult(null);
    setScored(null);
    setView("all");
    setSavedScoredPath(null);
    setGroupedRows(null);
    setShowGrouped(false);
    setLookupProgress(null);
    setScoreProgress(null);

    let rows: Record<string, string>[] = [];
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
        return;
      }
      setResult(final.result);
      rows = final.result?.rows ?? [];
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Lookup failed");
      return;
    } finally {
      setLoading(false);
    }

    if (rows.length === 0) return; // nothing found to score; the "no objects" note below explains
    await runScoring(rows);
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

  async function handleToggleGrouped() {
    if (showGrouped) {
      setShowGrouped(false);
      return;
    }
    if (groupedRows) {
      setShowGrouped(true);
      return;
    }
    if (!scored?.recommended?.length) return;
    setGroupedLoading(true);
    setError("");
    try {
      const res = await api.groupByObject(scored.recommended);
      setGroupedRows(res.rows);
      setShowGrouped(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Grouping failed");
    } finally {
      setGroupedLoading(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Find Archiving Objects for Tables</strong> — select a table list from the
        input folder, or upload your own Excel file (Table Name in column A, Description
        in column B, header row first). For each table, this looks up the archiving
        object(s) that reference it, then — in the same run — has the selected AI model
        score each candidate (0–100, approximate), referring to SAP's Data Management
        Guide (DVM Guide) where it covers the table, and picks the highest-scoring object
        per table. For a table with no archiving object it looks for a housekeeping/cleanup
        program instead: first in the DVM Guide, then on SAP for Me (needs your SAP for Me
        credentials in the sidebar; if it can't run, the Rationale says why). The result is
        two sheets: every object identified (with its score), and the recommended object per
        table. If your file doesn't already have "Volume (GB)" / "Volume (MB)" size columns,
        they're fetched automatically from DB02 and carried through. Scores and rationale are
        AI-generated best-effort judgments, not guaranteed SAP guidance — treat them as a
        starting point.
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
          disabled={loading || scoring || !canSubmit}
        >
          {loading
            ? "Step 1 of 2 — looking up archiving objects…"
            : scoring
            ? "Step 2 of 2 — scoring and recommending…"
            : "Find, Score & Recommend"}
        </button>

        {loading && (
          <ProgressBar
            mode="determinate"
            completed={lookupProgress?.completed ?? 0}
            total={lookupProgress?.total ?? 0}
            label={lookupProgress?.message ?? undefined}
          />
        )}

        {scoring && (
          <ProgressBar
            mode="determinate"
            completed={scoreProgress?.completed ?? 0}
            total={scoreProgress?.total ?? 0}
            label={scoreProgress?.message ?? undefined}
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

      {/* Lookup finished but nothing to score */}
      {result && !loading && (result.rows?.length ?? 0) === 0 && !error && (
        <p className="tx-hint">No archiving objects were found for any table in this file.</p>
      )}

      {/* Scoring failed after a successful lookup: keep the lookup, offer a retry */}
      {!scored && !scoring && !loading && result?.rows && result.rows.length > 0 && (
        <>
          <ResultsTable
            rows={result.rows}
            caption={`Archiving objects found — ${result.rows.length} row${result.rows.length !== 1 ? "s" : ""} (not scored yet)`}
          />
          <div className="action-row">
            <button type="button" className="btn btn-primary" onClick={() => runScoring(result.rows ?? [])}>
              Retry scoring
            </button>
          </div>
        </>
      )}

      {scored?.rows && scored.rows.length > 0 && (
        <>
          <div className="sheet-tabs" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={view === "all"}
              className={`sheet-tab ${view === "all" ? "active" : ""}`}
              onClick={() => setView("all")}
            >
              All objects identified ({scored.rows.length})
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={view === "recommended"}
              className={`sheet-tab ${view === "recommended" ? "active" : ""}`}
              onClick={() => setView("recommended")}
            >
              Recommended objects ({scored.recommended?.length ?? 0})
            </button>
          </div>

          {view === "all" ? (
            <ResultsTable
              rows={scored.rows}
              caption={`All objects identified, with scores — ${scored.rows.length} row${scored.rows.length !== 1 ? "s" : ""}`}
            />
          ) : (
            <ResultsTable
              rows={scored.recommended ?? []}
              caption="Recommended — highest-scoring object per table"
            />
          )}

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
              onClick={handleToggleGrouped}
              disabled={groupedLoading}
            >
              {groupedLoading ? "Grouping…" : showGrouped ? "Hide Grouped by Object" : "Show Grouped by Object"}
            </button>
          </div>
          {savedScoredPath && (
            <div className="saved-notice">
              Saved to <code>output/archiving_objects_scored.xlsx</code>
            </div>
          )}

          {showGrouped && groupedRows && (
            <ResultsTable
              rows={groupedRows}
              caption="Grouped by Object — tables sharing an archiving object/housekeeping program, sorted by cumulative size (largest first; tables with neither are grouped last)"
            />
          )}
        </>
      )}

      {scored?.rows && scored.rows.length > 0 && (
        <ReferenceDocPanel scored={scored} />
      )}

    </div>
  );
}
