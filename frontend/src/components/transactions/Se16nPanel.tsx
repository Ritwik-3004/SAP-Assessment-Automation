import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function Se16nPanel() {
  const [tableName, setTableName] = useState("");
  const [maxRows, setMaxRows] = useState(200);
  const [whereClause, setWhereClause] = useState("");
  const [result, setResult] = useState<TransactionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!tableName.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.se16n({
        table_name: tableName,
        max_rows: maxRows,
        where_clause: whereClause || undefined,
      });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "SE16N failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>SE16N</strong> — General Table Display. Browse the contents of any
        transparent table with optional filters.
      </div>

      <form className="tx-form" onSubmit={run}>
        <div className="form-row">
          <label>Table Name</label>
          <input
            type="text"
            placeholder="e.g. BKPF"
            value={tableName}
            onChange={(e) => setTableName(e.target.value.toUpperCase())}
            required
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
        <div className="form-row">
          <label>WHERE Clause (optional)</label>
          <input
            type="text"
            placeholder="e.g. GJAHR = '2023'"
            value={whereClause}
            onChange={(e) => setWhereClause(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Running SE16N…" : "Display Table"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {result?.rows && (
        <ResultsTable rows={result.rows} caption={`Contents of ${result.table_name}`} />
      )}
    </div>
  );
}
