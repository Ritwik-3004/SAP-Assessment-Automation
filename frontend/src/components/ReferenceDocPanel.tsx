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
      "Upload a reference document containing past archiving object recommendations. " +
      "The tool will compare it against your scored results and highlight any differences.",
    previewTitle: "Preview — Final Recommendations",
    savedPath: "output/archiving_objects_with_reference.xlsx",
    downloadName: "archiving_objects_with_reference.xlsx",
    analyze: async (file) => {
      const res = await api.analyzeReferenceDoc(file, recommended);
      return {
        filename: res.filename,
        annotated: res.annotated_recommended,
        matches: res.matches,
        mismatches: res.mismatches,
        not_in_ref: res.not_in_ref,
      };
    },
    save: (final) => api.saveReferenceDoc(allRows, final),
    exportFile: (final) => api.exportReferenceDoc(allRows, final),
    applyOverride: (row, refValue) => ({
      ...row,
      "Archiving Object": refValue,
      Comments: `Updated from ${row["Archiving Object"]} per reference document`,
      "Ref Doc Object": "",
    }),
  };

  return <ReferenceReviewPanel config={config} />;
}
