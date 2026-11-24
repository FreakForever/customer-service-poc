export type View = "review" | "live" | "p1" | "mine" | "all";
export const VIEWS: { id: View; label: string }[] = [
  { id: "review", label: "Needs review" }, { id: "live", label: "Live handoffs" }, { id: "p1", label: "P1" }, { id: "mine", label: "Mine" }, { id: "all", label: "All" },
];
export type Item = { id: string; channel: "chat" | "email"; subject: string | null; status: string; priority: string | null; assigned_to: string | null; updated_at: string;
  company: string | null; from_addr: string | null; last_message: string | null; last_role: string | null };
export type Listing = { items: Item[]; next: number | null; counts: Record<View | "tickets", number> };
export type Action = { type: string; name?: string; edited?: boolean };
export type Message = { id: string; role: "customer" | "assistant" | "agent" | "system"; content: string; citations: { marker: string; title?: string; url?: string | null; doc_id?: string | null }[] | null;
  actions: Action[] | null; trace_id: string | null; delivery: string; created_at: string; rating: number | null; attachments: { filename: string; mime: string; bytes: number }[] };
export type Ticket = { id: string; status: string; priority: string; model: string | null; error_code: string | null; summary: string | null; assigned_engineer: string | null; scheduled_visit: string | null };
export type Detail = {
  conversation: { id: string; channel: "chat" | "email"; subject: string | null; status: string; priority: string | null; assigned_to: string | null; from_addr: string | null; agent_name: string | null; updated_at: string };
  messages: Message[]; summary: string | null;
  customer: { id: string; company: string; industry: string; city: string; service_centre: string; contacts: { name: string; email: string; phone: string }[] } | null;
  assets: { serial_no: string; model: string; site_city: string; warranty_end: string; in_warranty: boolean; in_amc: boolean }[];
  contracts: { id: string; plan: string; start_date: string; end_date: string; active: boolean }[]; tickets: (Ticket & { here: boolean | null })[];
};
export type Trace = {
  trace: { id: string; answered_tier: string | null; intents: string[]; confidence: number | null; groundedness: number | null; latency_ms: number; tokens_in: number; tokens_out: number;
    cost_usd: string | null; path: string[] | null; flags: string[] | null; handoff_reason: string | null; suggested: string | null };
  spans: { node: string; started_at: string; duration_ms: number; model: string | null; tokens_in: number; tokens_out: number; cache_hit: boolean | null; attrs: Record<string, unknown> }[];
  evidence: { citation: string; kind: string; title: string | null; doc_id: string | null; score: number | null; text: string }[];
  verification: { passed: boolean; coverage: number | null; judge: number | null; issues: string[] } | null;
};
export const TICKET_STATUSES = ["new", "assigned", "scheduled", "in_progress", "awaiting_parts", "resolved", "closed"];
export const nice = (s: string) => s.replace(/_/g, " ");
export const when = (iso: string) => new Date(iso).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });

/** The text an agent starts from: the newest unsent draft, else a draft held back by verification. */
export function suggestion(d: Detail, t: Trace | null): { text: string; draftId: string | null } {
  const draft = [...d.messages].reverse().find((m) => m.delivery === "draft");
  return draft ? { text: draft.content, draftId: draft.id } : { text: (t?.trace.suggested ?? "").replace(/\s*\[E\d+\]/g, ""), draftId: null };
}
/** Question to promote: the customer's message just before the answer. */
export function askedBefore(d: Detail, id: string): string {
  const i = d.messages.findIndex((m) => m.id === id);
  return [...d.messages.slice(0, i)].reverse().find((m) => m.role === "customer")?.content.slice(0, 300) ?? "";
}
export const sourceOf = (m: Message) => m.citations?.map((c) => c.doc_id ?? c.url?.match(/^\/a\/([^#]+)/)?.[1]).find(Boolean) ?? "";
