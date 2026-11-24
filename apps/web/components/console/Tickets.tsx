"use client";

import { useState } from "react";

import { nice, TICKET_STATUSES, type Ticket } from "@/lib/console";
import { staffApi, usePoll } from "@/lib/staffClient";

import { Badge, priorityTone } from "./Badge";

type Row = Ticket & { company: string | null; serial_no: string | null };
type Data = { items: Row[]; next: number | null; engineers: string[] };

export function Tickets() {
  const [f, setF] = useState({ status: "open", priority: "", mine: false, q: "" });
  const [note, setNote] = useState("");
  const { data, error, reload } = usePoll<Data>(`console/tickets?status=${f.status}&priority=${f.priority}&mine=${f.mine}&limit=100&q=${encodeURIComponent(f.q)}`, 15_000);
  async function patch(id: string, change: Record<string, string | null>) {
    try { await staffApi(`console/tickets/${id}`, "PATCH", change); setNote(`${id} updated`); } catch (e) { setNote((e as Error).message); }
    await reload();
  }
  const sel = "min-h-8 rounded border border-field bg-surface px-2 text-sm";
  return (
    <section aria-labelledby="h-t" className="min-w-0 overflow-auto p-4" tabIndex={0}>
      <h1 id="h-t" className="text-2xl">Tickets</h1>
      <div className="my-3 flex flex-wrap items-center gap-3 text-sm">
        <label>Status <select className={sel} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="open">open</option><option value="">all</option>{TICKET_STATUSES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
        <label>Priority <select className={sel} value={f.priority} onChange={(e) => setF({ ...f, priority: e.target.value })}><option value="">all</option><option>P1</option><option>P2</option><option>P3</option></select></label>
        <label><input type="checkbox" checked={f.mine} onChange={(e) => setF({ ...f, mine: e.target.checked })} /> My tickets</label>
        <label>Search <input type="search" className={sel} maxLength={100} value={f.q} onChange={(e) => setF({ ...f, q: e.target.value })} /></label>
        <span role="status" className="text-steel">{note}</span>{error && <span role="alert" className="text-bad">{error}</span>}
      </div>
      <table className="w-full border-collapse bg-surface text-left text-sm">
        <thead><tr className="bg-sky">{["Ticket", "Customer", "Issue", "Priority", "Status", "Engineer", "Visit"].map((h) => <th key={h} scope="col" className="border border-line p-2">{h}</th>)}</tr></thead>
        <tbody>{data?.items.map((t) => (
          <tr key={t.id}>
            <th scope="row" className="border border-line p-2 whitespace-nowrap">{t.id}</th><td className="border border-line p-2">{t.company ?? "unverified"}</td>
            <td className="border border-line p-2">{t.summary}<span className="block text-xs text-steel">{[t.model, t.error_code, t.serial_no].filter(Boolean).join(" · ")}</span></td>
            <td className="border border-line p-2"><Badge tone={priorityTone(t.priority)}>{t.priority}</Badge></td>
            <td className="border border-line p-2"><select aria-label={`Status of ${t.id}`} className={sel} value={t.status} onChange={(e) => void patch(t.id, { status: e.target.value })}>{TICKET_STATUSES.map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></td>
            <td className="border border-line p-2"><select aria-label={`Engineer for ${t.id}`} className={sel} value={t.assigned_engineer ?? ""} onChange={(e) => void patch(t.id, { assigned_engineer: e.target.value || null })}><option value="">none</option>{data.engineers.map((n) => <option key={n}>{n}</option>)}</select></td>
            <td className="border border-line p-2"><input type="date" aria-label={`Visit date of ${t.id}`} className={sel} value={t.scheduled_visit ?? ""} onChange={(e) => void patch(t.id, { scheduled_visit: e.target.value || null })} /></td>
          </tr>))}</tbody>
      </table>
      {data && !data.items.length && <p className="mt-3 text-steel">No tickets match.</p>}
      {data?.next && <p className="mt-3 text-steel">Only the first 100 are shown. Narrow the filters to see others.</p>}
    </section>
  );
}
