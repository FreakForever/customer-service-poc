import { AGENT_API_URL } from "./api";

export type Doc = { doc_id: string; version: string; title: string; doc_type: string; family: string | null; status: string; effective_from: string };
export type Section = { heading_path: string[]; anchor: string; content: string };
export type Article = { document: Doc; sections: Section[]; related: Doc[]; superseded_by: { doc_id: string; title: string } | null;
  versions: { version: string; status: string; effective_from: string }[] };
export type Hit = { kind: "error_code" | "article" | "faq"; title: string; snippet: string; url: string | null };
export type Home = { popular: Doc[]; bulletins: Doc[]; recent: Doc[] };
export type FamilyInfo = {
  models: { id: string; name: string }[]; documents: Doc[]; parts: { part_no: string; name: string; fits_models: string[] }[];
  error_codes: { id: string; code: string; title: string; display_text: string; severity: string; safety_stop: boolean; doc_id: string | null; anchor: string | null }[];
};

/** null = not found; throws when the API can't be reached. */
export async function kb<T>(path: string): Promise<T | null> {
  const r = await fetch(`${AGENT_API_URL}/v1/kb${path}`, { cache: "no-store", signal: AbortSignal.timeout(10000) });
  if (r.status === 404 || r.status === 422) return null;
  if (!r.ok) throw new Error(`help centre API: ${r.status}`);
  return (await r.json()) as T;
}
