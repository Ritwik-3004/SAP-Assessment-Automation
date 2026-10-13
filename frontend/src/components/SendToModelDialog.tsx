import { useEffect, useRef, useState } from "react";
import { api } from "../api/client";
import type { LlmSettings } from "../types";

interface Props {
  /** The documents the user picked. Nothing is read or sent until the user confirms. */
  files: File[];
  onConfirm: () => void;
  onCancel: () => void;
}

/** Asked before a reference document is analysed: its text goes to the AI model, so the user must confirm that
 *  it holds no client data or other sensitive information. */
export default function SendToModelDialog({ files, onConfirm, onCancel }: Props) {
  const [settings, setSettings] = useState<LlmSettings | null>(null);
  const [checked, setChecked] = useState(false);
  const checkRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    api.getLlmSettings().then(setSettings).catch(() => {});
    checkRef.current?.focus();
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCancel();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onCancel]);

  const local = settings?.provider === "ollama";
  const plural = files.length !== 1;

  return (
    <div className="modal-backdrop" onMouseDown={(e) => e.target === e.currentTarget && onCancel()}>
      <div className="modal" role="dialog" aria-modal="true" aria-labelledby="send-to-model-title">
        <h3 id="send-to-model-title">Send {plural ? "these documents" : "this document"} to the AI model?</h3>

        <p>
          The text of {plural ? `the ${files.length} documents` : "the document"} you selected will be sent to{" "}
          {settings ? (
            <>
              <strong>{settings.label}</strong>
              {local ? ", which runs on this computer, so nothing leaves it" : ", an online service, so the text leaves this computer"}
            </>
          ) : (
            "the selected AI model"
          )}
          . It is read there and compared with your results.
        </p>

        <div className="modal-warning">
          <strong>Make sure {plural ? "they contain" : "it contains"} no client data or other sensitive information</strong>{" "}
          — for example customer or vendor names, amounts, personal data or confidential project details. Remove
          anything like that from the file first.
        </div>

        <ul className="modal-files">
          {files.map((f) => (
            <li key={f.name + f.size}>
              <span>📄 {f.name}</span>
              <span className="tx-hint">{f.size < 1024 * 1024 ? `${Math.max(1, Math.round(f.size / 1024))} KB` : `${(f.size / 1024 / 1024).toFixed(1)} MB`}</span>
            </li>
          ))}
        </ul>

        <label className="modal-check">
          <input ref={checkRef} type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
          <span>
            I have checked that {plural ? "these documents contain" : "this document contains"} no client data or
            sensitive information.
          </span>
        </label>

        <div className="modal-actions">
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={onConfirm} disabled={!checked}>
            Send to the AI model
          </button>
        </div>
      </div>
    </div>
  );
}
