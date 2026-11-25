"use client";

import Link from "next/link";
import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { CSV_TYPES, DOC_TYPES, type CsvResult, type Doc, type Job } from "@/lib/admin";
import { FAMILIES } from "@/lib/families";
import { staffApi, usePoll } from "@/lib/staffClient";

import { JobPill } from "./Jobs";
import { field, primary, Table } from "./ui";

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ doc_id: string; version: string; job_id: string } | null>(null);
  const [kind, setKind] = useState("document");
  const [result, setResult] = useState<CsvResult | null>(null);
  const csv = CSV_TYPES.find((t) => t.id === kind);
  const input = useRef<HTMLInputElement>(null);
  const { data: docs } = usePoll<{ items: Doc[] }>("admin/documents", 60_000);
  const { data: jobs } = usePoll<{ items: Job[] }>(done ? "admin/jobs" : null, 1500);
  const job = jobs?.items.find((j) => j.id === done?.job_id);

  function drop(e: DragEvent) { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) { setError("Choose a file first."); return; }
    setBusy(true); setError(""); setDone(null); setResult(null);
    const data = new FormData(e.currentTarget);
    data.set("file", file);
    try {
      if (csv) { const body = new FormData(); body.set("file", file); setResult(await staffApi(`admin/csv?type=${kind}`, "POST", body)); }
      else setDone(await staffApi("admin/documents", "POST", data));
      setFile(null); if (input.current) input.current.value = "";
    } catch (x) { setError((x as Error).message); }
    setBusy(false);
  }
  return (
    <section aria-labelledby="h-up" className="max-w-3xl">
      <h1 id="h-up" className="mb-3 text-2xl">Upload knowledge</h1>
      <label className="mb-3 block font-semibold">What are you uploading?<select className={`${field} mt-1 block`} value={kind} onChange={(e) => { setKind(e.target.value); setError(""); setDone(null); setResult(null); }}><option value="document">Document</option>{CSV_TYPES.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}</select></label>
      <form onSubmit={(e) => void submit(e)} className="space-y-4 rounded-card border border-line bg-surface p-6">
        <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop} className={`rounded-card border-2 border-dashed p-6 text-center ${over ? "border-blue bg-sky" : "border-field"}`}>
          <label htmlFor="file" className="block font-semibold">Drop a file here, or choose one</label>
          <input id="file" ref={input} type="file" accept={csv ? ".csv" : ".md,.markdown,.txt,.pdf,.docx"} className="mt-2 max-w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="mt-2 text-sm text-steel">{file ? `${file.name} (${Math.ceil(file.size / 1024)} KB)` : csv ? "CSV in UTF-8, up to 2 MB" : "MD, TXT, PDF or DOCX, up to 10 MB. The same file name becomes a new version."}</p>
        </div>
        {csv ? <p className="text-sm text-steel">Columns: {csv.columns}. Lists inside a cell are separated by |. Rows with a known id are updated.</p> : <>
        <label className="block font-semibold">Title<input name="title" required minLength={3} maxLength={200} className={`${field} mt-1 w-full`} /></label>
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block font-semibold">Type<select name="doc_type" className={`${field} mt-1 w-full`} defaultValue="bulletin">{DOC_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="block font-semibold">Product family<select name="family" className={`${field} mt-1 w-full`}><option value="">All products</option>{FAMILIES.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
          <label className="block font-semibold">Replaces<select name="supersedes" className={`${field} mt-1 w-full`}><option value="">Nothing</option>{docs?.items.filter((d) => d.status === "active").map((d) => <option key={d.doc_id} value={d.doc_id}>{d.title}</option>)}</select></label>
        </div></>}
        <button type="submit" disabled={busy} className={primary}>{busy ? "Uploading…" : "Upload"}</button>
        {error && <p role="alert" className="text-bad">{error}</p>}
      </form>
      <div role="status" className="mt-4">{done && (
        <p className="rounded-card border border-line bg-surface p-4">{done.doc_id} version {done.version}: <JobPill status={job?.status ?? "queued"} />{job?.status === "done" && <> {String(job.result.chunks)} sections are searchable now. Facts for the knowledge graph follow in a minute: see <Link className="text-blue underline" href="/admin/changes">Graph changes</Link>. <Link className="text-blue underline" href={`/a/${done.doc_id}`}>Open the article</Link></>}{job?.error && <span className="block text-bad">{job.error}</span>}</p>)}</div>
      <div role="status" className="mt-4">{result && <p className="rounded-card border border-line bg-surface p-4">{result.applied} {result.applied === 1 ? "row" : "rows"} applied, {result.rejected.length} rejected.</p>}</div>
      {result && result.rejected.length > 0 && <><h2 className="mt-4 text-lg">Rejected rows</h2><Table label="Rejected rows" heads={["Line", "Reason"]}>{result.rejected.map((r) => <tr key={r.line}><td>{r.line}</td><td className="break-words">{r.error}</td></tr>)}</Table></>}
      {result && result.warnings.length > 0 && <><h2 className="mt-4 text-lg">Please check</h2><Table label="Warnings" heads={["Line", "Note"]}>{result.warnings.map((r, i) => <tr key={i}><td>{r.line}</td><td className="break-words">{r.message}</td></tr>)}</Table></>}
    </section>
  );
}
