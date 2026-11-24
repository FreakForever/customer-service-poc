"use client";

import { Badge } from "@/components/console/Badge";
import type { Job } from "@/lib/admin";
import { nice, when } from "@/lib/console";
import { usePoll } from "@/lib/staffClient";

import { Status, Table } from "./ui";

export function JobPill({ status }: { status: Job["status"] }) {
  return <Badge tone={status === "done" ? "ok" : status === "failed" ? "bad" : status === "running" ? "warn" : "plain"}>{status}</Badge>;
}

export function Jobs() {
  const { data, error } = usePoll<{ items: Job[] }>("admin/jobs?limit=100", 2000);
  return (
    <section aria-labelledby="h-j"><h1 id="h-j" className="text-2xl">Jobs</h1><Status error={error} loading={!data} />
      <Table label="Jobs" heads={["Job", "Document", "Status", "Attempts", "Result", "Created", "Seconds"]}>
        {data?.items.map((j) => (
          <tr key={j.id}><th scope="row">{nice(j.kind)}</th><td>{j.doc_id}{j.version && ` v${j.version}`}</td><td><JobPill status={j.status} /></td><td>{j.attempts}</td>
            <td className="max-w-80 break-words">{j.error ? <span className="text-bad">{j.error}</span> : Object.entries(j.result).filter(([k]) => !["doc_id", "version"].includes(k)).map(([k, v]) => `${nice(k)}: ${v}`).join(", ")}</td>
            <td className="whitespace-nowrap">{when(j.created_at)}</td><td>{j.started_at && j.finished_at ? ((Date.parse(j.finished_at) - Date.parse(j.started_at)) / 1000).toFixed(1) : ""}</td></tr>))}
      </Table></section>
  );
}
