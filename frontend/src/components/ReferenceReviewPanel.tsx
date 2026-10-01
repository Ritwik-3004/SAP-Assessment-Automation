import { useRef, useState } from "react";
import type { ReferenceReview } from "../types";

type Row = Record<string, string>;
type Phase = "idle" | "analyzing" | "results" | "preview";

/** Everything that differs between "table → archiving object" and
 *  "archiving object → header table" reference-document review. */
export interface ReferenceReviewConfig {
  /** The list being reviewed (sent to the backend for comparison). */
  rows: Row[];
  /** Column that identifies a row (also the React key and the selection key). */
  idKey: string;
  idLabel: string;
  /** Column the reference document can override. */
  valueKey: string;
  valueLabel: string;
  currentLabel: string;
  refLabel: string;
  /** Backend-added column holding the document's suggestion. */
  refKey: string;
  /** Column that carries the comparison note ("Matches…", "Updated from…"). */
  commentKey: string;
  commentLabel: string;
  /** A supporting column shown next to the value (Score / Confidence). */
  extraKey: string;
  extraLabel: string;
  uploadHint: string;
  previewTitle: string;
  savedPath: string;
  downloadName: string;
  analyze: (file: File) => Promise<ReferenceReview>;
  save: (final: Row[]) => Promise<{ path: string }>;
  exportFile: (final: Row[]) => Promise<Blob>;
  /** The row after the user accepts the reference document's value. */
  applyOverride: (row: Row, refValue: string) => Row;
}

export default function ReferenceReviewPanel({ config }: { config: ReferenceReviewConfig }) {
  const c = config;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<ReferenceReview | null>(null);
  const [error, setError] = useState("");

  // Mismatches the user selected for override (set of id values)
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Preview state after "Apply"
  const [previewRows, setPreviewRows] = useState<Row[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  const [exporting, setExporting] = useState(false);

  // ── File pick ────────────────────────────────────────────────────────────

  async function handleFile(file: File) {
    setPhase("analyzing");
    setError("");
    setResult(null);
    setSelected(new Set());
    setPreviewRows(null);
    setSavedPath(null);

    try {
      const res = await c.analyze(file);
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

  function toggle(id: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set((result?.mismatches ?? []).map((r) => r[c.idKey])));
  }

  function deselectAll() {
    setSelected(new Set());
  }

  // ── Apply changes ────────────────────────────────────────────────────────

  function applyChanges() {
    if (!result) return;

    const preview = result.annotated.map((row) => {
      const refValue = row[c.refKey];
      if (selected.has(row[c.idKey]) && refValue) {
        return c.applyOverride(row, refValue);
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
      const res = await c.save(previewRows);
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
      const blob = await c.exportFile(previewRows);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = c.downloadName;
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
              <p className="ref-doc-upload-hint">{c.uploadHint}</p>
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
                      <th>{c.idLabel}</th>
                      <th>{c.currentLabel}</th>
                      <th>{c.refLabel}</th>
                      <th>{c.extraLabel}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {mismatches.map((row) => {
                      const id = row[c.idKey];
                      const checked = selected.has(id);
                      return (
                        <tr
                          key={id}
                          className={checked ? "ref-doc-row-selected" : "ref-doc-row-mismatch"}
                          onClick={() => toggle(id)}
                          style={{ cursor: "pointer" }}
                        >
                          <td style={{ textAlign: "center" }}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={() => toggle(id)}
                              onClick={(e) => e.stopPropagation()}
                            />
                          </td>
                          <td><strong>{id}</strong></td>
                          <td>{row[c.valueKey] || "—"}</td>
                          <td className="ref-doc-ref-obj">{row[c.refKey]}</td>
                          <td>{row[c.extraKey] || "—"}</td>
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
                    <tr><th>{c.idLabel}</th><th>{c.valueLabel}</th><th>{c.extraLabel}</th></tr>
                  </thead>
                  <tbody>
                    {matches.map((row) => (
                      <tr key={row[c.idKey]} className="ref-doc-row-match">
                        <td>{row[c.idKey]}</td>
                        <td>{row[c.valueKey]}</td>
                        <td>{row[c.extraKey] || "—"}</td>
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
                    <tr><th>{c.idLabel}</th><th>{c.valueLabel}</th><th>{c.extraLabel}</th></tr>
                  </thead>
                  <tbody>
                    {notInRef.map((row) => (
                      <tr key={row[c.idKey]}>
                        <td>{row[c.idKey]}</td>
                        <td>{row[c.valueKey] || "—"}</td>
                        <td>{row[c.extraKey] || "—"}</td>
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
            <span className="ref-doc-badge ref-doc-badge-match">{c.previewTitle}</span>
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
                  <th>{c.idKey}</th>
                  <th>{c.valueKey}</th>
                  <th>{c.extraLabel}</th>
                  <th>{c.commentLabel}</th>
                </tr>
              </thead>
              <tbody>
                {previewRows.map((row) => (
                  <tr
                    key={row[c.idKey]}
                    className={
                      row[c.commentKey]?.startsWith("Updated")
                        ? "ref-doc-row-selected"
                        : row[c.commentKey]?.startsWith("Matches")
                        ? "ref-doc-row-match"
                        : ""
                    }
                  >
                    <td>{row[c.idKey]}</td>
                    <td>{row[c.valueKey] || "—"}</td>
                    <td>{row[c.extraKey] || "—"}</td>
                    <td className="ref-doc-comment">{row[c.commentKey] || ""}</td>
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
              Saved to <code>{c.savedPath}</code>
            </div>
          )}
        </>
      )}
    </div>
  );
}
