import { api } from "../api/client";
import ReferenceReviewPanel, { type ReferenceReviewConfig } from "./ReferenceReviewPanel";

interface Props {
  /** The header tables the app found (Archiving Object, Header Table, Source, Confidence, Comments). */
  rows: Record<string, string>[];
}

/** Reference-document review for Find Header Tables (archiving object → header table):
 *  the same upload / compare / override / preview / save / download flow as Find Archiving Objects. */
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
      "Upload a reference document (for example one written by SMEs from past projects) that lists " +
      "the header table of each archiving object. The tool will compare it against the header tables " +
      "found above and highlight any differences.",
    previewTitle: "Preview — Final Header Tables",
    savedPath: "output/header_tables_with_reference.xlsx",
    downloadName: "header_tables_with_reference.xlsx",
    analyze: async (file) => {
      const res = await api.analyzeHeaderReference(file, rows);
      return {
        filename: res.filename,
        annotated: res.annotated_rows,
        matches: res.matches,
        mismatches: res.mismatches,
        not_in_ref: res.not_in_ref,
      };
    },
    save: (final) => api.saveHeaderReference(rows, final),
    exportFile: (final) => api.exportHeaderReference(rows, final),
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
