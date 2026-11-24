import { staffFetch } from "@/lib/api";
import { getStaff } from "@/lib/auth";
import { staffPath } from "@/lib/staffPath";

type Ctx = { params: Promise<{ path: string[] }> };

/** Adds the signed-in person's short-lived token. Changes need a custom header, which other sites can't send. */
async function proxy(req: Request, ctx: Ctx) {
  const staff = await getStaff();
  if (!staff) return Response.json({ detail: "Sign in required" }, { status: 401 });
  const path = staffPath((await ctx.params).path);
  if (!path) return Response.json({ detail: "Not found" }, { status: 404 });
  const write = req.method !== "GET";
  if (write && req.headers.get("x-mcs-staff") !== "1") return Response.json({ detail: "Bad request" }, { status: 400 });
  const type = req.headers.get("content-type");
  try {
    const r = await staffFetch(staff, path + new URL(req.url).search, {
      method: req.method, body: write ? await req.arrayBuffer() : undefined, headers: type ? { "content-type": type } : {} }, 60_000);
    return new Response(r.status === 204 ? null : await r.arrayBuffer(), { status: r.status, headers: { "content-type": r.headers.get("content-type") ?? "application/json", "cache-control": "no-store" } });
  } catch {
    return Response.json({ detail: "The agent API can't be reached" }, { status: 502 });
  }
}
export { proxy as GET, proxy as POST, proxy as PATCH, proxy as PUT, proxy as DELETE };
