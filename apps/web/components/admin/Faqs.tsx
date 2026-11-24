"use client";

import { useState, type FormEvent } from "react";

import { Badge } from "@/components/console/Badge";
import type { Faq } from "@/lib/admin";
import { nice } from "@/lib/console";
import { staffApi, usePoll } from "@/lib/staffClient";

import { field, primary, secondary, Status, Table } from "./ui";

function Edit({ f, onDone }: { f: Faq; onDone: (note?: string) => void }) {
  const [error, setError] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const d = new FormData(e.currentTarget);
    try { await staffApi(`admin/faqs/${f.id}`, "PUT", { question: d.get("question"), answer: d.get("answer"), status: d.get("status") }); onDone(`${f.id} saved`); } catch (x) { setError((x as Error).message); }
  }
  return (
    <form onSubmit={(e) => void submit(e)} aria-label={`Edit ${f.id}`} className="space-y-2">
      <label className="block font-semibold">Question<input name="question" required minLength={5} maxLength={300} defaultValue={f.question} className={`${field} mt-1 w-full`} /></label>
      <label className="block font-semibold">Answer<textarea name="answer" required minLength={5} maxLength={4000} rows={4} defaultValue={f.answer} className={`${field} mt-1 w-full`} /></label>
      <label className="block font-semibold">Status <select name="status" defaultValue={f.status} className={field}>{["active", "needs_review", "withdrawn"].map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
      <span className="flex gap-2"><button type="submit" className={primary}>Save</button><button type="button" className={secondary} onClick={() => onDone()}>Cancel</button></span>
      {error && <p role="alert" className="text-bad">{error}</p>}
    </form>
  );
}

export function Faqs() {
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const [edit, setEdit] = useState<string | null>(null);
  const [note, setNote] = useState("");
  const { data, error, reload } = usePoll<{ items: Faq[] }>(`admin/faqs?status=${status}&q=${encodeURIComponent(q)}`, 15_000);
  return (
    <section aria-labelledby="h-f"><h1 id="h-f" className="text-2xl">FAQ cache</h1>
      <div className="mt-2 flex flex-wrap gap-4 text-sm"><label>Status <select className={field} value={status} onChange={(e) => setStatus(e.target.value)}><option value="">all</option>{["active", "needs_review", "withdrawn"].map((s) => <option key={s} value={s}>{nice(s)}</option>)}</select></label>
        <label>Search <input type="search" maxLength={100} className={field} value={q} onChange={(e) => setQ(e.target.value)} /></label></div>
      <Status error={error} note={note} loading={!data} />
      <Table label="FAQ entries" heads={["Entry", "Question and answer", "Source", "Status", "Variants", "Hits", "Actions"]}>
        {data?.items.map((f) => (
          <tr key={f.id}><th scope="row" className="whitespace-nowrap">{f.id}<span className="block text-xs font-normal text-steel">{f.origin}</span></th>
            <td>{edit === f.id ? <Edit f={f} onDone={(n) => { setEdit(null); if (n) setNote(n); void reload(); }} /> : <><b>{f.question}</b><span className="block text-steel">{f.answer}</span></>}</td>
            <td>{f.source_doc}</td><td><Badge tone={f.status === "active" ? "ok" : f.status === "withdrawn" ? "bad" : "warn"}>{nice(f.status)}</Badge></td><td>{f.variants}</td><td>{f.hits}</td>
            <td>{edit !== f.id && <button type="button" className={secondary} aria-label={`Edit ${f.id}`} onClick={() => setEdit(f.id)}>Edit</button>}</td></tr>))}
      </Table></section>
  );
}
