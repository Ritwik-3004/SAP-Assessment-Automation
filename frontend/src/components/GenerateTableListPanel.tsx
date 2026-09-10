import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import type { Db02TopTablesResult } from "../types";

const PREVIEW_ROWS = 20;

export default function GenerateTableListPanel() {
  const [limit, setLimit] = useState(300);
  const [result, setResult] = useState<Db02TopTablesResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.db02TopTables({ limit });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generating the table list failed");
    } finally {
      setLoading(false);
    }
  }

  async function handleExport() {
    if (!result?.rows?.length) return;
    setExporting(true);
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
      setError(err instanceof Error ? err.message : "Export failed");
    } finally {
      setExporting(false);
    }
  }

  const rows = result?.rows ?? [];
  const preview = rows.slice(0, PREVIEW_ROWS);

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Generate Table List (DB02)</strong> — don't have a list of tables to
        check yet? This runs a SQL query in DB02's SQL Editor against the system's
        largest tables (by memory size) and their descriptions. Download the result and
        feed it straight into the Batch Archiving Analysis tab.
      </div>

      <form className="tx-form" onSubmit={handleGenerate}>
        <div className="form-row">
          <label>Top N tables</label>
          <input
            type="number"
            min={1}
            max={5000}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value) || 1)}
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
            rows={preview}
            caption={`Preview — showing ${preview.length} of ${rows.length} rows`}
          />
          <button
            type="button"
            className="btn btn-secondary"
            onClick={handleExport}
            disabled={exporting}
          >
            {exporting ? "Preparing file…" : "Download Excel"}
          </button>
        </>
      )}
    </div>
  );
}
