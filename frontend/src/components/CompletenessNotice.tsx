import type { Completeness } from "../types";

const MAX_LINES = 8;

/** Says what the model's limits kept a result from covering completely: text that had to be shortened before it was
 *  sent, parts that could not be processed, and notes (for example an excerpt that was shortened). Renders nothing for a
 *  result that is complete. `hideUnprocessed` is for screens that already list what could not be processed. */
export default function CompletenessNotice({
  completeness,
  hideUnprocessed = false,
}: {
  completeness?: Completeness;
  hideUnprocessed?: boolean;
}) {
  if (!completeness) return null;
  const lines: string[] = [
    ...(completeness.trimmed ?? []).map(
      (t) => `${t.what}: only ${t.kept.toLocaleString()} of ${t.total.toLocaleString()} ${t.unit} could be sent to the model.`
    ),
    ...(hideUnprocessed ? [] : (completeness.unprocessed ?? []).map((u) => `${u.item}: not processed (${u.reason}).`)),
    ...(completeness.notes ?? []),
  ];
  if (lines.length === 0) return null;
  const incomplete = (completeness.trimmed ?? []).length > 0 || (!hideUnprocessed && (completeness.unprocessed ?? []).length > 0);
  return (
    <div className="completeness-notice" role="status">
      <strong>{incomplete ? "Part of this was not read or processed because of the model's limits:" : "Note:"}</strong>
      <ul>
        {lines.slice(0, MAX_LINES).map((line, i) => (
          <li key={i}>{line}</li>
        ))}
        {lines.length > MAX_LINES && <li>… and {lines.length - MAX_LINES} more.</li>}
      </ul>
    </div>
  );
}
