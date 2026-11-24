import Link from "next/link";

import type { Trace } from "@/lib/console";

import { Badge } from "./Badge";

const pct = (n: number | null | undefined) => (n === null || n === undefined ? "–" : n.toFixed(2));

export function TraceTimeline({ t }: { t: Trace }) {
  const start = t.spans.length ? Math.min(...t.spans.map((s) => Date.parse(s.started_at))) : 0;
  const total = Math.max(1, t.trace.latency_ms, ...t.spans.map((s) => Date.parse(s.started_at) - start + s.duration_ms));
  const rows: [string, string][] = [["Intents", t.trace.intents.join(", ") || "–"], ["Confidence", pct(t.trace.confidence)], ["Coverage", pct(t.verification?.coverage)],
    ["Judge", pct(t.verification?.judge)], ["Latency", `${t.trace.latency_ms} ms`], ["Tokens", `${t.trace.tokens_in} in, ${t.trace.tokens_out} out`],
    ["Cost", t.trace.cost_usd === null ? "not recorded" : `$${t.trace.cost_usd}`], ["Handoff", t.trace.handoff_reason ?? "–"], ["Flags", t.trace.flags?.join(", ") || "–"]];
  return (
    <div className="space-y-5 p-4 text-sm">
      <p className="flex items-center gap-2"><Badge tone="ok">{t.trace.answered_tier ?? "no tier"}</Badge>{t.verification && <Badge tone={t.verification.passed ? "ok" : "bad"}>{t.verification.passed ? "checks passed" : "checks failed"}</Badge>}</p>
      <dl className="grid grid-cols-[6rem_1fr] gap-x-2 gap-y-1">{rows.map(([k, v]) => <div key={k} className="contents"><dt className="text-steel">{k}</dt><dd className="break-words">{v}</dd></div>)}</dl>
      {t.verification && t.verification.issues.length > 0 && <section><h2 className="text-base">Issues</h2><ul className="list-disc pl-5">{t.verification.issues.map((i) => <li key={i}>{i}</li>)}</ul></section>}
      <section><h2 className="text-base">Timeline</h2>
        <ol aria-label="Spans" className="mt-1 space-y-1.5">{t.spans.map((s, i) => (
          <li key={i}><span className="flex justify-between gap-2 text-xs"><span className="truncate">{s.node}{s.model && ` · ${s.model}`}{s.cache_hit && " · cache hit"}{s.attrs?.status !== "ok" && s.attrs?.status ? ` · ${String(s.attrs.status)}` : ""}</span><span className="whitespace-nowrap">{s.duration_ms} ms</span></span>
            <span aria-hidden className="block h-1.5 rounded bg-sky"><span className="block h-1.5 rounded bg-blue" style={{ marginLeft: `${Math.min(99, ((Date.parse(s.started_at) - start) / total) * 100)}%`, width: `${Math.max(1, Math.min(100, (s.duration_ms / total) * 100))}%` }} /></span></li>))}</ol></section>
      <section><h2 className="text-base">Evidence ({t.evidence.length})</h2>
        <ul aria-label="Evidence" className="mt-1 space-y-2">{t.evidence.map((e, i) => (
          <li key={i} className="rounded border border-line p-2"><span className="block break-words text-xs font-semibold">{i + 1}. {e.doc_id ? <Link className="text-blue underline" target="_blank" href={`/a/${e.citation.includes("#") ? e.citation : e.doc_id}`}>{e.title ?? e.citation}</Link> : (e.title ?? e.citation)} · {e.kind}{e.score !== null && ` · ${e.score.toFixed(2)}`}</span><span className="text-xs text-steel">{e.text}</span></li>))}</ul></section>
    </div>
  );
}
