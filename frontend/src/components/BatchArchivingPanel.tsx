import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import type { Db15BatchResult, ProgressSnapshot, ScoredResult } from "../types";

const PREVIEW_ROWS = 20;

export default function BatchArchivingPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Db15BatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [lookupProgress, setLookupProgress] = useState<ProgressSnapshot<Db15BatchResult> | null>(null);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  const [scored, setScored] = useState<ScoredResult | null>(null);
  const [scoring, setScoring] = useState(false);
  const [scoreProgress, setScoreProgress] = useState<ProgressSnapshot<ScoredResult> | null>(null);
  const [showRecommended, setShowRecommended] = useState(false);
  const [scoredExporting, setScoredExporting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    setScored(null);
    setShowRecommended(false);
    setLookupProgress(null);
    try {
      const started = await api.db15Batch(file);
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

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Find Archiving Objects for Tables</strong> — upload an Excel file
        listing the tables you want to check (Table Name in column A, Description in
        column B, header row first). For each table, this looks up the archiving
        object(s) that reference it.
      </div>

      <form className="tx-form" onSubmit={handleSubmit}>
        <div className="form-row">
          <label>Table List (Excel)</label>
          <input
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </div>

        <button type="submit" className="btn btn-primary" disabled={loading || !file}>
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
            rows={result.rows.slice(0, PREVIEW_ROWS)}
            caption={`Preview — showing ${Math.min(PREVIEW_ROWS, result.rows.length)} of ${result.rows.length} rows`}
          />
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? "Preparing file…" : "Export to Excel"}
          </button>

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
            rows={scored.rows.slice(0, PREVIEW_ROWS)}
            caption={`Preview — showing ${Math.min(PREVIEW_ROWS, scored.rows.length)} of ${scored.rows.length} scored rows`}
          />

          <button
            type="button"
            className="btn btn-secondary"
            onClick={() => setShowRecommended((v) => !v)}
          >
            {showRecommended ? "Hide Recommended List" : "Show Recommended List"}
          </button>
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleScoredExport}
            disabled={scoredExporting}
          >
            {scoredExporting ? "Preparing file…" : "Download Excel (Scored)"}
          </button>

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
