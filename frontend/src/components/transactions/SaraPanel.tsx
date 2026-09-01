import { useState } from "react";
import { api } from "../../api/client";
import ResultsTable from "../ResultsTable";
import type { TransactionResult } from "../../types";

export default function SaraPanel() {
  const [archivingObject, setArchivingObject] = useState("");
  const [result, setResult] = useState<TransactionResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function run(e: React.FormEvent) {
    e.preventDefault();
    if (!archivingObject.trim()) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.sara({ archiving_object: archivingObject });
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "SARA failed");
    } finally {
      setLoading(false);
    }
  }

  const data = result?.sessions ?? result?.rows;

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>SARA</strong> — Archive Administration. Shows archiving sessions,
        their status, record counts, and file sizes for a given archiving object.
      </div>

      <form className="tx-form" onSubmit={run}>
        <div className="form-row">
          <label>Archiving Object</label>
          <input
            type="text"
            placeholder="e.g. FI_DOCUMNT"
            value={archivingObject}
            onChange={(e) => setArchivingObject(e.target.value.toUpperCase())}
            required
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={loading}>
          {loading ? "Loading SARA…" : "Show Archive Sessions"}
        </button>
      </form>

      {error && <p className="tx-error">{error}</p>}
      {data && (
        <ResultsTable
          rows={data}
          caption={`Archive sessions for ${result?.archiving_object}`}
        />
      )}
    </div>
  );
}
