import { api } from "../api/client";
import type { ScoredResult } from "../types";
import ReferenceReviewPanel, { type ReferenceReviewConfig } from "./ReferenceReviewPanel";

interface Props {
  scored: ScoredResult;
}

/** Reference-document review for Find Archiving Objects (table → archiving object).
 *  The UI itself lives in ReferenceReviewPanel; this only supplies the table/object wiring. */
export default function ReferenceDocPanel({ scored }: Props) {
  const recommended = scored.recommended ?? [];
  const allRows = scored.rows ?? [];

  /** Archiving object (upper case) → its real description, from any row of this run. Placeholders
   *  such as "(no archiving objects found)" start with "(" and are left out. */
  function knownDescriptions(): Record<string, string> {
    const out: Record<string, string> = {};
    for (const r of [...allRows, ...recommended]) {
      const obj = (r["Archiving Object"] || "").trim().toUpperCase();
      const desc = (r["Object Description"] || "").trim();
      if (obj && desc && !desc.startsWith("(") && !(obj in out)) out[obj] = desc;
    }
    return out;
  }

  const config: ReferenceReviewConfig = {
    rows: recommended,
    idKey: "Table Name",
    idLabel: "Table",
    valueKey: "Archiving Object",
    valueLabel: "Archiving Object",
    currentLabel: "Current Object",
    refLabel: "Reference Doc Object",
    refKey: "Ref Doc Object",
    commentKey: "Comments",
    commentLabel: "Comments",
    extraKey: "Score",
    extraLabel: "Score",
    uploadHint:
      "Upload one or more reference documents containing past archiving object recommendations. " +
      "The tool will compare it against your scored results and highlight any differences.",
    previewTitle: "Preview — Final Recommendations",
    savedPath: "output/archiving_objects_with_reference.xlsx",
    downloadName: "archiving_objects_with_reference.xlsx",
    analyze: async (files) => {
      const res = await api.analyzeReferenceDoc(files, recommended, knownDescriptions());
      return {
        filename: res.filename,
        warnings: res.warnings,
        annotated: res.annotated_recommended,
        matches: res.matches,
        mismatches: res.mismatches,
        not_in_ref: res.not_in_ref,
        objectDescriptions: res.object_descriptions,
      };
    },
    save: (final) => api.saveReferenceDoc(allRows, final),
    exportFile: (final) => api.exportReferenceDoc(allRows, final),
    // Taking the document's object must not leave the old object's details behind. The score comes
    // from the scored list when that table was scored against the new object, otherwise it is blank.
    // The description belongs to the object, so it is never blank: the scored pair's, else the one
    // resolved by the analysis (the run, the remembered list, SAP, or an "(AI-suggested)" one), else
    // the same object's description from any row. A housekeeping program is cleared (a table has
    // one or the other).
    applyOverride: (row, refValue, review) => {
      const scoredPair = allRows.find(
        (r) =>
          r["Table Name"] === row["Table Name"] &&
          (r["Archiving Object"] || "").toUpperCase() === refValue.toUpperCase()
      );
      const description =
        scoredPair?.["Object Description"]?.trim() ||
        review.objectDescriptions?.[refValue.toUpperCase()] ||
        knownDescriptions()[refValue.toUpperCase()] ||
        "(description not found)";
      const housekeeping = row["Housekeeping Program"];
      const previous = row["Archiving Object"]
        ? row["Archiving Object"]
        : housekeeping
        ? `(housekeeping program ${housekeeping})`
        : "(no archiving object)";
      return {
        ...row,
        "Archiving Object": refValue,
        "Object Description": description,
        Score: scoredPair?.Score ?? "",
        Rationale: "Chosen from the reference document.",
        "Housekeeping Program": "",
        Comments: `Updated from ${previous} per reference document`,
        "Ref Doc Object": "",
      };
    },
    groupedView: {
      fetch: async (rows) => (await api.groupByObject(rows)).rows,
      caption:
        "Grouped by Object — tables sharing an archiving object/housekeeping program, sorted by cumulative size (largest first; tables with neither are grouped last)",
    },
  };

  return <ReferenceReviewPanel config={config} />;
}
