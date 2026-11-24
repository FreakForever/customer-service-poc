"use client";

import { useMemo, useState } from "react";

import { layout, type Hood, type KgNode } from "@/lib/admin";
import { usePoll } from "@/lib/staffClient";

import { field, Status } from "./ui";

const W = 900, H = 560;

function Graph({ hood, onPick }: { hood: Hood; onPick: (id: string) => void }) {
  const pos = useMemo(() => layout(hood.nodes, hood.edges, W, H), [hood]);
  const short = (s: string) => (s.length > 22 ? `${s.slice(0, 21)}…` : s);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={`Neighbourhood of ${hood.nodes[0].name}: ${hood.nodes.length - 1} nodes. The same facts are listed below.`} className="w-full rounded-card border border-line bg-surface">
      {hood.edges.map((e, i) => { const a = pos.get(e.src), b = pos.get(e.dst); return a && b && <line key={i} x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke="var(--mt-line)" strokeWidth={1.5}><title>{`${e.src} ${e.type} ${e.dst}`}</title></line>; })}
      {hood.nodes.map((n, i) => { const p = pos.get(n.id)!; return (
        <g key={n.id} transform={`translate(${p.x} ${p.y})`} onClick={() => onPick(n.id)} className="cursor-pointer">
          <title>{`${n.name} (${n.label})`}</title><circle r={i === 0 ? 13 : 8} fill={i === 0 ? "var(--mt-navy)" : "var(--mt-blue)"} /><text y={i === 0 ? 28 : 21} textAnchor="middle" fontSize={11} fill="var(--mt-navy)">{short(n.id)}</text></g>); })}
    </svg>
  );
}

export function Explorer() {
  const [q, setQ] = useState("");
  const [id, setId] = useState("DHD-150");
  const [depth, setDepth] = useState(1);
  const { data: found } = usePoll<{ items: KgNode[] }>(q.trim() ? `admin/kg/search?q=${encodeURIComponent(q)}` : null, 600_000);
  const { data, error } = usePoll<Hood>(`admin/kg/node/${encodeURIComponent(id)}?depth=${depth}`, 600_000);
  const pick = (x: string) => { setId(x); setQ(""); };
  const me = data?.nodes[0];
  const name = new Map(data?.nodes.map((n) => [n.id, n.name]));
  return (
    <section aria-labelledby="h-k"><h1 id="h-k" className="text-2xl">Graph explorer</h1>
      <div className="mt-2 flex flex-wrap gap-4 text-sm">
        <div className="relative"><label>Find a node <input type="search" maxLength={80} className={field} value={q} onChange={(e) => setQ(e.target.value)} /></label>
          {found && <ul aria-label="Nodes found" className="absolute z-10 w-80 max-w-[90vw] rounded border border-line bg-surface shadow-lg">{found.items.map((n) => <li key={n.id}><button type="button" className="w-full px-3 py-1.5 text-left hover:bg-sky" onClick={() => pick(n.id)}>{n.id} · {n.name} <span className="text-steel">({n.label})</span></button></li>)}{!found.items.length && <li className="px-3 py-1.5 text-steel">Nothing found</li>}</ul>}</div>
        <label>Depth <select className={field} value={depth} onChange={(e) => setDepth(Number(e.target.value))}><option value={1}>1</option><option value={2}>2</option></select></label>
      </div>
      <Status error={error} loading={!data} note={data?.truncated ? "Only the nearest nodes are shown." : ""} />
      {data && me && (
        <div className="grid gap-4 xl:grid-cols-[1fr_22rem]">
          <Graph hood={data} onPick={pick} />
          <div className="space-y-4 text-sm"><section><h2 className="text-lg">{me.name}</h2><p className="text-steel">{me.label} · source {me.source ?? "unknown"}</p>
            <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3">{Object.entries(me.props ?? {}).filter(([k]) => !["name", "source"].includes(k)).map(([k, v]) => <div key={k} className="contents"><dt className="text-steel">{k}</dt><dd className="break-words">{String(v)}</dd></div>)}</dl></section>
            <section><h2 className="text-lg">Facts ({data.edges.length})</h2><ul aria-label="Facts" tabIndex={0} className="max-h-96 space-y-1 overflow-y-auto">{data.edges.map((e, i) => (
              <li key={i}>{[e.src, e.dst].map((x, j) => <span key={j}>{j === 1 && ` ${e.type} `}<button type="button" title={name.get(x)} className="text-blue underline" onClick={() => pick(x)}>{x}</button></span>)}{e.source && e.source !== "seed" && <span className="text-steel"> ({e.source})</span>}</li>))}</ul></section></div>
        </div>)}
    </section>
  );
}
