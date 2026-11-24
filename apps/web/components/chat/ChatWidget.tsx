"use client";

import { useEffect, useRef, useState, type FormEvent } from "react";

import { MessageList } from "./MessageList";
import { useChat } from "./useChat";

export const OPEN_EVENT = "mcs:open-chat";
const FLAG = "mcs_chat_open";

export function ChatWidget({ persona }: { persona: string }) {
  const [open, setOpen] = useState(false);
  const chat = useChat(persona, open);
  const input = useRef<HTMLInputElement>(null);
  const launcher = useRef<HTMLButtonElement>(null);
  const log = useRef<HTMLDivElement>(null);
  const focusBack = useRef(false);

  function toggle(v: boolean) {
    setOpen(v);
    focusBack.current = !v;
    try { sessionStorage.setItem(FLAG, v ? "1" : ""); } catch { /* private mode */ }
  }
  useEffect(() => {                                  // stays open across pages (a citation opens an article)
    try { if (sessionStorage.getItem(FLAG)) setOpen(true); } catch { /* private mode */ }
    const f = () => toggle(true);
    window.addEventListener(OPEN_EVENT, f);
    return () => window.removeEventListener(OPEN_EVENT, f);
  }, []);
  useEffect(() => {
    if (open) input.current?.focus({ preventScroll: true });
    else if (focusBack.current) launcher.current?.focus();
  }, [open]);
  useEffect(() => { log.current?.scrollTo({ top: log.current.scrollHeight }); }, [chat.msgs, chat.status, open]);

  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const text = input.current?.value.trim() ?? "";
    if (!text || chat.busy) return;
    input.current!.value = "";
    void chat.send(text);
  }

  if (!open) return (
    <button ref={launcher} type="button" onClick={() => toggle(true)} className="fixed bottom-4 right-4 z-20 min-h-14 rounded-full bg-blue px-6 font-semibold text-surface shadow-lg hover:bg-blue-600">Chat with us</button>
  );
  return (
    <section role="dialog" aria-label="Matsui Assistant" onKeyDown={(e) => { if (e.key === "Escape") toggle(false); }}
      className="fixed bottom-2 right-2 z-20 flex h-[min(600px,calc(100dvh-1rem))] w-[min(380px,calc(100vw-1rem))] flex-col overflow-hidden rounded-card border border-field bg-surface shadow-xl">
      <header className="on-navy flex items-center gap-2 bg-navy px-4 py-3 text-surface">
        <div className="min-w-0 flex-1"><h2 className="text-base">Matsui Assistant</h2><p className="text-xs">usually replies instantly · demo data</p></div>
        <button type="button" onClick={chat.reset} disabled={chat.busy} className="min-h-8 rounded border border-surface px-2 text-xs hover:bg-blue-600">New chat</button>
        <button type="button" aria-label="Close chat" onClick={() => toggle(false)} className="min-h-8 min-w-8 rounded text-xl hover:bg-blue-600">×</button>
      </header>
      {chat.human && (
        <p role="status" className="border-b border-line bg-sky px-4 py-2 text-sm font-semibold">
          {chat.human.status === "with_agent" ? `You're now chatting with ${chat.human.message && !chat.human.message.endsWith(".") ? chat.human.message : "Matsui service team"}`
            : `${chat.human.message}${chat.human.position ? ` You are number ${chat.human.position} in the queue.` : ""}`}
        </p>
      )}
      <div ref={log} role="log" aria-live="polite" aria-label="Conversation" tabIndex={0} className="flex-1 space-y-4 overflow-y-auto p-4">
        {!chat.msgs.length && <p className="text-sm text-steel">Hello! Ask about an error code, warranty, spare parts or a service ticket.</p>}
        <MessageList msgs={chat.msgs} busy={chat.busy} formErrors={chat.formErrors} onQuick={(p, l) => void chat.send(p, l)} onForm={(id, f) => void chat.submit(id, f)} onRate={(m, r) => void chat.rate(m, r)} />
        {chat.busy && <p role="status" className="flex items-center gap-2 text-sm text-steel"><span aria-hidden className="size-4 animate-spin rounded-full border-2 border-line border-t-blue" />{chat.status || "Sending…"}</p>}
      </div>
      <form onSubmit={submit} className="flex gap-2 border-t border-line p-3">
        <label htmlFor="chat-input" className="sr-only">Your message</label>
        <input id="chat-input" ref={input} maxLength={4000} autoComplete="off" placeholder="Type your message" className="min-w-0 flex-1 rounded-card border border-field px-3 py-2 text-sm placeholder:text-steel" />
        <button type="submit" disabled={chat.busy} className="rounded-card bg-blue px-4 font-semibold text-surface hover:bg-blue-600">Send</button>
      </form>
      <p className="px-3 pb-2 text-center text-xs text-steel">AI-generated, may be reviewed</p>
    </section>
  );
}
