import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { ObjectReferenceReview as Review } from "../types";

type ReviewState = { status: "idle" | "running" | "done" | "error"; message: string | null; filename: string; warnings: string[] };

interface Props {
  /** The analysis (step 1) has finished, so there is something to check a document against. */
  enabled: boolean;
  /** Called after changes were applied, so the preview / Excel summary can refresh. */
  onApplied: () => void;
}

/** Reference document(s) for the Archiving Object Analysis: upload any readable document, see what it confirms,
 *  the gaps it fills and where it differs, and apply only the items the user ticks. */
export default function ObjectReferenceReview({ enabled, onApplied }: Props) {
  const [state, setState] = useState<ReviewState>({ status: "idle", message: null, filename: "", warnings: [] });
  const [review, setReview] = useState<Review | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [applying, setApplying] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const running = state.status === "running";

  async function refresh() {
    try {
      const r = await api.objectReferenceReview();
      setState(r.state);
      setReview(r.review);
    } catch {
      // non-fatal
    }
  }

  // Pick up a check that is already running / finished (e.g. after coming back to the task)
  useEffect(() => {
    refresh();
  }, []);

  useEffect(() => {
    if (!running) return;
    const timer = setInterval(refresh, 1500);
    return () => clearInterval(timer);
  }, [running]);

  async function handleFiles(e: React.ChangeEvent<HTMLInputElement>) {
    const picked = Array.from(e.target.files ?? []);
    if (fileInputRef.current) fileInputRef.current.value = "";
    if (!picked.length) return;
    setError("");
    setNotice("");
    setSelected(new Set());
    setReview(null);
    try {
      await api.objectReferenceAnalyze(picked);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not start the reference check");
    }
  }

  const toggle = (id: string) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  // everything the user could apply: gaps and mismatches not yet applied
  const selectable: string[] = (review?.objects ?? []).flatMap((o) => [
    ...o.dependencies.only_document.filter((d) => !d.applied).map((d) => d.id),
    ...o.conditions.new.filter((c) => !c.applied).map((c) => c.id),
    ...o.conditions.conflicts.filter((c) => !c.applied).map((c) => c.id),
  ]);

  async function apply() {
    setApplying(true);
    setError("");
    try {
      const res = await api.objectReferenceApply(Array.from(selected));
      setNotice(`Applied ${res.applied} change${res.applied !== 1 ? "s" : ""}. The preview and the Excel file are updated.`);
      setSelected(new Set());
      await refresh();
      onApplied();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Could not apply the changes");
    } finally {
      setApplying(false);
    }
  }

  const inDocument = (review?.objects ?? []).filter((o) => o.in_document);

  return (
    <div className="oa-ref-card">
      <div className="oa-ref-head">📎 Reference document(s)</div>
      <div className="oa-ref-body">
        <p className="tx-hint">
          Upload past analysis or SME notes to fill gaps and spot mismatches. Any kind of document works — Excel,
          PowerPoint, Word, PDF, CSV or text — and you can select several. The tool reads what each says about the
          archiving conditions and the objects to archive first, compares it with the results above, and changes
          nothing until you tick what to apply.
        </p>
        <div className="action-row">
          <label className={`btn btn-secondary ${!enabled || running ? "disabled" : ""}`} style={{ cursor: enabled && !running ? "pointer" : "not-allowed" }}>
            {review ? "Upload different document(s)" : "Upload reference document(s)"}
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept=".xlsx,.xlsm,.pdf,.pptx,.docx,.csv,.txt,.md"
              onChange={handleFiles}
              disabled={!enabled || running}
              style={{ display: "none" }}
            />
          </label>
        </div>
        {!enabled && <p className="tx-hint">Available once the analysis has finished.</p>}

        {running && (
          <div className="ref-doc-analyzing">
            <span className="ref-doc-spinner" />
            {state.message ?? "Checking the document(s)…"}
          </div>
        )}
        {error && <p className="tx-error">{error}</p>}
        {state.status === "error" && <p className="tx-error">{state.message}</p>}
        {state.warnings.map((w, i) => (
          <p key={i} className="tx-error">{w}</p>
        ))}

        {review && state.status === "done" && (
          <>
            <div className="ref-doc-summary" style={{ margin: 0, padding: 0, border: "none", background: "transparent" }}>
              <span className="ref-doc-badge ref-doc-badge-match">✓ {review.summary.confirmed} confirmed</span>
              <span className="ref-doc-badge ref-doc-badge-mismatch">+ {review.summary.gaps} gap{review.summary.gaps !== 1 ? "s" : ""} the document fills</span>
              <span className="ref-doc-badge ref-doc-badge-mismatch">⚠ {review.summary.mismatches} mismatch{review.summary.mismatches !== 1 ? "es" : ""}</span>
              <span className="ref-doc-filename">📄 {review.summary.files.join(", ")}</span>
            </div>
            {review.summary.not_in_document.length > 0 && (
              <p className="tx-hint">
                Not mentioned in the document: {review.summary.not_in_document.join(", ")}
              </p>
            )}

            {inDocument.length === 0 && (
              <p className="tx-hint">The document does not say anything about the archiving objects analysed.</p>
            )}

            {inDocument.map((o) => {
              const c = o.conditions;
              const d = o.dependencies;
              const gaps = d.only_document.length + c.new.length;
              const confirmed = d.confirmed.length + c.covered.length;
              return (
                <details key={o.object} className="oa-details" open={gaps + c.conflicts.length > 0}>
                  <summary>
                    <strong>{o.object}</strong>
                    <span className="tx-hint"> · {confirmed} confirmed · {gaps} gap{gaps !== 1 ? "s" : ""} · {c.conflicts.length} mismatch{c.conflicts.length !== 1 ? "es" : ""}</span>
                  </summary>
                  <div className="oa-body">
                    {(d.confirmed.length > 0 || d.only_tool.length > 0 || d.only_document.length > 0) && (
                      <div className="oa-before">
                        <h4>Objects to archive first</h4>
                        {d.confirmed.map((n) => <span key={n} className="oa-chip ok">✓ {n}</span>)}
                        {d.only_tool.map((n) => <span key={n} className="oa-chip muted" title="In SAP's network but not mentioned in the document">{n} (not in document)</span>)}
                        {d.only_document.map((g) => (
                          <label key={g.id} className="oa-ref-check">
                            <input type="checkbox" checked={g.applied || selected.has(g.id)} disabled={g.applied} onChange={() => toggle(g.id)} />
                            <span>
                              <strong>{g.object}</strong> — the document (<em>{g.file}</em>) says it must be archived before {o.object}, but SAP's network doesn't list it
                              {g.applied && <small>applied ✓</small>}
                            </span>
                          </label>
                        ))}
                      </div>
                    )}

                    {(c.new.length > 0 || c.conflicts.length > 0 || c.covered.length > 0) && (
                      <div className="oa-conds">
                        <h4>Archiving conditions</h4>
                        {c.conflicts.map((g) => (
                          <label key={g.id} className="oa-ref-check">
                            <input type="checkbox" checked={g.applied || selected.has(g.id)} disabled={g.applied} onChange={() => toggle(g.id)} />
                            <span>
                              <strong>⚠ Differs.</strong> Document (<em>{g.file}</em>): {g.document}
                              <small>Tool: {g.tool}{g.note ? ` — ${g.note}` : ""}</small>
                              <small>Ticking replaces the tool's wording with the document's.</small>
                              {g.applied && <small>applied ✓</small>}
                            </span>
                          </label>
                        ))}
                        {c.new.map((g) => (
                          <label key={g.id} className="oa-ref-check">
                            <input type="checkbox" checked={g.applied || selected.has(g.id)} disabled={g.applied} onChange={() => toggle(g.id)} />
                            <span>
                              <strong>+ Gap.</strong> {g.text}
                              <small>From the document: {g.file}; the tool found nothing like it.</small>
                              {g.applied && <small>applied ✓</small>}
                            </span>
                          </label>
                        ))}
                        {c.covered.length > 0 && (
                          <details>
                            <summary className="tx-hint">✓ {c.covered.length} condition(s) the document confirms</summary>
                            {c.covered.map((g, i) => (
                              <div key={i} className="oa-ref-check"><span>{g.document}<small>Matches: {g.tool}</small></span></div>
                            ))}
                          </details>
                        )}
                      </div>
                    )}
                  </div>
                </details>
              );
            })}

            <div className="action-row">
              <button className="btn btn-primary" onClick={apply} disabled={selected.size === 0 || applying}>
                {applying ? "Applying…" : `Apply ${selected.size} selected`}
              </button>
              <button className="btn btn-secondary" onClick={() => setSelected(new Set(selectable))} disabled={selectable.length === 0}>
                Select all gaps and mismatches
              </button>
              <button className="btn btn-secondary" onClick={() => setSelected(new Set())} disabled={selected.size === 0}>
                Clear selection
              </button>
            </div>
            {notice && <div className="saved-notice">{notice}</div>}
          </>
        )}
      </div>
    </div>
  );
}
