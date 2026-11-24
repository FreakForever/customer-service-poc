"use client";

import Link from "next/link";
import { usePathname, useSearchParams } from "next/navigation";
import { useState } from "react";

import { nice, VIEWS, when, type Listing, type View } from "@/lib/console";
import { usePoll } from "@/lib/staffClient";

import { Badge, priorityTone } from "./Badge";

export function ViewList() {
  const params = useSearchParams();
  const path = usePathname();
  const view = (VIEWS.find((v) => v.id === params.get("view"))?.id ?? "review") as View;
  const [q, setQ] = useState("");
  const [limit, setLimit] = useState(30);
  const { data, error } = usePoll<Listing>(`console/conversations?view=${view}&limit=${limit}&q=${encodeURIComponent(q)}`, 5000);
  const link = (on: boolean) => `flex min-h-8 items-center justify-between rounded px-3 text-sm ${on ? "bg-blue font-semibold text-surface" : "hover:bg-sky"}`;
  return (
    <aside className="flex min-h-0 flex-col border-r border-line bg-surface" aria-label="Conversations">
      <nav aria-label="Views" className="space-y-0.5 border-b border-line p-2">
        {VIEWS.map((v) => <Link key={v.id} href={`/console?view=${v.id}`} aria-current={view === v.id && path !== "/console/tickets" ? "page" : undefined} className={link(view === v.id && path !== "/console/tickets")}>
          <span>{v.label}</span><span>{data?.counts[v.id] ?? ""}</span></Link>)}
        <Link href="/console/tickets" aria-current={path === "/console/tickets" ? "page" : undefined} className={link(path === "/console/tickets")}><span>Tickets</span><span>{data?.counts.tickets ?? ""}</span></Link>
      </nav>
      {path !== "/console/tickets" && <>
        <div className="border-b border-line p-2"><label className="sr-only" htmlFor="cq">Search conversations</label>
          <input id="cq" type="search" value={q} maxLength={100} onChange={(e) => setQ(e.target.value)} placeholder="Search" className="w-full rounded border border-field px-2 py-1 text-sm placeholder:text-steel" /></div>
        {error && <p role="alert" className="p-3 text-sm text-bad">{error}</p>}
        <ul className="min-h-0 flex-1 overflow-y-auto" tabIndex={0} aria-label="Conversation list">
          {data?.items.map((c) => {
            const href = `/console/c/${c.id}`;
            return (
              <li key={c.id}><Link href={`${href}?view=${view}`} aria-current={path === href ? "page" : undefined} className={`block border-b border-line p-3 text-sm hover:bg-sky ${path === href ? "bg-sky" : ""}`}>
                <span className="flex items-center gap-1"><span className="min-w-0 flex-1 truncate font-semibold">{c.company ?? c.from_addr ?? "Visitor"}</span>{c.priority && <Badge tone={priorityTone(c.priority)}>{c.priority}</Badge>}</span>
                {c.subject && <span className="block truncate">{c.subject}</span>}
                <span className="block truncate text-steel">{c.last_message}</span>
                <span className="flex justify-between text-xs text-steel"><span>{c.channel} · {nice(c.status)}</span><time>{when(c.updated_at)}</time></span>
              </Link></li>
            );
          })}
          {data && !data.items.length && <li className="p-4 text-sm text-steel">Nothing here.</li>}
          {data?.next && <li className="p-2"><button type="button" onClick={() => setLimit((n) => Math.min(100, n + 30))} className="min-h-8 w-full rounded border border-field text-sm text-blue">Show more</button></li>}
        </ul>
      </>}
    </aside>
  );
}
