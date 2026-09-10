import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import type { Db15BatchResult } from "../types";

const PREVIEW_ROWS = 20;

export default function BatchArchivingPanel() {
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Db15BatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.db15Batch(file);
      setResult(res);
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
        </>
      )}
    </div>
  );
}
