import { AGENT_API_URL } from "@/lib/api";
import { toInbound, validate, type Contact } from "@/lib/contact";

export async function POST(req: Request) {
  const origin = req.headers.get("origin");
  if (origin && new URL(origin).host !== req.headers.get("host")) return Response.json({ title: "Forbidden" }, { status: 403 });
  const raw = (await req.json().catch(() => null)) as Record<string, unknown> | null;
  const s = (k: string) => (typeof raw?.[k] === "string" ? (raw[k] as string) : "");
  const c: Contact = { name: s("name"), company: s("company"), email: s("email"), serial: s("serial"), subject: s("subject"), message: s("message") };
  const errors = validate(c);
  if (Object.keys(errors).length) return Response.json({ errors }, { status: 422 });
  try {
    const r = await fetch(`${AGENT_API_URL}/v1/email/inbound`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(toInbound(c)), signal: AbortSignal.timeout(15000) });
    return Response.json({}, { status: r.ok ? 202 : r.status === 429 ? 429 : 502 });
  } catch {
    return Response.json({}, { status: 502 });
  }
}
