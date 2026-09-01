import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function TaanaPanel() {
  const [tableName, setTableName] = useState("");
  const [maxRows, setMaxRows] = useState(500);
  const [result, setResult] = useState<TransactionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.taana({ table_name: tableName || undefined, max_rows: maxRows });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "TAANA failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>TAANA</strong> — Database Table Analysis. Provides row counts, table sizes,
        and archivability metadata for selected tables.
      </div>

      <form className="tx-form" onSubmit={run}>
        <div className="form-row">
          <label>Table Name (optional)</label>
          <input
            type="text"
            placeholder="e.g. BKPF or leave blank for all"
            value={tableName}
            onChange={(e) => setTableName(e.target.value.toUpperCase())}
          />
        </div>
        <div className="form-row">
          <label>Max Rows</label>
          <input
            type="number"
            min={1}
            max={9999}
            value={maxRows}
            onChange={(e) => setMaxRows(Number(e.target.value))}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Running TAANA…" : "Execute TAANA"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {result?.rows && <ResultsTable rows={result.rows} caption="TAANA Analysis Results" />}
    </div>
  );
}
