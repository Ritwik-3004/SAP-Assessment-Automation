import { useState } from "react";
import { api } from "../api/client";
import ResultsTable from "./ResultsTable";
import type { Db15BatchResult } from "../types";

const ALL_TRANSACTIONS = ["DB15", "TAANA", "SE16N", "SE11", "AOBJ", "SARA"] as const;
type TxOption = (typeof ALL_TRANSACTIONS)[number];

const IMPLEMENTED: TxOption[] = ["DB15"];

export default function BatchArchivingPanel() {
  const [transaction, setTransaction] = useState<TxOption>("DB15");
  const [file, setFile] = useState<File | null>(null);
  const [result, setResult] = useState<Db15BatchResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [error, setError] = useState("");

  const isImplemented = IMPLEMENTED.includes(transaction);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !isImplemented) return;
    setLoading(true);
    setError("");
    setResult(null);
    try {
      const res = await api.db15Batch(file);
      setResult(res);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Batch DB15 run failed");
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
      a.download = "db15_archiving_objects.xlsx";
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
        <strong>Batch Table Archiving Analysis</strong> — upload an Excel file listing
        tables to check (Table Name in column A, Description in column B, header row
        first), pick a transaction, and run it against every table. Only DB15 is
        implemented so far; the others will follow the same pattern.
      </div>

      <form className="tx-form" onSubmit={handleSubmit}>
        <div className="form-row">
          <label>Transaction</label>
          <select
            value={transaction}
            onChange={(e) => setTransaction(e.target.value as TxOption)}
          >
            {ALL_TRANSACTIONS.map((tx) => (
              <option key={tx} value={tx} disabled={!IMPLEMENTED.includes(tx)}>
                {tx}
                {!IMPLEMENTED.includes(tx) ? " (coming soon)" : ""}
              </option>
            ))}
          </select>
        </div>

        <div className="form-row">
          <label>Table List (Excel)</label>
          <input
            type="file"
            accept=".xlsx,.xls"
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            required
          />
        </div>

        {!isImplemented && (
          <p className="tx-hint">
            Batch processing for {transaction} is not implemented yet — select DB15.
          </p>
        )}

        <button
          type="submit"
          className="btn btn-primary"
          disabled={loading || !file || !isImplemented}
        >
          {loading ? "Running DB15 for each table…" : "Submit"}
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

      {result?.rows && (
        <>
          <ResultsTable rows={result.rows} caption="Archiving objects by table" />
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
