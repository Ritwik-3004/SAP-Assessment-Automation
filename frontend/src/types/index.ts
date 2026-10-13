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
  /** who chose the fields: the fields sheet, the user, or a re-run with additional fields */
  source?: "sheet" | "you" | "re-run";
  rows: number;
  note: string;
}

export interface TableAnalysisSnapshot {
  status: "idle" | "running" | "waiting" | "done" | "error";
  /** "redo" while one table is being re-run with additional fields */
  mode?: "run" | "redo";
  /** the fields sheet: how many tables it lists, or why it could not be used */
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

export interface OpenItemRecord {
  object: string;
  description: string;
  /** the sheet's heading for the object (Sales Documents, FINANCE, ...) */
  area: string;
  state: "queued" | "running" | "done" | "skipped" | "failed";
  rows: number;
  note: string;
  /** the query that ran (or is running / waiting) */
  query: string;
  /** the sheet's query as the tool runs it; empty if the sheet has none for this object */
  default_query: string;
  edited: boolean;
  /** "sheet": the standard query; "sheet_client": the sheet's own query with its own client (reverted); "edited": the user's */
  query_kind: "sheet" | "sheet_client" | "edited";
  /** the client the sheet's query names (e.g. "300") when the tool replaced it with the logged-on client, else "" */
  sheet_client: string;
  /** the client the tool used instead (the one the user is logged on to), else "" */
  client: string;
  /** the sheet's query exactly as it is in the sheet (with the sheet's client), to revert to; "" if not replaced */
  sheet_query: string;
  /** an edited query is waiting to run next */
  queued_edit: boolean;
  seconds: number;
}

export interface OpenItemSnapshot {
  status: "idle" | "running" | "done";
  total: number;
  completed: number;
  message: string | null;
  /** the query being run now; `started` is epoch seconds */
  current: { object: string; query: string; started: number } | null;
  /** set when the sheet's client was replaced with the logged-on client */
  client_note: { sheet_client: string; client: string; objects: string[] } | null;
  records: OpenItemRecord[];
  output_path: string | null;
  warnings: string[];
  sheet: { objects: string[]; problem: string };
}

export interface OpenItemResult {
  columns: string[];
  rows: string[][];
  total_rows: number;
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

/** The sidebar tasks; each saves into its own folder under "results". */
export type TaskId =
  | "TOP_TABLES"
  | "BATCH"
  | "HEADER_TABLES"
  | "TABLE_ANALYSIS"
  | "OBJECT_ANALYSIS"
  | "OPEN_ITEM_ANALYSIS"
  | "FINAL_REPORT";

export interface FinalReportSource {
  label: string;
  file: string;
  folder: string;
  status: "found" | "fallback" | "missing";
  note: string;
}

export interface FinalReportResult {
  files: { name: string; path: string }[];
  objects: {
    object: string;
    description: string;
    header_table: string;
    tables: number;
    conditions: number;
    table_analysis: boolean;
    open_items: boolean;
  }[];
  warnings: string[];
  /** the Housekeeping Programs sheet of the overview */
  housekeeping: { programs: number; tables: number };
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
  /** full path of the file the results were saved to automatically, or why saving failed */
  saved_path?: string;
  save_error?: string;
}

export interface HeaderTableBatchResult {
  status: "ok" | "error";
  message?: string;
  rows?: Record<string, string>[];
  errors?: { archiving_object: string; message: string }[];
  saved_path?: string;
  save_error?: string;
}

export interface ScoredResult {
  status: "ok" | "error";
  message?: string;
  rows?: Record<string, string>[];
  recommended?: Record<string, string>[];
  /** Tables the run could not score (a limit stopped it); the others are scored and kept. */
  unscored?: { table: string; reason: string }[];
  saved_path?: string;
  save_error?: string;
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

export interface OllamaModel {
  id: string;
  size_gb: number;
  params: string;
  /** can call tools (the chat assistant needs this) */
  tools: boolean;
  /** fits in this computer's memory */
  fits: boolean;
  /** too few parameters to give reliable answers */
  too_small: boolean;
  /** what "Auto" would use */
  best: boolean;
}

/** Models installed in Ollama on this computer (nothing here ever leaves it). */
export interface OllamaInfo {
  url: string;
  reachable: boolean;
  error: string;
  /** advice to show: Ollama not running, no model installed, or the best one is too small */
  notice: string;
  models: OllamaModel[];
  auto_model: string;
  /** cloud models Ollama lists that were left out because they don't run locally */
  hidden_cloud: number;
  ram_gb: number | null;
  /** models worth pulling, best first */
  suggested: string[];
}

export interface LlmSettings {
  provider: "anthropic" | "groq" | "ollama";
  /** Ollama: the chosen model, "" for Auto */
  ollama_model: string;
  ollama: OllamaInfo;
  model: string;
  label: string;
  claude_model: string;
  claude_key_configured: boolean;
  groq_key_configured: boolean;
  groq_models: { id: string; label: string }[];
  groq_limits: LlmLimits;
}

export interface LlmUsage {
  provider: "anthropic" | "groq" | "ollama";
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
