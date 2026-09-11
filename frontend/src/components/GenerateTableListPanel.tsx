import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import type { Db02TopTablesResult } from "../types";

export default function GenerateTableListPanel() {
  const [limitStr, setLimitStr] = useState("300");
  const [result, setResult] = useState<Db02TopTablesResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [error, setError] = useState("");

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    const limit = Math.max(1, parseInt(limitStr, 10) || 300);
    setLoading(true);
    setError("");
    setResult(null);
    setSavedPath(null);
    try {
      const res = await api.db02TopTables({ limit });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generating the table list failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleDownload() {
    if (!result?.rows?.length) return;
    setDownloading(true);
    setError("");
    try {
      const blob = await api.db02Export(result.rows);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "db02_top_tables.xlsx";
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Download failed");
    } finally {
      setDownloading(false);
    }
  }

  async function handleSave() {
    if (!result?.rows?.length) return;
    setSaving(true);
    setError("");
    setSavedPath(null);
    try {
      const res = await api.saveToInput(result.rows);
      setSavedPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  const rows = result?.rows ?? [];

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Generate Table List (DB02)</strong> — don't have a list of tables to
        check yet? This runs a SQL query in DB02's SQL Editor against the system's
        largest tables (by memory size) and their descriptions. Save the result to the
        input folder to use it directly in the Find Archiving Objects tab.
      </div>

      <form className="tx-form" onSubmit={handleGenerate}>
        <div className="form-row">
          <label>Top N tables</label>
          <input
            type="number"
            min={1}
            max={5000}
            value={limitStr}
            onChange={(e) => setLimitStr(e.target.value)}
          />
        </div>

        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Running SQL Editor query…" : "Generate List"}
        </button>

        {loading && <ProgressBar mode="indeterminate" label="Running SQL Editor query…" />}
      </form>

      {error && <p className="tx-error">{error}</p>}

      {rows.length > 0 && (
        <>
          <ResultsTable
            rows={rows}
            caption={`Top tables — ${rows.length} row${rows.length !== 1 ? "s" : ""}`}
          />

          <div className="action-row">
            <button
              type="button"
              className="btn btn-primary"
              onClick={handleSave}
              disabled={saving}
            >
              {saving ? "Saving…" : "Save to input folder"}
            </button>
            <button
              type="button"
              className="btn btn-secondary"
              onClick={handleDownload}
              disabled={downloading}
            >
              {downloading ? "Preparing…" : "Download Excel"}
            </button>
          </div>

          {savedPath && (
            <div className="saved-notice">
              Saved to <code>input/list_of_tables.xlsx</code> — available in the Find
              Archiving Objects tab.
            </div>
          )}
        </>
      )}
    </div>
  );
}
