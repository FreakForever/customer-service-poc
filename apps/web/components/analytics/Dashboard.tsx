"use client";

import Link from "next/link";
import { useState } from "react";

import { back, num, ms, pct, score, TIERS, tierLabel, today, usd, type Benchmark, type Flagged, type Summary } from "@/lib/analytics";
import { usePoll } from "@/lib/staffClient";

import { field, Status, Table } from "../admin/ui";
import { Card, DayColumns, LatencyBoxes, Tile } from "./charts";

const TABS = ["Overview", "Cost", "Benchmark"] as const;
const PRESETS = [["1", "Today"], ["7", "Last 7 days"], ["30", "Last 30 days"], ["custom", "Choose dates"]];

export function Dashboard() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("Overview");
  const [preset, setPreset] = useState("7"), [channel, setChannel] = useState("");
  const [own, setOwn] = useState(() => ({ from: back(today(), 6), to: today() }));
  const range = preset === "custom" ? own : { from: back(today(), Number(preset) - 1), to: today() };
  const days = (Date.parse(range.to) - Date.parse(range.from)) / 86_400_000;
  const bad = !(days >= 0 && days < 92);
  const q = `from=${range.from}&to=${range.to}&channel=${channel}`;
  const sum = usePoll<Summary>(tab === "Benchmark" || bad ? null : `analytics/summary?${q}`, 60_000);
  const s = sum.data;
  return (
    <div className="mx-auto max-w-6xl">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
        <h1 className="text-2xl">Analytics</h1>
        <nav aria-label="Analytics" className="flex gap-2">
          {TABS.map((t) => <button key={t} type="button" aria-pressed={tab === t} onClick={() => setTab(t)} className={`min-h-8 rounded px-3 text-sm ${tab === t ? "bg-blue font-semibold text-surface" : "bg-surface hover:bg-sky"}`}>{t}</button>)}
        </nav>
      </div>
      {tab === "Benchmark" ? <BenchmarkTab /> : <>
        <div className="mt-3 flex flex-wrap items-end gap-3 text-sm">
          <label>Period<select className={`${field} ml-2`} value={preset} onChange={(e) => { if (e.target.value === "custom") setOwn(range); setPreset(e.target.value); }}>{PRESETS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          {preset === "custom" && <>
            <label>From<input type="date" className={`${field} ml-2`} value={own.from} max={own.to} onChange={(e) => setOwn({ ...own, from: e.target.value })} /></label>
            <label>To<input type="date" className={`${field} ml-2`} value={own.to} onChange={(e) => setOwn({ ...own, to: e.target.value })} /></label></>}
          <label>Channel<select className={`${field} ml-2`} value={channel} onChange={(e) => setChannel(e.target.value)}><option value="">All</option><option value="chat">Chat</option><option value="email">Email</option></select></label>
        </div>
        <Status error={bad ? "Choose a period of 1 to 92 days." : sum.error} loading={!s && !bad} />
        {s && (tab === "Overview" ? <Overview s={s} q={q} /> : <Cost s={s} />)}
      </>}
    </div>
  );
}

