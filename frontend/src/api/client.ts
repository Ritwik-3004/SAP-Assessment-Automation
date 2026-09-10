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
};
