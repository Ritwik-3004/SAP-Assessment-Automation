const BASE = "http://127.0.0.1:8000";

async function post<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? res.statusText);
  }
  return res.json();
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(err.detail ?? res.statusText);
  }
  return res.json();
}

/** Poll a /progress endpoint until it reaches "done" or "error", calling
 * onTick with every snapshot along the way. Used for long-running batch
 * operations (DB15 lookup, scoring) that run in a background thread. */
async function pollUntilDone<T>(
  getProgress: () => Promise<import("../types").ProgressSnapshot<T>>,
  onTick: (snapshot: import("../types").ProgressSnapshot<T>) => void,
  intervalMs = 700
): Promise<import("../types").ProgressSnapshot<T>> {
  for (;;) {
    const snapshot = await getProgress();
    onTick(snapshot);
    if (snapshot.status === "done" || snapshot.status === "error") {
      return snapshot;
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
}

export const api = {
  health: () => get<{ status: string; sap_connected: boolean }>("/api/health"),
  systems: () => get<{ systems: { id: string; description: string; name: string }[] }>("/api/sap/systems"),
  connect: (body: { system: string; client: string; username: string; password: string; language: string }) =>
    post<{ status: string; system: string; user: string }>("/api/sap/connect", body),
  disconnect: () => post<{ status: string }>("/api/sap/disconnect", {}),
  status: () => get<{ connected: boolean }>("/api/sap/status"),
  sapInfo: () => get<{ connected: boolean; system: string | null; user: string | null }>("/api/sap/info"),
  loadCredentials: () =>
    get<
      Partial<import("../types").SavedSapCredentials> & {
        profiles?: import("../types").SavedSapCredentials[];
      }
    >("/api/sap/credentials"),
  selectCredentials: (id: { system: string; client: string; username: string }) =>
    post<{ selected: boolean }>("/api/sap/credentials/select", id),
  deleteCredentials: (id: { system: string; client: string; username: string }) =>
    post<{ deleted: boolean }>("/api/sap/credentials/delete", id),
  saveCredentials: (body: { system: string; client: string; username: string; password: string; language: string }) =>
    post<{ saved: boolean }>("/api/sap/credentials", body),
  loadSapForMeCredentials: () =>
    get<{ email?: string; password?: string; profiles?: { email: string; password: string }[] }>(
      "/api/sap-for-me/credentials"
    ),
  selectSapForMeCredentials: (email: string) =>
    post<{ selected: boolean }>("/api/sap-for-me/credentials/select", { email }),
  deleteSapForMeCredentials: (email: string) =>
    post<{ deleted: boolean }>("/api/sap-for-me/credentials/delete", { email }),
  saveSapForMeCredentials: (body: { email: string; password: string }) =>
    post<{ saved: boolean }>("/api/sap-for-me/credentials", body),
  getLlmSettings: (refreshOllama = false) =>
    get<import("../types").LlmSettings>(`/api/llm/settings${refreshOllama ? "?refresh_ollama=true" : ""}`),
  saveLlmSettings: (body: { provider: string; model: string }) =>
    post<import("../types").LlmSettings>("/api/llm/settings", body),
  getLlmUsage: () => get<import("../types").LlmUsage>("/api/llm/usage"),
  testLlm: () => post<import("../types").LlmTestResult>("/api/llm/test", {}),

  taana: (body: { table_name?: string; max_rows: number }) =>
    post<import("../types").TransactionResult>("/api/transactions/taana", body),
  db15: (body: { table_name: string }) =>
    post<import("../types").TransactionResult>("/api/transactions/db15", body),
  se16n: (body: { table_name: string; max_rows: number; where_clause?: string }) =>
    post<import("../types").TransactionResult>("/api/transactions/se16n", body),
  se11: (body: { table_name: string }) =>
    post<import("../types").TransactionResult>("/api/transactions/se11", body),
  aobj: (body: { object_filter?: string }) =>
    post<import("../types").TransactionResult>("/api/transactions/aobj", body),
  sara: (body: { archiving_object: string }) =>
    post<import("../types").TransactionResult>("/api/transactions/sara", body),

  db15Batch: async (file: File) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${BASE}/api/transactions/db15/batch`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<import("../types").JobStarted>;
  },

  db15BatchProgress: () =>
    get<import("../types").ProgressSnapshot<import("../types").Db15BatchResult>>(
      "/api/transactions/db15/batch/progress"
    ),

  pollDb15Batch: (onTick: (snapshot: import("../types").ProgressSnapshot<import("../types").Db15BatchResult>) => void) =>
    pollUntilDone(() => api.db15BatchProgress(), onTick),

  db15Export: async (rows: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/transactions/db15/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  db02TopTables: (body: { limit: number }) =>
    post<import("../types").Db02TopTablesResult>("/api/transactions/db02/top-tables", body),

  db02Export: async (rows: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/transactions/db02/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  db15Score: (rows: Record<string, string>[]) =>
    post<import("../types").JobStarted>("/api/transactions/db15/score", { rows }),

  db15ScoreProgress: () =>
    get<import("../types").ProgressSnapshot<import("../types").ScoredResult>>(
      "/api/transactions/db15/score/progress"
    ),

  pollDb15Score: (onTick: (snapshot: import("../types").ProgressSnapshot<import("../types").ScoredResult>) => void) =>
    pollUntilDone(() => api.db15ScoreProgress(), onTick),

  db15ScoreExport: async (rows: Record<string, string>[], recommended: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/transactions/db15/score-export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, recommended }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  chat: (
    message: string,
    scoredRows: Record<string, string>[],
    recommended: Record<string, string>[],
    history: { role: string; content: string }[]
  ) =>
    post<{
      reply: string;
      updated_rows: Record<string, string>[] | null;
      updated_recommended: Record<string, string>[] | null;
    }>("/api/chat", {
      message,
      scored_rows: scoredRows,
      recommended,
      history,
    }),

  analyzeReferenceDoc: async (
    files: File[],
    recommended: Record<string, string>[],
    knownDescriptions: Record<string, string> = {},
  ) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    formData.append("recommended", JSON.stringify(recommended));
    formData.append("known_descriptions", JSON.stringify(knownDescriptions));
    const res = await fetch(`${BASE}/api/reference-doc/analyze`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<import("../types").ReferenceDocResult>;
  },

  saveReferenceDoc: (rows: Record<string, string>[], recommended: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/reference-doc/save", { rows, recommended }),

  exportReferenceDoc: async (rows: Record<string, string>[], recommended: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/reference-doc/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, recommended }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  analyzeHeaderReference: async (files: File[], rows: Record<string, string>[]) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    formData.append("rows", JSON.stringify(rows));
    const res = await fetch(`${BASE}/api/header-reference/analyze`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<import("../types").HeaderReferenceResult>;
  },

  saveHeaderReference: (rows: Record<string, string>[], final: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/header-reference/save", { rows, final }),

  exportHeaderReference: async (rows: Record<string, string>[], final: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/header-reference/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows, final }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  objectAnalysisStart: async (source: { filename?: string; file?: File }) => {
    const formData = new FormData();
    if (source.file) formData.append("file", source.file);
    if (source.filename) formData.append("filename", source.filename);
    const res = await fetch(`${BASE}/api/object-analysis/start`, { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<{ status: "started"; total: number; objects: string[] }>;
  },
  objectAnalysisProgress: () =>
    get<import("../types").ObjectAnalysisSnapshot>("/api/object-analysis/progress"),
  objectAnalysisResults: () =>
    get<{ objects: import("../types").ObjectAnalysisDetail[] }>("/api/object-analysis/results"),
  objectReferenceAnalyze: async (files: File[]) => {
    const formData = new FormData();
    files.forEach((f) => formData.append("files", f));
    const res = await fetch(`${BASE}/api/object-analysis/reference/analyze`, { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<{ status: string }>;
  },
  objectReferenceReview: () =>
    get<{
      state: { status: "idle" | "running" | "done" | "error"; message: string | null; filename: string; warnings: string[] };
      review: import("../types").ObjectReferenceReview | null;
    }>("/api/object-analysis/reference/review"),
  objectReferenceApply: (ids: string[]) => post<{ applied: number }>("/api/object-analysis/reference/apply", { ids }),
  objectAnalysisSapForMe: () => post<{ status: string }>("/api/object-analysis/sap-for-me", {}),
  objectAnalysisSapForMeCancel: () => post<{ ok: boolean }>("/api/object-analysis/sap-for-me/cancel", {}),
  objectAnalysisCancel: () => post<{ ok: boolean }>("/api/object-analysis/cancel", {}),
  objectAnalysisDownload: async () => {
    const res = await fetch(`${BASE}/api/object-analysis/download`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },
  tableAnalysisStart: async (source: { filename?: string; file?: File }, groupByYear = true) => {
    const formData = new FormData();
    if (source.file) formData.append("file", source.file);
    if (source.filename) formData.append("filename", source.filename);
    formData.append("group_by_year", groupByYear ? "true" : "false");
    const res = await fetch(`${BASE}/api/table-analysis/start`, { method: "POST", body: formData });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<{ status: "started"; total: number; tables: string[] }>;
  },
  tableAnalysisProgress: () =>
    get<import("../types").TableAnalysisSnapshot>("/api/table-analysis/progress"),
  tableAnalysisAnswer: (promptId: number, answer: Record<string, unknown>) =>
    post<{ ok: boolean }>("/api/table-analysis/answer", { prompt_id: promptId, answer }),
  tableAnalysisRedo: (table: string) => post<{ status: string }>("/api/table-analysis/redo", { table }),
  tableAnalysisSkip: () => post<{ ok: boolean }>("/api/table-analysis/skip", {}),
  tableAnalysisStopWaiting: (table: string) => post<{ ok: boolean }>("/api/table-analysis/stop-waiting", { table }),
  tableAnalysisCancel: () => post<{ ok: boolean }>("/api/table-analysis/cancel", {}),
  tableAnalysisDownload: async () => {
    const res = await fetch(`${BASE}/api/table-analysis/download`);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  getInputFiles: () => get<{ files: string[] }>("/api/files/input"),

  db15BatchFromInput: (filename: string) =>
    post<import("../types").JobStarted>("/api/transactions/db15/batch-from-input", { filename }),

  saveToInput: (rows: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/files/input/save", { rows }),

  saveArchivingToOutput: (rows: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/files/output/save-archiving", { rows }),

  saveScoredToOutput: (rows: Record<string, string>[], recommended: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/files/output/save-scored", { rows, recommended }),

  groupByObject: (recommended: Record<string, string>[]) =>
    post<{ status: "ok"; rows: Record<string, string>[] }>("/api/transactions/db15/group-by-object", { recommended }),

  getOutputFiles: () => get<{ files: string[] }>("/api/files/output"),

  headerTablesBatch: async (file: File, maxObjects: number) => {
    const formData = new FormData();
    formData.append("file", file);
    const res = await fetch(`${BASE}/api/transactions/header-tables/batch?max_objects=${maxObjects}`, {
      method: "POST",
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json() as Promise<import("../types").JobStarted>;
  },

  headerTablesBatchFromOutput: (filename: string, maxObjects: number) =>
    post<import("../types").JobStarted>("/api/transactions/header-tables/batch-from-output", {
      filename,
      max_objects: maxObjects,
    }),

  headerTablesBatchProgress: () =>
    get<import("../types").ProgressSnapshot<import("../types").HeaderTableBatchResult>>(
      "/api/transactions/header-tables/batch/progress"
    ),

  pollHeaderTablesBatch: (
    onTick: (snapshot: import("../types").ProgressSnapshot<import("../types").HeaderTableBatchResult>) => void
  ) => pollUntilDone(() => api.headerTablesBatchProgress(), onTick),

  headerTablesExport: async (rows: Record<string, string>[]) => {
    const res = await fetch(`${BASE}/api/transactions/header-tables/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ rows }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.blob();
  },

  saveHeaderTablesToOutput: (rows: Record<string, string>[]) =>
    post<{ saved: boolean; path: string }>("/api/files/output/save-header-tables", { rows }),
};
