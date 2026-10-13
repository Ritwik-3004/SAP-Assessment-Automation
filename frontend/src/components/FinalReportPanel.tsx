import { useEffect, useState } from "react";
import { api } from "../api/client";
import ProgressBar from "./ProgressBar";
import SavedNotice from "./SavedNotice";
import type { FinalReportResult, FinalReportSource } from "../types";

const STATUS_TEXT: Record<FinalReportSource["status"], string> = {
  found: "Found",
  fallback: "Found (plain file)",
  missing: "Missing",
};

export default function FinalReportPanel() {
  const [sources, setSources] = useState<FinalReportSource[]>([]);
  const [loadingSources, setLoadingSources] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [result, setResult] = useState<FinalReportResult | null>(null);
  const [error, setError] = useState("");

  async function loadSources() {
    setLoadingSources(true);
    try {
      setSources((await api.finalReportSources()).sources);
    } catch {
      // Non-fatal: generating reports the problem
    } finally {
      setLoadingSources(false);
    }
  }

  useEffect(() => {
    loadSources();
  }, []);

  async function handleGenerate(e: React.FormEvent) {
    e.preventDefault();
    setGenerating(true);
    setError("");
    setResult(null);
    try {
      setResult(await api.finalReportGenerate());
      loadSources();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Generating the final report failed");
    } finally {
      setGenerating(false);
    }
  }

  const objectsMissing = sources.some((s) => s.status === "missing" && s.label.startsWith("Archiving objects"));

  return (
    <div className="tx-panel">
      <div className="tx-description">
        <strong>Final report generation</strong> — combines the important results of the other tasks into two Excel
        files. The <strong>Overview</strong> lists every archiving object with its description, header table, tables,
        archiving conditions, the objects to archive before it and sizes; whatever is common to an archiving object's tables is merged into one cell. A second sheet
        lists the <strong>housekeeping programs</strong> with their tables and sizes. The{" "}
        <strong>Object details</strong> file has one sheet per archiving object with its table analysis (taken from its
        header table) and, next to it, its open item analysis when there is one. Both files are saved automatically; you
        don't need to be connected to SAP for this step, because it only reads results the earlier tasks saved. Where a
        reference-document file exists it is used, otherwise the plain file.
      </div>

      <div className="table-wrapper">
        <p className="table-caption">
          Results this report is built from{" "}
          <button type="button" className="btn-refresh" onClick={loadSources} disabled={loadingSources} title="Check again">
            ↻
          </button>
        </p>
        <div className="table-scroll">
          <table className="results-table">
            <thead>
              <tr>
                <th>Content</th>
                <th>File used</th>
                <th>Status</th>
                <th>Note</th>
              </tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.label}>
                  <td>{s.label}</td>
                  <td>{s.file || "—"}</td>
                  <td>
                    <span className={`ta-status ${s.status === "missing" ? "failed" : "done"}`}>{STATUS_TEXT[s.status]}</span>
                  </td>
                  <td className="ref-doc-comment note-wrap">{s.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <form className="tx-form" onSubmit={handleGenerate}>
        <button type="submit" className="btn btn-primary" disabled={generating || objectsMissing}>
          {generating ? "Generating…" : "Generate final report"}
        </button>
        {generating && <ProgressBar mode="indeterminate" label="Reading the saved results and building the two files…" />}
      </form>

      {error && <p className="tx-error">{error}</p>}

      {result && (
        <>
          <SavedNotice paths={result.files.map((f) => f.path)} />
          <p className="tx-hint">
            The overview has a <em>Housekeeping Programs</em> sheet with {result.housekeeping.programs} program
            {result.housekeeping.programs !== 1 ? "s" : ""} and {result.housekeeping.tables} table
            {result.housekeeping.tables !== 1 ? "s" : ""}.
          </p>
          {result.warnings.map((w, i) => (
            <p key={i} className="tx-hint">
              {w}
            </p>
          ))}
          <div className="table-wrapper" style={{ marginTop: 12 }}>
            <p className="table-caption">
              Archiving objects in the report — {result.objects.length}
            </p>
            <div className="table-scroll">
              <table className="results-table">
                <thead>
                  <tr>
                    <th>Archiving object</th>
                    <th>Header table</th>
                    <th>Tables</th>
                    <th>Conditions</th>
                    <th>Table analysis</th>
                    <th>Open item analysis</th>
                  </tr>
                </thead>
                <tbody>
                  {result.objects.map((o) => (
                    <tr key={o.object}>
                      <td>
                        <strong>{o.object}</strong>
                        {o.description && <div className="tx-hint">{o.description}</div>}
                      </td>
                      <td>{o.header_table || "—"}</td>
                      <td>{o.tables}</td>
                      <td>{o.conditions}</td>
                      <td>{o.table_analysis ? "Yes" : "—"}</td>
                      <td>{o.open_items ? "Yes" : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
