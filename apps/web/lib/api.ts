/** Server-side base URL of the agent API (Cloud Run URL in dev/demo, localhost when running `make dev`). */
export const AGENT_API_URL = process.env.AGENT_API_URL ?? "http://localhost:8000";

export type Health = { status: string; env?: string; db?: string; neo4j?: string };

export async function getHealth(): Promise<Health> {
  try {
    const res = await fetch(`${AGENT_API_URL}/health`, { cache: "no-store", signal: AbortSignal.timeout(8000) });
    return (await res.json()) as Health;
  } catch (err) {
    return { status: `unreachable (${(err as Error).message})` };
  }
}
