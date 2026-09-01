import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function Db15Panel() {
  const [tableName, setTableName] = useState("");
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
      const res = await api.db15({ table_name: tableName });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "DB15 failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>DB15</strong> — Find Archiving Objects. Shows which archiving objects
        reference a given database table.
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
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Running DB15…" : "Find Archiving Objects"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {result?.rows && (
        <ResultsTable
          rows={result.rows}
          caption={`Archiving objects for table ${result.table_name}`}
        />
      )}
    </div>
  );
}