function Overview({ s, q }: { s: Summary; q: string }) {
  const flagged = usePoll<{ items: Flagged[] }>(`analytics/flagged?${q}`, 60_000);
  const lat = s.latency_p50_p95.overall, g = s.groundedness;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Conversations" value={num(s.volume.conversations)} sub={`${num(s.volume.turns)} answers`} />
        <Tile label="Deflection" value={pct(s.deflection.rate)} sub={`${num(s.deflection.conversations)} without a person`} />
        <Tile label="Handoff rate" value={pct(s.handoff_rate.rate)} sub={`${num(s.handoff_rate.conversations)} handed off`} />
        <Tile label="Median latency" value={ms(lat.p50)} />
        <Tile label="p95 latency" value={ms(lat.p95)} />
        <Tile label="Groundedness" value={score(g.score)} sub={`${num(g.judged)} judged · citations ${pct(g.citation_coverage)}`} />
        <Tile label="Cost per conversation" value={usd(s.cost.per_conversation)} sub={`${usd(s.cost.total)} in total`} />
        <Tile label="Positive feedback" value={pct(s.feedback.positive)} sub={`${num(s.feedback.up)} up · ${num(s.feedback.down)} down`} />
      </div>
      <p className="text-sm text-steel">Days are Indian time. Deflection counts conversations where nobody from Matsui was needed, including ones the customer left.</p>
      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Answers by tier, per day"><DayColumns label="Answers by tier per day" days={s.by_day} whole unit={num} segs={(d) => TIERS.map((t) => ({ label: t.label, bg: t.bg, value: d[t.key] }))} /></Card>
        <Card title="Latency per tier"><LatencyBoxes rows={s.latency_p50_p95.by_tier} /></Card>
        <Card title="Feedback, per day"><DayColumns label="Feedback per day" days={s.by_day} whole unit={num} segs={(d) => [{ label: "Thumbs up", bg: "bg-chart-1", value: d.up }, { label: "Thumbs down", bg: "bg-chart-2", value: d.down }]} /></Card>
        <Card title="Volume by channel">
          <Table heads={["Channel", "Conversations", "Answers"]} label="Volume by channel">{s.volume.by_channel.map((c) => <tr key={c.channel}><td>{c.channel}</td><td>{num(c.conversations)}</td><td>{num(c.turns)}</td></tr>)}</Table>
          <h3 className="mt-4 text-sm">Tier share</h3>
          <Table heads={["Tier", "Answers", "Share"]} label="Tier share">{s.tier_mix.map((t) => <tr key={t.tier}><td>{tierLabel(t.tier)}</td><td>{num(t.turns)}</td><td>{pct(t.share)}</td></tr>)}</Table>
        </Card>
      </div>
      <Card title="Flagged answers" note="Thumbs down, failed checks, held back, or groundedness under 0.80. Newest first.">
        {flagged.error && <p role="alert" className="text-sm text-bad">{flagged.error}</p>}
        {flagged.data && (flagged.data.items.length ? (
          <ul aria-label="Flagged answers" className="divide-y divide-line">
            {flagged.data.items.map((f) => (
              <li key={f.trace_id} className="py-2 text-sm">
                <p><span className="font-semibold">{f.reasons.join(", ")}</span> <span className="text-steel">· {f.tier} · {f.channel} · {new Date(f.created_at).toLocaleString("en-GB", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Kolkata" })}</span></p>
                {f.answer && <p className="break-words">{f.answer}</p>}{f.reason && <p className="text-steel">Customer: {f.reason}</p>}
                {f.conversation_id && <Link className="inline-flex min-h-6 items-center text-blue underline" href={`/console/c/${f.conversation_id}`}>Open conversation</Link>}
              </li>
            ))}
          </ul>
        ) : <p className="text-sm text-steel">Nothing flagged in this period.</p>)}
      </Card>
    </div>
  );
}

function Cost({ s }: { s: Summary }) {
  const c = s.cost;
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Tile label="Cost per conversation" value={usd(c.per_conversation)} sub={`${num(c.priced_conversations)} conversations`} />
        <Tile label="Total cost" value={usd(c.total)} />
        <Tile label="Tokens in" value={num(c.tokens_in)} />
        <Tile label="Tokens out" value={num(c.tokens_out)} />
      </div>
      <p className="text-sm text-steel">Estimates: tokens × list price, without embeddings. The free tier isn&apos;t billed.{c.unpriced_turns > 0 && ` ${num(c.unpriced_turns)} answers have no cost recorded, so they and their conversations are left out of the cost figures.`}</p>
      <Card title="Cost per day"><DayColumns label="Cost per day" days={s.by_day} unit={usd} segs={(d) => [{ label: "Cost", bg: "bg-chart-1", value: d.cost ?? 0 }]} /></Card>
      <Card title="Cost per tier">
        <Table heads={["Tier", "Answers", "Tokens in", "Tokens out", "Total", "Per answer"]} label="Cost per tier">
          {c.by_tier.map((t) => <tr key={t.tier}><td>{tierLabel(t.tier)}</td><td>{num(t.turns)}</td><td>{num(t.tokens_in)}</td><td>{num(t.tokens_out)}</td><td>{usd(t.total)}</td><td>{usd(t.per_turn)}</td></tr>)}
        </Table>
      </Card>
      <Card title="Cost per model">
        <Table heads={["Model", "Calls", "Tokens in", "Tokens out", "Cost"]} label="Cost per model">
          {c.by_model.map((m) => <tr key={m.model}><td className="break-words">{m.model}{m.estimated && " (tokens estimated)"}</td><td>{num(m.calls)}</td><td>{num(m.tokens_in)}</td><td>{num(m.tokens_out)}</td><td>{m.cost === null ? "not priced" : usd(m.cost)}</td></tr>)}
        </Table>
      </Card>
      <Card title="Most expensive conversations">
        <Table heads={["Conversation", "Channel", "Answers", "Tokens", "Cost"]} label="Most expensive conversations">
          {c.top_conversations.map((t) => <tr key={t.id}><td>{t.available ? <Link className="text-blue underline" href={`/console/c/${t.id}`}>{t.subject || t.id.slice(0, 8)}</Link> : `${t.id.slice(0, 8)} (deleted)`}</td><td>{t.channel}</td><td>{num(t.turns)}</td><td>{num(t.tokens)}</td><td>{usd(t.cost)}</td></tr>)}
        </Table>
      </Card>
    </div>
  );
}

