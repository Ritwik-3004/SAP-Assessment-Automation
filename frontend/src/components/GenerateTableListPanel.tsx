import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import ProgressBar from "./ProgressBar";
import SavedNotice from "./SavedNotice";
import type { Db02TopTablesResult } from "../types";

export default function GenerateTableListPanel() {
  const [limitStr, setLimitStr] = useState("300");
  const [result, setResult] = useState<Db02TopTablesResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    const limit = Math.max(1, parseInt(limitStr, 10) || 300);
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

  const rows = result?.rows ?? [];

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Generate Table List</strong> — don't have a list of tables to
        check yet? This runs a SQL query in SAP against the system's
        largest tables (by memory size) and their descriptions. The result is saved
        automatically and is available in the Find Archiving Objects for Tables task.
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

          <SavedNotice path={result?.saved_path} error={result?.save_error} />
        </>
      )}
    </div>
  );
}
