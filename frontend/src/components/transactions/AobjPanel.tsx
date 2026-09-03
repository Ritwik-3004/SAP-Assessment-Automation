import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function AobjPanel() {
  const [filter, setFilter] = useState("");
  const [result, setResult] = useState<TransactionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.aobj({ object_filter: filter || undefined });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "AOBJ failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>AOBJ</strong> — Archiving Object Customizing. Lists all configured
        archiving objects with their write/delete programs, residence times, and settings.
      </div>

      <form className="tx-form" onSubmit={run}>
        <div className="form-row">
          <label>Object Filter (optional)</label>
          <input
            type="text"
            placeholder="e.g. FI_* (leave blank for all)"
            value={filter}
            onChange={(e) => setFilter(e.target.value.toUpperCase())}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Loading AOBJ…" : "List Archiving Objects"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {result?.rows && (
        <ResultsTable
          rows={result.rows}
          caption={filter ? `Archiving objects matching "${filter}"` : "All archiving objects"}
        />
      )}
    </div>
  );
}
