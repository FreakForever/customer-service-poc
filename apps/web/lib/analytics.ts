export type Box = { n: number; p5: number | null; p25: number | null; p50: number | null; p75: number | null; p95: number | null };
export type Day = { day: string; turns: number; conversations: number; t0: number; t1: number; t2: number; t3: number; t4: number; other: number; groundedness: number | null; cost: number | null; up: number; down: number };
export type Summary = {
  from: string; to: string; generated_at: string;
  volume: { conversations: number; turns: number; by_channel: { channel: string; conversations: number; turns: number }[] };
  tier_mix: { tier: string; turns: number; share: number }[];
  latency_p50_p95: { overall: Box; by_tier: (Box & { tier: string })[] };
  deflection: { rate: number | null; conversations: number }; handoff_rate: { rate: number | null; conversations: number };
  groundedness: { score: number | null; judged: number; citation_coverage: number | null; checked: number };
  feedback: { up: number; down: number; positive: number | null };
  cost: { total: number | null; per_conversation: number | null; priced_conversations: number; unpriced_turns: number; tokens_in: number; tokens_out: number;
    by_model: { model: string; calls: number; tokens_in: number; tokens_out: number; cost: number | null; estimated: boolean }[];
    by_tier: { tier: string; turns: number; tokens_in: number; tokens_out: number; unpriced: number; total: number | null; per_turn: number | null }[];
    top_conversations: { id: string; channel: string; turns: number; tokens: number; cost: number; subject: string | null; available: boolean }[] };
  by_day: Day[];
};
export type Flagged = { trace_id: string; conversation_id: string | null; channel: string; tier: string; created_at: string; groundedness: number | null; reason: string | null; reasons: string[]; answer: string | null };
type Score = { n?: number; correctness?: number | null };
export type Benchmark = { runs: { id: string; mode: string; started_at: string | null }[];
  by_mode: { mode: string; started_at: string | null; n: number | null; correctness: number | null; groundedness: number | null; p50_ms: number | null; p95_ms: number | null; tokens: number | null; cost_usd: number | null }[];
  by_type: { type: string; modes: Record<string, Score> }[] };

/** Fixed order and colour per tier: a filter never repaints a tier. */
export const TIERS = [
  { id: "T0", key: "t0", label: "T0 rules and tools", bg: "bg-chart-1" }, { id: "T1", key: "t1", label: "T1 FAQ cache", bg: "bg-chart-2" },
  { id: "T2", key: "t2", label: "T2 documents", bg: "bg-chart-3" }, { id: "T3", key: "t3", label: "T3 agents and graph", bg: "bg-chart-4" },
  { id: "T4", key: "t4", label: "T4 person", bg: "bg-chart-5" }, { id: "none", key: "other", label: "No tier", bg: "bg-steel" },
] as const;
export const tierLabel = (id: string) => TIERS.find((t) => t.id === id)?.label ?? id;

export const num = (n: number) => n.toLocaleString("en-IN");
export const pct = (r: number | null) => (r === null ? "–" : `${Math.round(r * 1000) / 10}%`);
export const ms = (v: number | null) => (v === null ? "–" : v < 1000 ? `${Math.round(v)} ms` : `${(v / 1000).toFixed(v < 10_000 ? 1 : 0)} s`);
/** Model calls cost fractions of a cent, so small amounts keep two significant digits. */
export function usd(v: number | null | undefined) {
  if (v === null || v === undefined) return "–";
  if (v === 0) return "$0";
  return v >= 1 ? `$${v.toFixed(2)}` : `$${v.toFixed(Math.min(8, 1 - Math.floor(Math.log10(v))))}`;
}
export const score = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v.toFixed(2));
export const day = (d: string) => new Date(`${d}T00:00:00Z`).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });

/** Today in India, where the service desk is. */
export const today = (now = new Date()) => now.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
export function back(from: string, days: number) {
  const d = new Date(`${from}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() - days);
  return d.toISOString().slice(0, 10);
}
/** Up to four clean axis steps that cover `max`. */
export function ticks(max: number, whole = false): number[] {
  if (!(max > 0)) return [0, 1];
  const raw = Math.max(max / 4, whole ? 1 : 0), mag = 10 ** Math.floor(Math.log10(raw)), step = [1, 2, ...(whole ? [] : [2.5]), 5, 10].map((m) => m * mag).find((s) => s >= raw - 1e-12)!;
  return Array.from({ length: Math.ceil(max / step - 1e-9) + 1 }, (_, i) => i * step);
}
