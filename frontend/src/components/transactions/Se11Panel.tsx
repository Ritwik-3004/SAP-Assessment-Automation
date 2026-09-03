import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function Se11Panel() {
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
      const res = await api.se11({ table_name: tableName });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "SE11 failed");
    } finally {
      setLoading(false);
    }
  }

  const data = result?.fields ?? result?.rows;

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>SE11</strong> — ABAP Dictionary. View the technical field structure,
        data types, and key flags of any database table.
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
          {loading ? "Loading SE11…" : "Show Dictionary"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {data && (
        <ResultsTable rows={data} caption={`Field definitions for ${result?.table_name}`} />
      )}
    </div>
  );
}
