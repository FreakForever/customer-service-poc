import { AGENT_API_URL } from "@/lib/api";
import { chatPath } from "@/lib/chat";

type Ctx = { params: Promise<{ path: string[] }> };

/** Same-origin pass-through to the chat API (streams included). Only chat paths; the session token is the caller's. */
async function proxy(req: Request, ctx: Ctx) {
  const path = chatPath(req.method, (await ctx.params).path);
  if (!path) return Response.json({ title: "Not found" }, { status: 404 });
  const headers = new Headers();
  for (const h of ["authorization", "content-type"]) { const v = req.headers.get(h); if (v) headers.set(h, v); }
  try {
    const r = await fetch(`${AGENT_API_URL}${path}`, { method: req.method, headers, body: req.method === "POST" ? await req.text() : undefined, cache: "no-store", signal: req.signal });
    return new Response(r.status === 204 ? null : r.body, { status: r.status, headers: {
      "content-type": r.headers.get("content-type") ?? "application/json", "cache-control": "no-cache, no-transform", "x-accel-buffering": "no" } });
  } catch {
    return Response.json({ title: "Chat is unavailable" }, { status: 502 });
  }
}
export { proxy as GET, proxy as POST };
