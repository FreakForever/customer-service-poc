"use client";

import { useEffect, useRef, useState } from "react";

import type { Detail } from "@/lib/console";
import { staffApi } from "@/lib/staffClient";

type Props = { detail: Detail; me: string; start: { text: string; draftId: string | null }; onSent: () => Promise<void> };

/** Starts from the AI draft (editable). A newer draft never overwrites what the agent typed. */
export function ReplyEditor({ detail, me, start, onSent }: Props) {
  const c = detail.conversation;
  const [text, setText] = useState("");
  const [draftId, setDraftId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const touched = useRef(false);

  useEffect(() => { if (!touched.current) { setText(start.text); setDraftId(start.draftId); } }, [start.text, start.draftId]);

  const mine = c.status === "human" && c.assigned_to === me;
  const blocked = c.status === "closed" ? "This conversation is closed." : c.status === "human" && !mine ? `${c.agent_name ?? "Another agent"} is handling this conversation.`
    : c.channel === "chat" && !mine ? "Take over the conversation to reply." : "";
  const stale = draftId !== null && !detail.messages.some((m) => m.id === draftId && m.delivery === "draft");
  const edited = draftId !== null && text.trim() !== (detail.messages.find((m) => m.id === draftId)?.content.trim() ?? "");

  async function send() {
    setBusy(true); setNote("");
    try {
      await staffApi(`console/conversations/${c.id}/messages`, "POST", { text, ...(draftId && !stale ? { approve_draft_id: draftId } : {}) });
      touched.current = false;
      setText(""); setDraftId(null); setNote("Sent");
      await onSent();
    } catch (e) { setNote((e as Error).message); }
    setBusy(false);
  }
  return (
    <div className="border-t border-line bg-surface p-3">
      <label htmlFor="reply" className="flex flex-wrap items-center gap-2 text-sm font-semibold">Reply{draftId && !stale && <span className="rounded-full border border-blue px-2 text-xs text-blue">AI draft{edited ? ", edited" : ""}</span>}{!draftId && start.text && text === start.text && <span className="rounded-full border border-warn px-2 text-xs text-warn">Held back by checks: review carefully</span>}</label>
      <textarea id="reply" value={text} disabled={Boolean(blocked)} rows={c.channel === "email" ? 7 : 3} maxLength={20000} onChange={(e) => { touched.current = true; setText(e.target.value); }}
        className="mt-1 w-full rounded border border-field p-2 text-sm disabled:bg-bg" />
      <div className="mt-2 flex flex-wrap items-center gap-3">
        <button type="button" disabled={busy || Boolean(blocked) || !text.trim() || stale} onClick={() => void send()} className="min-h-9 rounded bg-blue px-4 font-semibold text-surface hover:bg-blue-600 disabled:bg-steel">
          {draftId && !stale ? (edited ? "Send edited reply" : "Approve and send") : "Send"}</button>
        {touched.current && <button type="button" className="min-h-9 rounded border border-field px-3 text-sm text-blue" onClick={() => { touched.current = false; setText(start.text); setDraftId(start.draftId); }}>Discard changes</button>}
        <span role="status" className="text-sm text-steel">{stale ? "This draft was already sent." : note || blocked}</span>
      </div>
    </div>
  );
}
