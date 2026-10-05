export interface SapSystem {
  id: string;
  description: string;
  name: string;
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
