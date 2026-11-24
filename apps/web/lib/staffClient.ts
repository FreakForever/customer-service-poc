"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export class ApiError extends Error { constructor(public status: number, message: string) { super(message); } }

/** Browser → /api/staff proxy. FormData is sent as is, anything else as JSON. */
export async function staffApi<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const form = body instanceof FormData;
  const r = await fetch(`/api/staff/${path}`, { method, body: body === undefined ? undefined : form ? body : JSON.stringify(body),
    headers: { ...(method === "GET" ? {} : { "x-mcs-staff": "1" }), ...(body !== undefined && !form ? { "content-type": "application/json" } : {}) } });
  if (!r.ok) {
    const d = await r.json().catch(() => null);
    throw new ApiError(r.status, typeof d?.detail === "string" ? d.detail : r.status === 401 ? "Please sign in again" : "Something went wrong");
  }
  return (r.status === 204 ? undefined : await r.json()) as T;
}

/** Load now and every `ms`; keeps the last good data and reports the last error. Late answers for an old path are dropped. */
export function usePoll<T>(path: string | null, ms: number) {
  const [data, setData] = useState<{ path: string; value: T } | null>(null);
  const [error, setError] = useState("");
  const run = useRef(0);
  const reload = useCallback(async () => {
    if (!path) return;
    const n = ++run.current;
    try {
      const value = await staffApi<T>(path);
      if (n === run.current) { setData({ path, value }); setError(""); }
    } catch (e) { if (n === run.current) setError((e as Error).message); }
  }, [path]);
  useEffect(() => {
    const counter = run;
    void reload();
    const t = setInterval(() => { if (!document.hidden) void reload(); }, ms);
    return () => { clearInterval(t); counter.current++; };
  }, [reload, ms]);
  return { data: data && data.path === path ? data.value : null, error, reload };
}
