"use client";

import Link from "next/link";
import { useState } from "react";

import { Markdown } from "@/components/help/Markdown";
import { chipNo, display, type Citation, type Msg } from "@/lib/chat";

import { InlineForm } from "./InlineForm";

const chip = "inline-flex min-h-7 max-w-full items-center gap-1 rounded-full border border-blue bg-surface px-2 text-left text-xs text-blue hover:bg-sky";

function CitationChips({ items }: { items: Citation[] }) {
  const [openFact, setFact] = useState<string | null>(null);
  return (
    <>
      <ul aria-label="Sources" className="mt-2 flex flex-wrap gap-1">
        {items.map((c) => (
          <li key={c.marker} className="max-w-full">
            {c.url ? <Link href={c.url} className={chip}><b>{chipNo(c)}</b><span className="truncate">{c.title}</span></Link>
              : <button type="button" className={chip} aria-expanded={openFact === c.marker} onClick={() => setFact(openFact === c.marker ? null : c.marker)}><b>{chipNo(c)}</b><span className="truncate">{c.title}</span></button>}
          </li>
        ))}
      </ul>
      {openFact && <p role="note" className="mt-2 rounded border border-line bg-sky p-2 text-xs">{items.find((c) => c.marker === openFact)?.fact ?? "From Matsui's product data."}</p>}
    </>
  );
}

type Props = { msgs: Msg[]; busy: boolean; formErrors: Record<string, string>; onQuick: (payload: string, label: string) => void;
  onForm: (id: string, f: Record<string, string>) => void; onRate: (m: Msg, r: 1 | -1) => void };

export function MessageList({ msgs, busy, formErrors, onQuick, onForm, onRate }: Props) {
  return (
    <>
      {msgs.map((m) => (
        <div key={m.key} data-role={m.role} data-tier={m.tier} className={`max-w-[88%] ${m.role === "customer" ? "ml-auto" : ""}`}>
          {m.from && <p className="mb-1 text-xs font-semibold text-steel">{m.from}</p>}
          <div className={`break-words rounded-card px-3 py-2 text-sm ${m.role === "customer" ? "bg-blue text-surface" : m.role === "agent" ? "border border-ok bg-surface" : "bg-sky"} ${m.error ? "border border-bad" : ""}`}>
            {m.role === "customer" ? <span className="whitespace-pre-wrap">{m.text}</span> : <Markdown>{display(m.text)}</Markdown>}
          </div>
          {m.citations && m.citations.length > 0 && <CitationChips items={m.citations} />}
          {m.form && <InlineForm form={m.form} errors={formErrors} busy={busy} onSubmit={(f) => onForm(m.form!.form_id, f)} />}
          {m.role === "assistant" && m.id && (
            <div className="mt-1 flex gap-1" role="group" aria-label="Rate this answer">
              {([1, -1] as const).map((r) => (
                <button key={r} type="button" aria-pressed={m.rating === r} aria-label={r === 1 ? "Helpful" : "Not helpful"} onClick={() => onRate(m, r)}
                  className={`min-h-7 min-w-7 rounded border text-sm ${m.rating === r ? "border-blue bg-sky" : "border-line"}`}><span aria-hidden>{r === 1 ? "👍" : "👎"}</span></button>
              ))}
            </div>
          )}
          {m.quick && m.quick.length > 0 && (
            <ul aria-label="Suggested replies" className="mt-2 flex flex-wrap gap-2">
              {m.quick.map((q) => <li key={q.label}><button type="button" disabled={busy} onClick={() => onQuick(q.payload, q.label)} className="min-h-8 rounded-full border border-blue bg-surface px-3 py-1 text-left text-sm font-medium text-blue hover:bg-sky">{q.label}</button></li>)}
            </ul>
          )}
        </div>
      ))}
    </>
  );
}
