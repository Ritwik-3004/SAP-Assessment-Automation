import { useEffect, useState } from "react";
import { useScreenEpoch } from "../useScreenEpoch";
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
  // Bumped when the screen is left, so a poll started by this screen stops (the work itself goes on in the backend)
  const epoch = useScreenEpoch();

  /** Follow the query in the backend until it ends. Used after Generate and when the screen is opened again. */
  async function follow(isCurrent: () => boolean) {
    setLoading(true);
    try {
      const final = await api.pollTopTables(() => {}, () => !isCurrent());
      if (!isCurrent()) return;
      if (final.status === "error") setError(final.message ?? "Generating the table list failed");
      else setResult(final.result);
    } catch (err: unknown) {
      if (isCurrent()) setError(err instanceof Error ? err.message : "Generating the table list failed");
    } finally {
      if (isCurrent()) setLoading(false);
    }
  }

  // Pick up a run that is going or finished (the state lives in the backend, like the analysis tasks)
  useEffect(() => {
    const isCurrent = epoch.begin();
    api
      .topTablesProgress()
      .then((s) => {
        if (!isCurrent() || s.status === "idle") return;
        if (s.status === "running") follow(isCurrent);
        else if (s.status === "done") setResult(s.result);
        else setError(s.message ?? "Generating the table list failed");
      })
      .catch(() => {});
    return () => {
      epoch.leave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    const limit = Math.max(1, parseInt(limitStr, 10) || 300);
    setLoading(true);
    setError("");
    setResult(null);
    try {
      await api.topTablesStart({ limit });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generating the table list failed");
      setLoading(false);
      return;
    }
    follow(epoch.current());
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
