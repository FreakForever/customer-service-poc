"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/console/Badge";
import type { Change } from "@/lib/admin";
import { nice } from "@/lib/console";
import { staffApi, usePoll } from "@/lib/staffClient";

import { field, primary, secondary, Status } from "./ui";

const str = (v: unknown) => (typeof v === "string" ? v : JSON.stringify(v));

export function Changes() {
  const [status, setStatus] = useState("pending");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, error, reload } = usePoll<{ items: Change[] }>(`admin/kg/changes?status=${status}`, 5000);
  async function decide(c: Change, decision: "approve" | "reject") {
    setBusy(true);
    try { await staffApi(`admin/kg/changes/${c.id}/decision`, "POST", { decision }); setNote(decision === "approve" ? "Approved and written to the graph" : "Rejected"); } catch (e) { setNote((e as Error).message); }
    setBusy(false);
    await reload();
  }
  return (
    <section aria-labelledby="h-c"><h1 id="h-c" className="text-2xl">Graph changes</h1>
      <label className="text-sm">Show <select className={field} value={status} onChange={(e) => setStatus(e.target.value)}><option value="pending">pending</option><option value="approved">approved</option><option value="rejected">rejected</option><option value="">all</option></select></label>
      <Status error={error} note={note} loading={!data} />
      {data && !data.items.length && <p className="text-steel">No changes to show. Extraction from uploads arrives in BS-28.</p>}
      <ul aria-label="Changes" className="grid gap-4 lg:grid-cols-2">{data?.items.map((c) => {
        const { props, ...rest } = c.payload as { props?: Record<string, unknown> };
        const r = rest as Record<string, unknown>;
        return (
          <li key={c.id} className="rounded-card border border-line bg-surface p-4 text-sm">
            <p className="flex flex-wrap items-center gap-2"><Badge tone={c.op.startsWith("add") ? "ok" : "warn"}>{nice(c.op)}</Badge>{c.confidence !== null && <span>confidence {c.confidence.toFixed(2)}</span>}{c.status !== "pending" && <span>{c.status} by {c.decided_by}</span>}
              {c.doc_id && <Link className="text-blue underline" href={`/a/${c.doc_id}${c.anchor ? `#${c.anchor}` : ""}`}>{c.doc_id}</Link>}</p>
            <p className="mt-2 break-words font-semibold">{c.op === "add_edge" ? `${str(r.src)} → ${str(r.type)} → ${str(r.dst)}` : `${str(r.id)}${r.label ? ` (${str(r.label)})` : ""}`}</p>
            {props && <ul className="mt-1 font-mono text-xs">{Object.entries(props).map(([k, v]) => <li key={k} className="break-words text-ok">+ {k}: {str(v)}</li>)}</ul>}
            {c.status === "pending" && <p className="mt-3 flex gap-2"><button type="button" disabled={busy} className={primary} onClick={() => void decide(c, "approve")}>Approve</button><button type="button" disabled={busy} className={secondary} onClick={() => void decide(c, "reject")}>Reject</button></p>}
          </li>
        );
      })}</ul></section>
  );
}
