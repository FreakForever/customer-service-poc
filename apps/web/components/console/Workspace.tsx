"use client";

import { useState } from "react";

import { suggestion, type Detail, type Trace } from "@/lib/console";
import { usePoll } from "@/lib/staffClient";

import { ContextPanel } from "./ContextPanel";
import { ConversationPane } from "./ConversationPane";
import { ReplyEditor } from "./ReplyEditor";
import { TraceTimeline } from "./TraceTimeline";

/** Centre and right panes (FR-UI-04). */
export function Workspace({ id, me }: { id: string; me: string }) {
  const { data: d, error, reload } = usePoll<Detail>(`console/conversations/${id}`, 2000);
  const [tab, setTab] = useState<"customer" | "trace">("customer");
  const [picked, setPicked] = useState<string | null>(null);
  const traced = d?.messages.filter((m) => m.trace_id && m.role === "assistant") ?? [];
  const tid = picked ?? traced.at(-1)?.trace_id ?? null;
  const { data: trace, error: traceError } = usePoll<Trace>(tid ? `console/traces/${tid}` : null, 60_000);
  if (!d) return <p className="p-6" role="status">{error || "Loading…"}</p>;
  const last = traced.at(-1)?.trace_id === tid ? trace : null;
  const tabCls = (on: boolean) => `min-h-10 flex-1 border-b-2 text-sm font-semibold ${on ? "border-blue text-blue" : "border-line text-steel"}`;
  return (
    <div className="grid min-h-0 grid-rows-[minmax(28rem,1fr)_auto] lg:grid-cols-[1fr_20rem] lg:grid-rows-1">
      <section aria-label="Conversation" className="flex min-h-0 min-w-0 flex-col">
        {error && <p role="alert" className="bg-surface p-2 text-sm text-bad">{error}</p>}
        <ConversationPane detail={d} me={me} traceId={tab === "trace" ? tid : null} reload={reload} onTrace={(t) => { setPicked(t); setTab("trace"); }} />
        <ReplyEditor key={id} detail={d} me={me} start={suggestion(d, last)} onSent={reload} />
      </section>
      <aside aria-label="Context" className="flex min-h-0 flex-col border-l border-line bg-surface">
        <div role="tablist" aria-label="Context" className="flex">
          {(["customer", "trace"] as const).map((t) => <button key={t} type="button" role="tab" id={`tab-${t}`} aria-selected={tab === t} aria-controls="context-panel" tabIndex={tab === t ? 0 : -1} onClick={() => setTab(t)}
            onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { const n = t === "customer" ? "trace" : "customer"; setTab(n); document.getElementById(`tab-${n}`)?.focus(); } }} className={tabCls(tab === t)}>{t === "customer" ? "Customer" : "AI trace"}</button>)}
        </div>
        <div id="context-panel" role="tabpanel" aria-labelledby={`tab-${tab}`} tabIndex={0} className="min-h-0 flex-1 overflow-y-auto">
          {tab === "customer" ? <ContextPanel d={d} /> : trace ? <TraceTimeline t={trace} /> : <p className="p-4 text-sm text-steel">{traceError || (tid ? "Loading…" : "No AI answer in this conversation.")}</p>}
        </div>
      </aside>
    </div>
  );
}
