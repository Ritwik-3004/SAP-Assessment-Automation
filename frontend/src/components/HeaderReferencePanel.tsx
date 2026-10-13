import { api } from "../api/client";
import type { HeaderReferenceResult, ReferenceReview } from "../types";
import ReferenceReviewPanel, { type ReferenceReviewConfig } from "./ReferenceReviewPanel";

interface Props {
  /** The header tables the app found (Archiving Object, Header Table, Source, Confidence, Comments). */
  rows: Record<string, string>[];
}

/** Reference-document review for Find Header Tables (archiving object → header table):
 *  the same upload / compare / override / preview flow (the result is saved automatically) as Find Archiving Objects. */
/** The analysis result in the shape the review screen works with. */
function toReview(res: HeaderReferenceResult): ReferenceReview {
  return {
    filename: res.filename,
    warnings: res.warnings,
    annotated: res.annotated_rows,
    matches: res.matches,
    mismatches: res.mismatches,
    not_in_ref: res.not_in_ref,
  };
}

export default function HeaderReferencePanel({ rows }: Props) {
  const config: ReferenceReviewConfig = {
    rows,
    idKey: "Archiving Object",
    idLabel: "Archiving Object",
    valueKey: "Header Table",
    valueLabel: "Header Table",
    currentLabel: "Current Header Table",
    refLabel: "Reference Doc Header Table",
    refKey: "Ref Doc Header Table",
    commentKey: "Reference Check",
    commentLabel: "Reference Check",
    extraKey: "Confidence",
    extraLabel: "Confidence",
    uploadHint:
      "Upload one or more reference documents (for example ones written by SMEs from past projects) that list " +
      "the header table of each archiving object. The tool will compare it against the header tables " +
      "found above and highlight any differences.",
    previewTitle: "Preview — Final Header Tables",
    stateKey: "ref-doc-headers",
    start: async (files) => {
      await api.startHeaderReference(files, rows);
    },
    progress: async () => {
      const s = await api.headerReferenceProgress();
      return { ...s, result: s.result ? toReview(s.result) : null };
    },
    save: (final) => api.saveHeaderReference(rows, final),
    applyOverride: (row, refValue) => ({
      ...row,
      "Header Table": refValue,
      Source: "Reference document",
      Confidence: "High",
      Comments: "Header table taken from the reference document.",
      "Reference Check": `Updated from ${row["Header Table"] || "(blank)"} per reference document`,
      "Ref Doc Header Table": "",
    }),
  };

  return <ReferenceReviewPanel config={config} />;
}
