export interface SapSystem {
  id: string;
  description: string;
  name: string;
}

export interface TableAnalysisField {
  name: string;
  label: string;
  /** date | period | year | month (date/year/month question) */
  kind?: string;
  /** commonly useful (company code, document type, ...) */
  suggested?: boolean;
}

/** The question the table-analysis job is waiting for the user to answer. */
export interface TableAnalysisPrompt {
  id: number;
  kind: "date_fields" | "more_fields" | "other_fields" | "redo_fields";
  /** a remark to show above the question (e.g. why the fields sheet could not be used) */
  note?: string;
  /** redo_fields: the fields already analysed, which are kept */
  previous?: string[];
  table: string;
  fields?: TableAnalysisField[];
  can_group_by_year?: boolean;
  no_date_fields?: boolean;
  selected?: string[];
  has_other_fields?: boolean;
}

export interface TableAnalysisRecord {
  table: string;
  state: "running" | "done" | "skipped" | "failed";
  fields: string[];
  /** who chose the fields: the Fields for TAANA sheet, the user, or a re-run with additional fields */
  source?: "sheet" | "you" | "re-run";
  rows: number;
  note: string;
}

export interface TableAnalysisSnapshot {
  status: "idle" | "running" | "waiting" | "done" | "error";
  /** "redo" while one table is being re-run with additional fields */
  mode?: "run" | "redo";
  /** the Fields for TAANA sheet: how many tables it lists, or why it could not be used */
  fields_sheet?: { tables: number; problem: string };
  total: number;
  /** tables whose questions are answered (or skipped) */
  asked: number;
  /** tables finished: analysed, skipped or failed */
  completed: number;
  message: string | null;
  current: { table: string; step: string } | null;
  prompt: TableAnalysisPrompt | null;
  /** true once the last table's questions are answered; only waiting for SAP jobs after that */
  interactive_done: boolean;
  running: string[];
  records: TableAnalysisRecord[];
  output_path: string | null;
  warnings: string[];
}

export interface ObjectAnalysisRecord {
  object: string;
  description: string;
  state: "done" | "failed";
  conditions: number;
  by_source: Record<string, number>;
  archive_first: string[];
  note: string;
}

export interface ObjectAnalysisSnapshot {
  status: "idle" | "running" | "done" | "error";
  total: number;
  completed: number;
  message: string | null;
  current: { object: string; step: string } | null;
  records: ObjectAnalysisRecord[];
  output_path: string | null;
  warnings: string[];
  /** the optional second step, which runs in the background */
  sap_for_me: {
    status: "idle" | "running" | "done" | "error";
    total: number;
    completed: number;
    message: string | null;
    current: string | null;
  };
  reference: { status: "idle" | "running" | "done" | "error"; message: string | null; filename: string; warnings: string[] };
}

export interface ObjectAnalysisDetail {
  object: string;
  description: string;
  error: string;
  sara_url: string;
  network_known: boolean;
  sources: Record<string, string>;
  conditions: { condition: string; detail: string; source: string; ref?: string }[];
  prerequisites: {
    object: string;
    step: number | null;
    direct: boolean;
    required_by: string[];
    description?: string;
    from_reference?: boolean;
    ref?: string;
  }[];
}

export interface ObjectReferenceReview {
  files: string[];
  warnings: string[];
  summary: { confirmed: number; gaps: number; mismatches: number; not_in_document: string[]; files: string[] };
  objects: {
    object: string;
    in_document: boolean;
    dependencies: {
      stated: boolean;
      confirmed: string[];
      only_tool: string[];
      only_document: { id: string; object: string; file: string; applied?: boolean }[];
    };
    conditions: {
      covered: { document: string; tool: string; file: string }[];
      new: { id: string; text: string; file: string; applied?: boolean }[];
      conflicts: { id: string; document: string; tool: string; note: string; file: string; applied?: boolean }[];
    };
  }[];
}

export interface SavedSapCredentials {
  system: string;
  client: string;
  username: string;
  password: string;
  language: string;
}

export interface ConnectionState {
  connected: boolean;
  system?: string;
  user?: string;
}

export type TransactionId = "TAANA" | "DB15" | "SE16N" | "SE11" | "AOBJ" | "SARA";

export interface TransactionResult {
  status: "ok" | "error";
  transaction: string;
  message?: string;
  rows?: Record<string, string>[];
  fields?: Record<string, string>[];
  sessions?: Record<string, string>[];
  table_name?: string;
  archiving_object?: string;
  filter?: string;
}

export interface Db15BatchResult {
  status: "ok" | "error";
  transaction: string;
  message?: string;
  rows?: Record<string, string>[];
  errors?: { table_name: string; message: string }[];
}

export interface Db02TopTablesResult {
  status: "ok" | "error";
  transaction: string;
  message?: string;
  rows?: Record<string, string>[];
}

export interface HeaderTableBatchResult {
  status: "ok" | "error";
  message?: string;
  rows?: Record<string, string>[];
  errors?: { archiving_object: string; message: string }[];
}

export interface ScoredResult {
  status: "ok" | "error";
  message?: string;
  rows?: Record<string, string>[];
  recommended?: Record<string, string>[];
}

export interface JobStarted {
  status: "started";
  total: number;
}

export interface ProgressSnapshot<T> {
  status: "idle" | "running" | "done" | "error";
  completed: number;
  total: number;
  message?: string | null;
  result: T | null;
}

export interface ReferenceDocResult {
  status: "ok" | "error";
  message?: string;
  filename: string;
  /** Documents that were skipped (unreadable / no text) while others were used. */
  warnings?: string[];
  ref_mappings: Record<string, string>;
  annotated_recommended: Record<string, string>[];
  matches: Record<string, string>[];
  mismatches: Record<string, string>[];
  not_in_ref: Record<string, string>[];
  /** Description for every object the document proposes (real, "(AI-suggested) …" or "(description not found)"). */
  object_descriptions?: Record<string, string>;
}

export interface LlmLimits {
  rpm: number;
  tpm: number;
  rpd: number;
  tpd: number;
}

export interface LlmSettings {
  provider: "anthropic" | "groq";
  model: string;
  label: string;
  claude_model: string;
  claude_key_configured: boolean;
  groq_key_configured: boolean;
  groq_models: { id: string; label: string }[];
  groq_limits: LlmLimits;
}

export interface LlmUsage {
  provider: "anthropic" | "groq";
  model: string;
  date: string;
  requests: number;
  tokens: number;
  limits: LlmLimits | null;
}

export interface LlmTestResult {
  ok: boolean;
  label: string;
  message?: string;
  reply?: string;
  seconds?: number;
}

/** What the generic reference-document review panel works with (see ReferenceReviewPanel). */
export interface ReferenceReview {
  /** The documents that were read, comma-separated. */
  filename: string;
  warnings?: string[];
  annotated: Record<string, string>[];
  matches: Record<string, string>[];
  mismatches: Record<string, string>[];
  not_in_ref: Record<string, string>[];
  /** Archiving object → description, for the objects the document proposes (table → object review only). */
  objectDescriptions?: Record<string, string>;
}

export interface HeaderReferenceResult {
  status: "ok" | "error";
  message?: string;
  filename: string;
  warnings?: string[];
  ref_mappings: Record<string, string>;
  annotated_rows: Record<string, string>[];
  matches: Record<string, string>[];
  mismatches: Record<string, string>[];
  not_in_ref: Record<string, string>[];
}