const MODE: Record<string, string> = { baseline: "Baseline agent", rag: "RAG only", kg: "KG only", hybrid: "Hybrid (production)" };

function BenchmarkTab() {
  const { data, error } = usePoll<Benchmark>("analytics/benchmark", 60_000);
  const modes = data?.by_mode ?? [];
  return (
    <div className="space-y-4">
      <Status error={error} loading={!data} />
      {data && (modes.length === 0 ? <Card title="Benchmark"><p>No benchmark run has been recorded yet. Results appear here after the evaluation harness (<code>make eval</code>) has run.</p></Card> : <>
        <Card title="Answer correctness by mode" note="Latest run of each mode.">
          <ul aria-label="Correctness by mode" className="space-y-3">
            {modes.map((m) => <li key={m.mode} className="flex items-center gap-3 text-sm"><span className="w-32 shrink-0">{MODE[m.mode] ?? m.mode}</span><span aria-hidden className="h-5 min-w-0 flex-1 bg-bg"><span className="block h-full rounded-r bg-chart-1" style={{ width: `${(m.correctness ?? 0) * 100}%` }} /></span><span className="w-14 text-right tabular-nums">{pct(m.correctness)}</span></li>)}
          </ul>
        </Card>
        <Card title="Quality, latency and tokens by mode">
          <Table heads={["Mode", "Questions", "Correct", "Groundedness", "p50", "p95", "Tokens", "Cost", "Run"]} label="By mode">
            {modes.map((m) => <tr key={m.mode}><td>{MODE[m.mode] ?? m.mode}</td><td>{m.n === null ? "–" : num(m.n)}</td><td>{pct(m.correctness)}</td><td>{score(m.groundedness)}</td><td>{ms(m.p50_ms)}</td><td>{ms(m.p95_ms)}</td><td>{m.tokens === null ? "–" : num(m.tokens)}</td><td>{usd(m.cost_usd)}</td><td>{m.started_at ? m.started_at.slice(0, 10) : "–"}</td></tr>)}
          </Table>
        </Card>
        <Card title="Correctness by question type">
          <Table heads={["Question type", ...modes.map((m) => MODE[m.mode] ?? m.mode)]} label="By question type">
            {data.by_type.map((t) => <tr key={t.type}><td>{t.type.replace(/_/g, " ")}</td>{modes.map((m) => <td key={m.mode}>{t.modes[m.mode] ? `${pct(t.modes[m.mode].correctness ?? null)}${t.modes[m.mode].n ? ` of ${t.modes[m.mode].n}` : ""}` : "–"}</td>)}</tr>)}
          </Table>
        </Card>
      </>)}
    </div>
  );
}
