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
  ref_mappings: Record<string, string>;
  annotated_recommended: Record<string, string>[];
  matches: Record<string, string>[];
  mismatches: Record<string, string>[];
  not_in_ref: Record<string, string>[];
}
