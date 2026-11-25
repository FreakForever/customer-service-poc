export type Doc = { doc_id: string; version: string; title: string; doc_type: string; family: string | null; status: string; effective_from: string; effective_to: string | null; source: string; chunks: number };
export type Job = { id: string; kind: string; status: "queued" | "running" | "done" | "failed"; attempts: number; error: string | null; result: Record<string, string | number | boolean>; doc_id: string | null; version: string | null; created_at: string; started_at: string | null; finished_at: string | null };
export type Faq = { id: string; category: string; question: string; answer: string; source_doc: string | null; status: string; origin: string; variants: number; hits: number };
export type Change = { id: string; doc_id: string | null; op: string; payload: Record<string, unknown>; confidence: number | null; status: string; decided_by: string | null; anchor: string | null };
export type KgNode = { id: string; label: string; name: string; source: string | null; dist?: number; props?: Record<string, unknown> };
export type KgEdge = { src: string; type: string; dst: string; source: string | null };
export type Hood = { nodes: KgNode[]; edges: KgEdge[]; truncated: boolean };
export type CsvResult = { job_id: string; applied: number; rejected: { line: number; error: string }[]; warnings: { line: number; message: string }[] };
export const CSV_TYPES = [
  { id: "error_codes", label: "Error codes (CSV)", columns: "family, code, title, display_text, severity, safety_stop, cause_ids, likelihoods, notes" },
  { id: "spare_parts", label: "Spare parts (CSV)", columns: "part_no, family, name, price_inr, consumable, fits_models, stock_WH-NOI, stock_WH-PUN, stock_WH-CHE, stock_WH-AMD, lead_time_days, replaces (optional)" },
  { id: "faqs", label: "FAQs (CSV)", columns: "id, category, question, paraphrases, answer, source_doc (an active document or UPLOAD-FAQ)" },
];
export const DOC_TYPES = ["manual", "policy", "guide", "bulletin", "company"];

/** Force-directed layout with a fixed start, so the same neighbourhood always looks the same. First node is the centre. */
export function layout(nodes: KgNode[], edges: KgEdge[], w: number, h: number): Map<string, { x: number; y: number }> {
  const pos = nodes.map((n, i) => {
    const r = i === 0 ? 0 : n.dist === 2 ? 0.46 : 0.3, a = i * 2.39996;
    return { x: w / 2 + Math.cos(a) * r * w, y: h / 2 + Math.sin(a) * r * h };
  });
  const idx = new Map(nodes.map((n, i) => [n.id, i]));
  const links = edges.map((e) => [idx.get(e.src)!, idx.get(e.dst)!]).filter(([a, b]) => a !== b && a !== undefined && b !== undefined);
  const k = Math.sqrt((w * h) / Math.max(1, nodes.length)) * 0.7;
  for (let step = 0; step < 150; step++) {
    const heat = 12 * (1 - step / 150);
    const f = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < pos.length; i++) for (let j = i + 1; j < pos.length; j++) {
      const dx = pos[i].x - pos[j].x || 0.01, dy = pos[i].y - pos[j].y || 0.01, d2 = Math.max(25, dx * dx + dy * dy), m = (k * k) / d2;
      f[i].x += dx * m; f[i].y += dy * m; f[j].x -= dx * m; f[j].y -= dy * m;
    }
    for (const [a, b] of links) {
      const dx = pos[a].x - pos[b].x, dy = pos[a].y - pos[b].y, m = Math.hypot(dx, dy) / k;
      f[a].x -= dx * m; f[a].y -= dy * m; f[b].x += dx * m; f[b].y += dy * m;
    }
    for (let i = 1; i < pos.length; i++) {                     // the centre stays put
      const len = Math.hypot(f[i].x, f[i].y) || 1, s = Math.min(heat, len) / len;
      pos[i].x = Math.min(w - 50, Math.max(50, pos[i].x + f[i].x * s));
      pos[i].y = Math.min(h - 20, Math.max(20, pos[i].y + f[i].y * s));
    }
  }
  return new Map(nodes.map((n, i) => [n.id, pos[i]]));
}
