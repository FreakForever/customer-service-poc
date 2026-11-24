import { kb } from "@/lib/kb";

export async function GET(req: Request) {
  const q = (new URL(req.url).searchParams.get("q") ?? "").slice(0, 200);
  try {
    return Response.json((q.trim() && (await kb(`/search?limit=6&q=${encodeURIComponent(q)}`))) || { items: [] });
  } catch {
    return Response.json({ items: [] });
  }
}
