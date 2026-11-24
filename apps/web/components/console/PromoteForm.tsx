"use client";

import { useState, type FormEvent } from "react";

import { staffApi } from "@/lib/staffClient";

export function PromoteForm({ messageId, question, answer, source, onClose }: { messageId: string; question: string; answer: string; source: string; onClose: () => void }) {
  const [note, setNote] = useState("");
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    try {
      const r = await staffApi<{ faq_id: string }>(`console/messages/${messageId}/promote-faq`, "POST", { question: f.get("question"), answer: f.get("answer"), source_doc: f.get("source_doc") });
      setNote(`Saved as ${r.faq_id}`);
    } catch (x) { setNote((x as Error).message); }
  }
  const cls = "mt-1 w-full rounded border border-field p-2 text-sm";
  return (
    <form onSubmit={(e) => void submit(e)} aria-label="Promote to FAQ" className="mt-2 space-y-2 rounded-card border border-blue bg-surface p-3 text-sm">
      <p className="text-steel">Everyone will see this answer. Remove anything about a particular customer.</p>
      <label className="block font-semibold">Question<input name="question" required minLength={5} maxLength={300} defaultValue={question} className={cls} /></label>
      <label className="block font-semibold">Answer<textarea name="answer" required minLength={5} maxLength={4000} rows={4} defaultValue={answer.replace(/\s*\[E\d+\]/g, "")} className={cls} /></label>
      <label className="block font-semibold">Source document<input name="source_doc" required defaultValue={source} className={cls} /></label>
      <div className="flex items-center gap-2"><button type="submit" className="min-h-8 rounded bg-blue px-3 font-semibold text-surface">Save to FAQ</button>
        <button type="button" onClick={onClose} className="min-h-8 rounded border border-field px-3 text-blue">Close</button><span role="status">{note}</span></div>
    </form>
  );
}
