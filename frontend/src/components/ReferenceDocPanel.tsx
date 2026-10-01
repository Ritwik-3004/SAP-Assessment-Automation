import { useRef, useState } from "react";
import { api } from "../api/client";
import type { ReferenceDocResult, ScoredResult } from "../types";

interface Props {
  scored: ScoredResult;
}

type Phase = "idle" | "analyzing" | "results" | "preview";

export default function ReferenceDocPanel({ scored }: Props) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<ReferenceDocResult | null>(null);
  const [error, setError] = useState("");

  // Mismatches selected by the user for override (set of Table Name values)
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Preview state after "Apply Selected"
  const [previewRows, setPreviewRows] = useState<Record<string, string>[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  const recommended = scored.recommended ?? [];
  const allRows = scored.rows ?? [];

  // ── File pick ────────────────────────────────────────────────────────────

  async function handleFile(file: File) {
    setPhase("analyzing");
    setError("");
    setResult(null);
    setSelected(new Set());
    setPreviewRows(null);
    setSavedPath(null);

    try {
      const res = await api.analyzeReferenceDoc(file, recommended);
      setResult(res);
      setPhase("results");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Analysis failed");
      setPhase("idle");
    }
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const f = e.target.files?.[0];
    if (f) handleFile(f);
    if (fileInputRef.current) fileInputRef.current.value = "";
  }

  // ── Checkbox toggle ──────────────────────────────────────────────────────

  function toggle(tableName: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(tableName)) next.delete(tableName);
      else next.add(tableName);
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set((result?.mismatches ?? []).map((r) => r["Table Name"])));
  }

  function deselectAll() {
    setSelected(new Set());
  }

  // ── Apply changes ────────────────────────────────────────────────────────

  function applyChanges() {
    if (!result) return;

    const preview = result.annotated_recommended.map((row) => {
      const tableName = row["Table Name"];
      const refObj = row["Ref Doc Object"];
      if (selected.has(tableName) && refObj) {
        return {
          ...row,
          "Archiving Object": refObj,
          Comments: `Updated from ${row["Archiving Object"]} per reference document`,
          "Ref Doc Object": "",
        };
      }
      return row;
    });

    setPreviewRows(preview);
    setPhase("preview");
  }

  // ── Save / export ────────────────────────────────────────────────────────

  async function handleSave() {
    if (!previewRows) return;
    setSaving(true);
    setSavedPath(null);
    try {
      const res = await api.saveReferenceDoc(allRows, previewRows);
      setSavedPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Save failed");
    } finally {
      setSaving(false);
    }
  }

  async function handleExport() {
    if (!previewRows) return;
    setExporting(true);
    try {
      const blob = await api.exportReferenceDoc(allRows, previewRows);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "archiving_objects_with_reference.xlsx";
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

  // ── Render ───────────────────────────────────────────────────────────────

  const mismatches = result?.mismatches ?? [];
  const matches = result?.matches ?? [];
  const notInRef = result?.not_in_ref ?? [];

  return (
    <div className="ref-doc-panel">
      <div className="ref-doc-header">
        <span className="ref-doc-icon">📎</span>
        <div>
          <span className="ref-doc-title">Reference Document Analysis</span>
          <span className="ref-doc-subtitle">
            Cross-check recommendations against past project experience (Excel, PDF, or PowerPoint)
          </span>
        </div>
      </div>

      {/* ── Idle / upload ───────────────────────────────────────────── */}
      {(phase === "idle" || phase === "analyzing") && (
        <div className="ref-doc-upload-area">
          {phase === "idle" ? (
            <>
              <p className="ref-doc-upload-hint">
                Upload a reference document containing past archiving object recommendations.
                The tool will compare it against your scored results and highlight any differences.
              </p>
              <label className="btn btn-secondary ref-doc-upload-btn">
                Upload Reference Document
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".xlsx,.xls,.pdf,.pptx,.ppt"
                  onChange={handleFileChange}
                  style={{ display: "none" }}
                />
              </label>
            </>
          ) : (
            <div className="ref-doc-analyzing">
              <span className="ref-doc-spinner" />
              Analysing document with the selected AI model…
            </div>
          )}
          {error && <p className="tx-error" style={{ marginTop: "8px" }}>{error}</p>}
        </div>
      )}

      {/* ── Results ─────────────────────────────────────────────────── */}
      {phase === "results" && result && (
        <>
          <div className="ref-doc-summary">
            <span className="ref-doc-badge ref-doc-badge-match">
              ✓ {matches.length} matched
            </span>
            <span className="ref-doc-badge ref-doc-badge-mismatch">
              ⚠ {mismatches.length} mismatch{mismatches.length !== 1 ? "es" : ""}
            </span>
            <span className="ref-doc-badge ref-doc-badge-unknown">
              — {notInRef.length} not in reference doc
            </span>
            <span className="ref-doc-filename">📄 {result.filename}</span>
          </div>

          {/* Mismatches */}
          {mismatches.length > 0 && (
            <div className="ref-doc-section">
              <div className="ref-doc-section-header">
                <strong>⚠ Mismatches — select to override with reference document</strong>
                <div className="ref-doc-select-row">
                  <button className="btn-link" onClick={selectAll}>Select all</button>
                  <button className="btn-link" onClick={deselectAll}>Deselect all</button>
                </div>
              </div>
              <div className="table-scroll">
                <table className="results-table">
                  <thead>
                    <tr>
                      <th style={{ width: 36 }}>Override</th>
                      <th>Table</th>
                      <th>Current Object</th>
                      <th>Reference Doc Object</th>
                      <th>Score</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mismatches.map((row) => {
                      const tbl = row["Table Name"];
                      const checked = selected.has(tbl);
                      return (
                        <tr
                          key={tbl}
                          className={checked ? "ref-doc-row-selected" : "ref-doc-row-mismatch"}
                          onClick={() => toggle(tbl)}
                          style={{ cursor: "pointer" }}
                        >
                          <td style={{ textAlign: "center" }}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggle(tbl)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </td>
                          <td><strong>{tbl}</strong></td>
                          <td>{row["Archiving Object"] || "—"}</td>
                          <td className="ref-doc-ref-obj">{row["Ref Doc Object"]}</td>
                          <td>{row["Score"] || "—"}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* Matches (collapsed by default) */}
          {matches.length > 0 && (
            <details className="ref-doc-collapsible">
              <summary>✓ Matched ({matches.length})</summary>
              <div className="table-scroll" style={{ marginTop: 8 }}>
                <table className="results-table">
                  <thead>
                    <tr><th>Table</th><th>Archiving Object</th><th>Score</th></tr>
                  </thead>
                  <tbody>
                    {matches.map((row) => (
                      <tr key={row["Table Name"]} className="ref-doc-row-match">
                        <td>{row["Table Name"]}</td>
                        <td>{row["Archiving Object"]}</td>
                        <td>{row["Score"] || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}

          {/* Not in reference doc (collapsed) */}
          {notInRef.length > 0 && (
            <details className="ref-doc-collapsible">
              <summary>— Not in reference document ({notInRef.length})</summary>
              <div className="table-scroll" style={{ marginTop: 8 }}>
                <table className="results-table">
                  <thead>
                    <tr><th>Table</th><th>Archiving Object</th><th>Score</th></tr>
                  </thead>
                  <tbody>
                    {notInRef.map((row) => (
                      <tr key={row["Table Name"]}>
                        <td>{row["Table Name"]}</td>
                        <td>{row["Archiving Object"] || "—"}</td>
                        <td>{row["Score"] || "—"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          )}

          <div className="action-row" style={{ marginTop: 16 }}>
            <button className="btn btn-primary" onClick={applyChanges}>
              {selected.size > 0
                ? `Apply ${selected.size} override${selected.size !== 1 ? "s" : ""}`
                : "Keep as-is (no overrides)"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => {
                setPhase("idle");
                setResult(null);
                setSelected(new Set());
              }}
            >
              Upload different document
            </button>
          </div>
        </>
      )}

      {/* ── Preview ─────────────────────────────────────────────────── */}
      {phase === "preview" && previewRows && (
        <>
          <div className="ref-doc-summary">
            <span className="ref-doc-badge ref-doc-badge-match">Preview — Final Recommendations</span>
            {selected.size > 0 && (
              <span className="ref-doc-badge ref-doc-badge-mismatch">
                {selected.size} override{selected.size !== 1 ? "s" : ""} applied
              </span>
            )}
          </div>

          <div className="table-scroll">
            <table className="results-table">
              <thead>
                <tr>
                  <th>Table Name</th>
                  <th>Archiving Object</th>
                  <th>Score</th>
                  <th>Comments</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.map((row) => (
                  <tr
                    key={row["Table Name"]}
                    className={
                      row["Comments"]?.startsWith("Updated")
                        ? "ref-doc-row-selected"
                        : row["Comments"]?.startsWith("Matches")
                        ? "ref-doc-row-match"
                        : ""
                    }
                  >
                    <td>{row["Table Name"]}</td>
                    <td>{row["Archiving Object"] || "—"}</td>
                    <td>{row["Score"] || "—"}</td>
                    <td className="ref-doc-comment">{row["Comments"] || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {error && <p className="tx-error">{error}</p>}

          <div className="action-row" style={{ marginTop: 12 }}>
            <button className="btn btn-primary" onClick={handleSave} disabled={saving}>
              {saving ? "Saving…" : "Save to output folder"}
            </button>
            <button className="btn btn-secondary" onClick={handleExport} disabled={exporting}>
              {exporting ? "Preparing…" : "Download Excel"}
            </button>
            <button
              className="btn btn-secondary"
              onClick={() => setPhase("results")}
            >
              ← Back to comparison
            </button>
          </div>

          {savedPath && (
            <div className="saved-notice">
              Saved to <code>output/archiving_objects_with_reference.xlsx</code>
            </div>
          )}
        </>
      )}
    </div>
  );
}
