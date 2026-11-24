"use client";

import { useState, type ReactNode } from "react";

import { day, ms, num, ticks, TIERS, tierLabel, type Box, type Day } from "@/lib/analytics";

import { Table } from "../admin/ui";

export function Card({ title, note, children }: { title: string; note?: string; children: ReactNode }) {
  return <section className="min-w-0 rounded-card border border-line bg-surface p-4"><h2 className="text-base">{title}</h2>{note && <p className="text-sm text-steel">{note}</p>}<div className="mt-3">{children}</div></section>;
}
export function Tile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return <div role="group" aria-label={label} className="rounded-card border border-line bg-surface p-4"><p className="text-sm text-steel">{label}</p><p className="text-2xl font-semibold">{value}</p>{sub && <p className="text-sm text-steel">{sub}</p>}</div>;
}
export function AsTable({ label, heads, rows }: { label: string; heads: string[]; rows: ReactNode[][] }) {
  return <details className="mt-3"><summary className="inline-flex min-h-8 cursor-pointer items-center text-sm text-blue underline">Show as table</summary>
    <Table heads={heads} label={label}>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j} className={j ? "tabular-nums" : ""}>{c}</td>)}</tr>)}</Table></details>;
}
export function Legend({ items }: { items: { label: string; bg: string }[] }) {
  return <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm">{items.map((i) => <li key={i.label} className="flex items-center gap-2"><span aria-hidden className={`inline-block size-3 rounded-sm ${i.bg}`} />{i.label}</li>)}</ul>;
}

type Seg = { label: string; bg: string; value: number };
/** Stacked columns by day. Columns can be reached with the keyboard; the line under the chart reads out the selected day. */
export function DayColumns({ label, days, segs, unit, whole = false }: { whole?: boolean; label: string; days: Day[]; segs: (d: Day) => Seg[]; unit: (n: number) => string }) {
  const [on, setOn] = useState<number | null>(null);
  const data = days.map((d) => ({ day: d.day, segs: segs(d) }));
  const axis = ticks(Math.max(0, ...data.map((d) => d.segs.reduce((a, s) => a + s.value, 0))), whole);
  const max = axis[axis.length - 1], every = Math.ceil(data.length / 7);
  const text = (d: (typeof data)[number]) => `${day(d.day)}: ${d.segs.filter((s) => s.value > 0).map((s) => `${s.label} ${unit(s.value)}`).join(", ") || "none"}`;
  const used = data[0]?.segs.filter((_, i) => data.some((d) => d.segs[i].value > 0)) ?? [];
  if (!used.length) return <p className="text-sm text-steel">Nothing in this period.</p>;
  return (
    <div>
      {data[0].segs.length > 1 && <Legend items={used} />}
      <div className="flex">
        <div aria-hidden className="relative mr-2 h-48 w-14 text-right text-xs tabular-nums text-steel">
          {axis.map((t) => <span key={t} className="absolute right-0 translate-y-1/2 whitespace-nowrap" style={{ bottom: `${(t / max) * 100}%` }}>{unit(t)}</span>)}
        </div>
        <div className="min-w-0 flex-1">
          <div className="relative h-48 border-b border-field">
            {axis.slice(1).map((t) => <div key={t} aria-hidden className="absolute inset-x-0 border-t border-line" style={{ bottom: `${(t / max) * 100}%` }} />)}
            <ul aria-label={label} className="absolute inset-0 flex items-stretch">
              {data.map((d, i) => (
                <li key={d.day} className="min-w-0 flex-1">
                  <button type="button" aria-label={text(d)} onMouseEnter={() => setOn(i)} onMouseLeave={() => setOn(null)} onFocus={() => setOn(i)} onBlur={() => setOn(null)}
                    className={`flex h-full w-full flex-col-reverse items-center ${on === i ? "bg-sky" : ""}`}>
                    {d.segs.filter((s) => s.value > 0).map((s, j, all) => <span key={s.label} className={`block w-3/5 max-w-6 ${s.bg} ${j === all.length - 1 ? "rounded-t" : ""} ${j ? "border-b-2 border-surface" : ""}`} style={{ height: `${(s.value / max) * 100}%` }} />)}
                  </button>
                </li>
              ))}
            </ul>
          </div>
          <div aria-hidden className="flex text-xs text-steel">{data.map((d, i) => <span key={d.day} className="min-w-0 flex-1 overflow-visible whitespace-nowrap text-center">{i % every === 0 && <span className={(i / every) % 2 ? "hidden sm:inline" : ""}>{day(d.day)}</span>}</span>)}</div>
        </div>
      </div>
      <p aria-hidden className="mt-2 min-h-5 text-sm">{on === null ? <span className="text-steel">Point at a day for its numbers.</span> : text(data[on])}</p>
      <AsTable label={`${label} as a table`} heads={["Day", ...used.map((u) => u.label)]}
        rows={data.map((d) => [day(d.day), ...used.map((u) => unit(d.segs.find((s) => s.label === u.label)!.value))])} />
    </div>
  );
}

/** Horizontal boxes on one scale: the box is the middle half, the tick inside it the median, the thin line the 5th to 95th percentile. */
export function LatencyBoxes({ rows }: { rows: (Box & { tier: string })[] }) {
  const data = TIERS.flatMap((t) => rows.filter((r) => r.tier === t.id && r.n > 0));
  const axis = ticks(Math.max(0, ...data.map((r) => r.p95 ?? 0))), max = axis[axis.length - 1];
  const at = (v: number | null) => `${((v ?? 0) / max) * 100}%`, wide = (a: number | null, b: number | null) => `${(((b ?? 0) - (a ?? 0)) / max) * 100}%`;
  if (!data.length) return <p className="text-sm text-steel">Nothing in this period.</p>;
  return (
    <div>
      <ul aria-label="Latency per tier" className="space-y-3">
        {data.map((r) => {
          const t = TIERS.find((x) => x.id === r.tier)!;
          return (
            <li key={r.tier} className="sm:flex sm:items-center sm:gap-3">
              <div className="text-sm sm:w-40">{t.label}</div>
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <div aria-hidden className="relative h-5 min-w-0 flex-1 bg-bg">
                  <span className="absolute top-1/2 h-px -translate-y-1/2 bg-steel" style={{ left: at(r.p5), width: wide(r.p5, r.p95) }} />
                  <span className={`absolute inset-y-0.5 min-w-1 rounded-sm ${t.bg}`} style={{ left: at(r.p25), width: wide(r.p25, r.p75) }} />
                  <span className="absolute inset-y-0 w-0.5 bg-navy" style={{ left: at(r.p50) }} />
                </div>
                <span className="w-40 text-right text-sm tabular-nums">p50 {ms(r.p50)} · p95 {ms(r.p95)}</span>
              </div>
            </li>
          );
        })}
      </ul>
      <p className="mt-2 text-sm text-steel">Scale 0 to {ms(max)}. Box: middle half of answers. Dark tick: median. Line: 5th to 95th percentile.</p>
      <AsTable label="Latency as a table" heads={["Tier", "Answers", "p5", "p25", "p50", "p75", "p95"]}
        rows={data.map((r) => [tierLabel(r.tier), num(r.n), ms(r.p5), ms(r.p25), ms(r.p50), ms(r.p75), ms(r.p95)])} />
    </div>
  );
}
