"use client";

import Link from "next/link";
import { useRef, useState, type DragEvent, type FormEvent } from "react";

import { DOC_TYPES, type Doc, type Job } from "@/lib/admin";
import { FAMILIES } from "@/lib/families";
import { staffApi, usePoll } from "@/lib/staffClient";

import { JobPill } from "./Jobs";
import { field, primary } from "./ui";

export function Upload() {
  const [file, setFile] = useState<File | null>(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState<{ doc_id: string; version: string; job_id: string } | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const { data: docs } = usePoll<{ items: Doc[] }>("admin/documents", 60_000);
  const { data: jobs } = usePoll<{ items: Job[] }>(done ? "admin/jobs" : null, 1500);
  const job = jobs?.items.find((j) => j.id === done?.job_id);

  function drop(e: DragEvent) { e.preventDefault(); setOver(false); if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]); }
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (!file) { setError("Choose a file first."); return; }
    setBusy(true); setError(""); setDone(null);
    const data = new FormData(e.currentTarget);
    data.set("file", file);
    try { setDone(await staffApi("admin/documents", "POST", data)); setFile(null); if (input.current) input.current.value = ""; } catch (x) { setError((x as Error).message); }
    setBusy(false);
  }
  return (
    <section aria-labelledby="h-up" className="max-w-3xl">
      <h1 id="h-up" className="mb-3 text-2xl">Upload a document</h1>
      <form onSubmit={(e) => void submit(e)} className="space-y-4 rounded-card border border-line bg-surface p-6">
        <div onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)} onDrop={drop} className={`rounded-card border-2 border-dashed p-6 text-center ${over ? "border-blue bg-sky" : "border-field"}`}>
          <label htmlFor="file" className="block font-semibold">Drop a file here, or choose one</label>
          <input id="file" ref={input} type="file" accept=".md,.markdown,.txt,.pdf,.docx" className="mt-2 max-w-full text-sm" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
          <p className="mt-2 text-sm text-steel">{file ? `${file.name} (${Math.ceil(file.size / 1024)} KB)` : "MD, TXT, PDF or DOCX, up to 10 MB. The same file name becomes a new version."}</p>
        </div>
        <label className="block font-semibold">Title<input name="title" required minLength={3} maxLength={200} className={`${field} mt-1 w-full`} /></label>
        <div className="grid gap-4 sm:grid-cols-3">
          <label className="block font-semibold">Type<select name="doc_type" className={`${field} mt-1 w-full`} defaultValue="bulletin">{DOC_TYPES.map((t) => <option key={t}>{t}</option>)}</select></label>
          <label className="block font-semibold">Product family<select name="family" className={`${field} mt-1 w-full`}><option value="">All products</option>{FAMILIES.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select></label>
          <label className="block font-semibold">Replaces<select name="supersedes" className={`${field} mt-1 w-full`}><option value="">Nothing</option>{docs?.items.filter((d) => d.status === "active").map((d) => <option key={d.doc_id} value={d.doc_id}>{d.title}</option>)}</select></label>
        </div>
        <button type="submit" disabled={busy} className={primary}>{busy ? "Uploading…" : "Upload"}</button>
        {error && <p role="alert" className="text-bad">{error}</p>}
      </form>
      <div role="status" className="mt-4">{done && (
        <p className="rounded-card border border-line bg-surface p-4">{done.doc_id} version {done.version}: <JobPill status={job?.status ?? "queued"} />{job?.status === "done" && <> {String(job.result.chunks)} sections are searchable now. <Link className="text-blue underline" href={`/a/${done.doc_id}`}>Open the article</Link></>}{job?.error && <span className="block text-bad">{job.error}</span>}</p>)}</div>
      <p className="mt-6 text-sm text-steel">CSV uploads (error codes, spare parts, FAQs) and automatic graph extraction arrive in BS-28.</p>
    </section>
  );
}
