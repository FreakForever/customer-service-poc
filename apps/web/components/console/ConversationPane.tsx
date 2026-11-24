"use client";

import { useEffect, useRef, useState } from "react";

import { askedBefore, nice, sourceOf, when, type Detail, type Message } from "@/lib/console";
import { staffApi } from "@/lib/staffClient";

import { Badge, priorityTone } from "./Badge";
import { PromoteForm } from "./PromoteForm";

const WHO = { customer: "Customer", assistant: "Assistant", agent: "Agent", system: "Note" };

function Bubble({ m, detail, selected, onTrace }: { m: Message; detail: Detail; selected: boolean; onTrace: (id: string) => void }) {
  const [promote, setPromote] = useState(false);
  const by = m.actions?.find((a) => a.type === "sent_by" || a.type === "reviewed_by");
  return (
    <li data-role={m.role} data-delivery={m.delivery} className={`max-w-[85%] ${m.role === "customer" ? "" : "ml-auto"}`}>
      <p className="mb-0.5 flex flex-wrap items-center gap-2 text-xs text-steel">
        <b>{m.role === "agent" && by?.name ? by.name : WHO[m.role]}</b><time>{when(m.created_at)}</time>
        {m.delivery !== "sent" && <Badge tone={m.delivery === "failed" ? "bad" : "warn"}>{m.delivery}</Badge>}
        {by?.type === "reviewed_by" && <span>{by.edited ? "edited and sent" : "approved"} by {by.name}</span>}
        {m.rating !== null && <span>{m.rating === 1 ? "rated helpful" : "rated not helpful"}</span>}
      </p>
      <div className={`whitespace-pre-wrap break-words rounded-card border p-3 text-sm ${m.role === "customer" ? "border-line bg-surface" : m.role === "agent" ? "border-ok bg-surface" : "border-line bg-sky"}`}>{m.content}</div>
      {m.attachments.length > 0 && <ul aria-label="Attachments" className="mt-1 text-xs text-steel">{m.attachments.map((a) => <li key={a.filename}>📎 {a.filename} ({a.mime}, {Math.ceil(a.bytes / 1024)} KB)</li>)}</ul>}
      <div className="mt-1 flex gap-2 text-xs">
        {m.trace_id && m.role === "assistant" && <button type="button" aria-pressed={selected} onClick={() => onTrace(m.trace_id!)} className="min-h-6 text-blue underline">AI trace</button>}
        {(m.role === "assistant" || m.role === "agent") && m.delivery === "sent" && <button type="button" aria-expanded={promote} onClick={() => setPromote(!promote)} className="min-h-6 text-blue underline">Promote to FAQ</button>}
      </div>
      {promote && <PromoteForm messageId={m.id} question={askedBefore(detail, m.id)} answer={m.content} source={sourceOf(m)} onClose={() => setPromote(false)} />}
    </li>
  );
}

type Props = { detail: Detail; me: string; traceId: string | null; onTrace: (id: string) => void; reload: () => Promise<void> };

export function ConversationPane({ detail, me, traceId, onTrace, reload }: Props) {
  const c = detail.conversation;
  const [error, setError] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const shown = detail.messages.filter((m) => m.role !== "system");
  useEffect(() => { end.current?.scrollTo({ top: end.current.scrollHeight }); }, [shown.length, c.id]);

  async function act(what: "takeover" | "release" | "close") {
    setError("");
    try { await staffApi(`console/conversations/${c.id}/${what}`, "POST"); } catch (e) { setError((e as Error).message); }
    await reload();
  }
  const mine = c.status === "human" && c.assigned_to === me;
  const btn = "min-h-8 rounded border border-field bg-surface px-3 text-sm font-semibold text-blue hover:bg-sky";
  return (
    <>
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-surface p-3">
        <div className="min-w-0 flex-1"><h1 className="truncate text-lg">{c.subject || detail.customer?.company || c.from_addr || "Visitor"}</h1>
          <p className="flex flex-wrap items-center gap-2 text-xs text-steel">{c.channel}{c.from_addr && ` · ${c.from_addr}`}<Badge>{nice(c.status)}</Badge>{c.priority && <Badge tone={priorityTone(c.priority)}>{c.priority}</Badge>}{c.status === "human" && <span>{mine ? "You" : c.agent_name}</span>}</p></div>
        {c.status !== "closed" && <>
          {mine ? <button type="button" className={btn} onClick={() => void act("release")}>Return to bot</button> : <button type="button" className={btn} onClick={() => void act("takeover")}>Take over</button>}
          <button type="button" className={btn} onClick={() => void act("close")}>Close</button></>}
        {error && <p role="alert" className="w-full text-sm text-bad">{error}</p>}
      </header>
      {detail.summary && <section aria-label="Handoff summary" className="max-h-28 overflow-y-auto border-b border-warn bg-surface p-3 text-sm" tabIndex={0}><b>Handoff summary:</b> {detail.summary}</section>}
      <div ref={end} className="min-h-0 flex-1 overflow-y-auto p-4" tabIndex={0} role="region" aria-label="Messages"><ul className="space-y-4">{shown.map((m) => <Bubble key={m.id} m={m} detail={detail} selected={m.trace_id === traceId} onTrace={onTrace} />)}</ul></div>
    </>
  );
}
