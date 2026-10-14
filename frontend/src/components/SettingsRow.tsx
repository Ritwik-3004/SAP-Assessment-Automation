import type { ReactNode } from "react";
import { ChevronRight } from "./icons";

/** One folded row of the sidebar's Settings group: an icon, a title and a one-line summary (so the status is visible without
 *  opening it) and, when opened, the panel itself. The body stays mounted while closed so a half-typed form and the usage
 *  refresh are not lost. */
export default function SettingsRow({
  icon,
  title,
  summary,
  tone = "neutral",
  open,
  onToggle,
  children,
}: {
  icon: ReactNode;
  title: string;
  summary: string;
  /** colours the summary: ok (green dot), bad (red dot) or neutral */
  tone?: "neutral" | "ok" | "bad";
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <section className={`set-row${open ? " open" : ""}`}>
      <button type="button" className="set-row-head" onClick={onToggle} aria-expanded={open}>
        <span className="set-row-icon">{icon}</span>
        <span className="set-row-text">
          <strong>{title}</strong>
          <span className="set-row-summary" title={summary}>
            {tone !== "neutral" && <span className={`set-dot ${tone}`} aria-hidden="true" />}
            {summary}
          </span>
        </span>
        <span className="set-row-caret">
          <ChevronRight />
        </span>
      </button>
      <div className="set-row-body" hidden={!open}>
        {children}
      </div>
    </section>
  );
}
