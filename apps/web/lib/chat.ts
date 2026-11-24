export type Citation = { marker: string; title: string; url: string | null; fact?: string | null };
export type Quick = { type: string; label: string; payload: string };
export type FormField = { name: string; required?: boolean; pattern?: string; value?: string | null };
export type ChatForm = { form_id: string; fields: FormField[] };
export type Msg = {
  key: string; id?: string | null; role: "customer" | "assistant" | "agent"; text: string; from?: string;
  citations?: Citation[]; quick?: Quick[]; form?: ChatForm; tier?: string; rating?: 1 | -1; error?: boolean;
};

/** [E1] → [1] so the text matches the numbered chips; an unfinished marker at the end of a stream is hidden. */
export const display = (t: string) => t.replace(/\[E(\d+)\]/g, "[$1]").replace(/\[E?\d*$/, "");
export const chipNo = (c: Citation) => c.marker.replace(/\D/g, "");

/** Paths of the agent API the browser may reach through the proxy. */
const U = "[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}";
export function chatPath(method: string, parts: string[]): string | null {
  const p = parts.join("/");
  if (method === "POST" && p === "feedback") return "/v1/feedback";
  const ok = method === "POST" ? new RegExp(`^sessions(/${U}/(messages|forms/[a-z_]{1,40}))?$`) : new RegExp(`^sessions/${U}(/events)?$`);
  return ok.test(p) ? `/v1/chat/${p}` : null;
}
