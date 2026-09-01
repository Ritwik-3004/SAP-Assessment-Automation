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
};
