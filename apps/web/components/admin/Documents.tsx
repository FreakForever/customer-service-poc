"use client";

import Link from "next/link";
import { useState } from "react";

import { Badge } from "@/components/console/Badge";
import type { Doc } from "@/lib/admin";
import { staffApi, usePoll } from "@/lib/staffClient";

import { secondary, Status, Table } from "./ui";

export function Documents() {
  const { data, error, reload } = usePoll<{ items: Doc[] }>("admin/documents", 5000);
  const [ask, setAsk] = useState<string | null>(null);
  const [note, setNote] = useState("");
  async function act(id: string, what: "reindex" | "delete") {
    setAsk(null);
    try {
      await (what === "delete" ? staffApi(`admin/documents/${encodeURIComponent(id)}`, "DELETE") : staffApi(`admin/documents/${encodeURIComponent(id)}/reindex`, "POST"));
      setNote(what === "delete" ? `${id} deleted` : `${id} queued for re-indexing`);
    } catch (e) { setNote((e as Error).message); }
    await reload();
  }
  const firstLive = new Set<string>();
  return (
    <section aria-labelledby="h-d"><h1 id="h-d" className="text-2xl">Documents</h1><Status error={error} note={note} loading={!data} />
      <Table label="Documents" heads={["Document", "Version", "Type", "Status", "In force", "Sections", "Source", "Actions"]}>
        {data?.items.map((d) => {
          const own = d.status !== "deleted" && !firstLive.has(d.doc_id);
          if (own) firstLive.add(d.doc_id);
          return (
            <tr key={d.doc_id + d.version}><th scope="row">{d.status === "deleted" ? d.title : <Link className="text-blue underline" href={`/a/${d.doc_id}?version=${d.version}`}>{d.title}</Link>}<span className="block text-xs font-normal text-steel">{d.doc_id}</span></th>
              <td>{d.version}</td><td>{d.doc_type}{d.family && ` · ${d.family}`}</td><td><Badge tone={d.status === "active" ? "ok" : d.status === "deleted" ? "bad" : "warn"}>{d.status}</Badge></td>
              <td className="whitespace-nowrap">{d.effective_from}{d.effective_to && ` to ${d.effective_to}`}</td><td>{d.chunks}</td><td>{d.source}</td>
              <td>{own && (ask === d.doc_id
                ? <span className="flex flex-wrap items-center gap-2">Delete all versions? <button type="button" className={secondary} onClick={() => void act(d.doc_id, "delete")}>Yes, delete</button><button type="button" className={secondary} onClick={() => setAsk(null)}>Keep</button></span>
                : <span className="flex gap-2">{d.status === "active" && <button type="button" className={secondary} aria-label={`Re-index ${d.doc_id}`} onClick={() => void act(d.doc_id, "reindex")}>Re-index</button>}<button type="button" className={secondary} aria-label={`Delete ${d.doc_id}`} onClick={() => setAsk(d.doc_id)}>Delete</button></span>)}</td></tr>
          );
        })}
      </Table></section>
  );
}
