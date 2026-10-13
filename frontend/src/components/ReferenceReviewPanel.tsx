import { useEffect, useRef, useState } from "react";
import { api, pollUntilDone } from "../api/client";
import { useScreenEpoch } from "../useScreenEpoch";
import SavedNotice from "./SavedNotice";
import SendToModelDialog from "./SendToModelDialog";
import type { ProgressSnapshot, ReferenceReview } from "../types";

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
  /** Which remembered screen state belongs to this review (see the backend's /api/ui-state). */
  stateKey: "ref-doc-tables" | "ref-doc-headers";
  /** Starts reading the documents in the backend; it keeps going if the screen is left. */
  start: (files: File[]) => Promise<void>;
  /** The backend's state of that analysis, with its result already in the shape of a review. */
  progress: () => Promise<ProgressSnapshot<ReferenceReview>>;
  /** Saves the final list into the task's results folder; resolves with the full path it was saved to. */
  save: (final: Row[]) => Promise<{ path: string }>;
  /** The row after the user accepts the reference document's value (*review* is the analysis result). */
  applyOverride: (row: Row, refValue: string, review: ReferenceReview) => Row;
}

export default function ReferenceReviewPanel({ config }: { config: ReferenceReviewConfig }) {
  const c = config;
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<ReferenceReview | null>(null);
  const [error, setError] = useState("");
  // Documents picked but not yet confirmed: nothing is sent to the AI model until the user confirms
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);

  // Mismatches the user selected for override (set of id values)
  const [selected, setSelected] = useState<Set<string>>(new Set());

  // Preview state after "Apply"
  const [previewRows, setPreviewRows] = useState<Row[] | null>(null);
  const [saving, setSaving] = useState(false);
  const [savedPath, setSavedPath] = useState<string | null>(null);
  // Bumped when the screen is left, so a poll started by this screen stops (the analysis goes on in the backend)
  const epoch = useScreenEpoch();

  // ── Following the analysis in the backend ────────────────────────────────

  /** Follow the analysis until it ends, then restore what the user had done with it (which step, what was
   *  ticked). Used after the documents are sent and when the screen is opened again. */
  async function follow(isCurrent: () => boolean) {
    setPhase("analyzing");
    setError("");
    try {
      const final = await pollUntilDone(c.progress, () => {}, 700, () => !isCurrent());
      if (!isCurrent()) return;
      if (final.status === "error" || !final.result) {
        setError(final.message ?? "Analysis failed");
        setPhase("idle");
        return;
      }
      setResult(final.result);
      const saved = (await api.getUiState(c.stateKey).catch(() => ({ state: null }))).state;
      if (!isCurrent()) return;
      const savedSelected = Array.isArray(saved?.selected) ? (saved.selected as string[]) : [];
      setSelected(new Set(savedSelected));
      if (saved?.phase === "preview" && Array.isArray(saved.previewRows)) {
        setPreviewRows(saved.previewRows as Row[]);
        setSavedPath(typeof saved.savedPath === "string" ? saved.savedPath : null);
        setPhase("preview");
      } else {
        setPhase("results");
      }
    } catch (err: unknown) {
      if (isCurrent()) {
        setError(err instanceof Error ? err.message : "Analysis failed");
        setPhase("idle");
      }
    }
  }

  // Pick up an analysis that is going or finished (the state lives in the backend, like the analysis tasks)
  useEffect(() => {
    const isCurrent = epoch.begin();
    c.progress()
      .then((s) => {
        if (isCurrent() && s.status !== "idle") follow(isCurrent);
      })
      .catch(() => {});
    return () => {
      epoch.leave();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Remember where the user is in the review (step, ticked mismatches, the preview) so it can be picked up again
  useEffect(() => {
    if (phase !== "results" && phase !== "preview") return;
    if (!result) return;
    api
      .putUiState(c.stateKey, {
        phase,
        selected: Array.from(selected),
        previewRows: phase === "preview" ? previewRows : null,
        savedPath,
      })
      .catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phase, selected, previewRows, savedPath]);

  // ── File pick ────────────────────────────────────────────────────────────

  async function handleFiles(files: File[]) {
    setPhase("analyzing");
    setError("");
    setResult(null);
    setSelected(new Set());
    setPreviewRows(null);
    setSavedPath(null);

    try {
      await c.start(files);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Analysis failed");
      setPhase("idle");
      return;
    }
    follow(epoch.current());
  }

  function handleFileChange(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    if (picked.length) setPendingFiles(picked);
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
        return c.applyOverride(row, refValue, result);
      }
      return row;
    });

    setPreviewRows(preview);
    setPhase("preview");
    void saveFinal(preview);
  }

  // ── Save (automatic, as soon as the final list is shown) ─────────────────

  async function saveFinal(rows: Row[]) {
    setSaving(true);
    setSavedPath(null);
    setError("");
    try {
      const res = await c.save(rows);
      setSavedPath(res.path);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Saving the results failed");
    } finally {
      setSaving(false);
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
            Cross-check recommendations against past project experience (Excel, PowerPoint, Word, PDF, CSV or text)
          </span>
        </div>
      </div>

      {/* ── Idle / upload ───────────────────────────────────────────── */}
      {(phase === "idle" || phase === "analyzing") && (
        <div className="ref-doc-upload-area">
          {phase === "idle" ? (
            <>
              <p className="ref-doc-upload-hint">{c.uploadHint}</p>
              <p className="ref-doc-upload-hint">
                <strong>The text of the documents is sent to the AI model.</strong> You will be asked to confirm that
                they contain no client data or other sensitive information.
              </p>
              <p className="ref-doc-upload-hint">
                You can select several documents at once. If two documents name different values for the
                same item, the one listed first is used and the comment says what the others suggested.
              </p>
              <label className="btn btn-secondary ref-doc-upload-btn">
                Upload Reference Document(s)
                <input
                  ref={fileInputRef}
                  type="file"
                  multiple
                  accept=".xlsx,.xlsm,.pdf,.pptx,.docx,.csv,.txt,.md"
                  onChange={handleFileChange}
                  style={{ display: "none" }}
                />
              </label>
            </>
          ) : (
            <div className="ref-doc-analyzing">
              <span className="ref-doc-spinner" />
              Analysing document(s) with the selected AI model…
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

          {result.warnings && result.warnings.length > 0 && (
            <p className="tx-error" style={{ margin: "8px 18px" }}>
              Skipped: {result.warnings.join("; ")}
            </p>
          )}

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
              Upload different document(s)
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
                    <td className="ref-doc-comment note-wrap">{row[c.commentKey] || ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {error && <p className="tx-error">{error}</p>}

          <div className="action-row" style={{ marginTop: 12 }}>
            <button
              className="btn btn-secondary"
              onClick={() => setPhase("results")}
            >
              ← Back to comparison
            </button>
          </div>

          {saving ? <p className="tx-hint">Saving the results…</p> : <SavedNotice path={savedPath} />}
        </>
      )}

      {pendingFiles && (
        <SendToModelDialog
          files={pendingFiles}
          onCancel={() => setPendingFiles(null)}
          onConfirm={() => {
            const files = pendingFiles;
            setPendingFiles(null);
            handleFiles(files);
          }}
        />
      )}
    </div>
  );
}
