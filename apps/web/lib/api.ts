import type { Staff } from "./auth";
import { staffToken } from "./staffToken";

/** Server-side base URL of the agent API (Cloud Run URL in dev/demo, localhost when running `make dev`). */
export const AGENT_API_URL = process.env.AGENT_API_URL ?? "http://localhost:8000";

/** Call a staff route of the agent API as the signed-in person. Server side only. */
export async function staffFetch(staff: Staff, path: string, init: RequestInit = {}, timeoutMs = 15_000): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.set("Authorization", `Bearer ${await staffToken(staff)}`);
  return fetch(`${AGENT_API_URL}${path}`, { ...init, headers, cache: "no-store", signal: AbortSignal.timeout(timeoutMs) });
}
