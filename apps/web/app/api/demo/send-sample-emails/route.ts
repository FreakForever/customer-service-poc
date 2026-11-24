import { staffFetch } from "@/lib/api";
import { getStaff } from "@/lib/auth";

export async function POST(req: Request) {
  const staff = await getStaff();
  if (staff?.role !== "admin") return Response.json({ title: "Forbidden", status: 403 }, { status: 403 });
  // a custom header can't be sent cross-site without a CORS preflight
  if (req.headers.get("x-mcs-demo") !== "1") return Response.json({ title: "Bad request", status: 400 }, { status: 400 });
  try {
    const r = await staffFetch(staff, "/v1/demo/send-sample-emails", { method: "POST" });
    return Response.json(r.ok ? await r.json() : { title: "Agent API error" }, { status: r.ok ? 200 : 502 });
  } catch {
    return Response.json({ title: "Agent API unreachable", status: 502 }, { status: 502 });
  }
}
