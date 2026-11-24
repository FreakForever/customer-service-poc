"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import type { ChatForm, Citation, Msg, Quick } from "@/lib/chat";
import { readSse } from "@/lib/sse";

type Session = { sid: string; token: string };
type Stored = { id: string; role: Msg["role"]; content: string; citations: Citation[] | null };
export type Human = { status: "queued" | "with_agent"; message: string; position: number } | null;

const load = (k: string): Session | null => { try { return JSON.parse(sessionStorage.getItem(k) ?? "null"); } catch { return null; } };
const save = (k: string, s: Session | null) => { try { if (s) sessionStorage.setItem(k, JSON.stringify(s)); else sessionStorage.removeItem(k); } catch { /* private mode */ } };
const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

export function useChat(persona: string, open: boolean) {
  const key = `mcs_chat_${persona || "anon"}`;
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [human, setHuman] = useState<Human>(null);
  const [formErrors, setFormErrors] = useState<Record<string, string>>({});
  const session = useRef<Session | null>(null);
  const seq = useRef(0);
  const sending = useRef(false);

  const api = useCallback((path: string, init: RequestInit = {}) => fetch(`/api/chat/${path}`, { ...init, headers: {
    "content-type": "application/json", ...(session.current ? { authorization: `Bearer ${session.current.token}` } : {}) } }), []);
  const add = useCallback((m: Omit<Msg, "key">) => { const k = `m${++seq.current}`; setMsgs((all) => [...all, { ...m, key: k }]); return k; }, []);
  const patch = useCallback((k: string, f: (m: Msg) => Msg) => setMsgs((all) => all.map((m) => (m.key === k ? f(m) : m))), []);

  /** Add stored messages we don't have yet (agent replies that arrived while disconnected, or everything on resume). */
  const merge = useCallback((stored: Stored[], agentOnly: boolean) => setMsgs((all) => {
    const have = new Set(all.map((m) => m.id));
    const extra = stored.filter((m) => !have.has(m.id) && (!agentOnly || m.role === "agent"))
      .map((m) => ({ key: `m${++seq.current}`, id: m.id, role: m.role, text: m.content, citations: m.citations ?? [], from: m.role === "agent" ? "Matsui service team" : undefined }));
    return extra.length ? [...all, ...extra] : all;
  }), []);

  // resume this persona's conversation
  useEffect(() => {
    if (!open) return;
    let dead = false;
    setMsgs([]); setHuman(null); setStatus("");
    session.current = load(key);
    if (session.current) {
      void api(`sessions/${session.current.sid}`).then(async (r) => {
        if (dead) return;
        if (!r.ok) { if (r.status === 401 || r.status === 404) { session.current = null; save(key, null); } return; }
        const d = (await r.json()) as { messages: Stored[]; status: string };
        if (dead) return;
        merge(d.messages, false);
        if (d.status === "handoff") setHuman({ status: "queued", message: "Connecting you to our service team…", position: 0 });
        if (d.status === "human") setHuman({ status: "with_agent", message: "", position: 0 });
      }).catch(() => {});
    }
    return () => { dead = true; };
  }, [key, open, api, merge]);

  // human replies, while a person is involved
  const listening = human !== null;
  useEffect(() => {
    if (!open || !listening) return;
    const ctl = new AbortController();
    void (async () => {
      while (!ctl.signal.aborted && session.current) {
        try {
          const path = `sessions/${session.current.sid}`;
          const r = await api(`${path}/events`, { signal: ctl.signal });
          if (!r.ok || !r.body) throw new Error();
          const now = await api(path);                         // nothing can be missed between the two
          if (now.ok) merge(((await now.json()) as { messages: Stored[] }).messages, true);
          for await (const e of readSse(r.body)) {
            if (e.event === "agent_message") {
              const d = e.data as { from: string; text: string; message_id: string };
              setHuman({ status: "with_agent", message: d.from, position: 0 });
              setMsgs((all) => (all.some((m) => m.id === d.message_id) ? all : [...all, { key: `m${++seq.current}`, id: d.message_id, role: "agent", text: d.text, from: d.from }]));
            } else if (e.event === "agent_status") {
              const d = e.data as { status: string; from: string | null };
              setHuman(d.status === "human" ? { status: "with_agent", message: d.from ?? "", position: 0 } : d.status === "handoff" ? { status: "queued", message: "Connecting you to our service team…", position: 0 } : null);
            }
          }
        } catch { /* reconnect */ }
        if (!ctl.signal.aborted) await sleep(2000);
      }
    })();
    return () => ctl.abort();
  }, [open, listening, api, merge]);

  const start = useCallback(async () => {
    if (session.current) return;
    const r = await api("sessions", { method: "POST", body: JSON.stringify(persona ? { persona_customer_id: persona } : {}) });
    if (!r.ok) throw new Error("start");
    const d = await r.json();
    session.current = { sid: d.session_id, token: d.token };
    save(key, session.current);
  }, [api, key, persona]);

  const run = useCallback(async (shown: string, path: string, body: unknown): Promise<boolean> => {
    if (sending.current) return false;
    sending.current = true;
    setBusy(true); setFormErrors({});
    let reply = "";
    try {
      await start();
      const r = await api(`sessions/${session.current!.sid}/${path}`, { method: "POST", body: JSON.stringify(body) });
      if (r.status === 422) {
        const errors = (await r.json()).detail?.errors;
        if (errors) { setFormErrors(errors); return false; }
        throw new Error("invalid");
      }
      setMsgs((all) => all.map((m) => ({ ...m, quick: undefined, form: undefined })));
      add({ role: "customer", text: shown });
      if (r.status === 409) { add({ role: "assistant", text: "I'm still answering your previous message. Please try again in a moment.", error: true }); return false; }
      if (r.status === 401 || r.status === 404) { session.current = null; save(key, null); }
      if (!r.ok || !r.body) throw new Error(String(r.status));
      let finished = false;
      for await (const { event, data } of readSse(r.body)) {
        const d = data as Record<string, unknown>;
        if (event === "status") setStatus(String(d.text));
        else if (event === "token") {
          setStatus("");
          if (!reply) reply = add({ role: "assistant", text: String(d.t) });
          else patch(reply, (m) => ({ ...m, text: m.text + String(d.t) }));
        } else if (event === "citations") patch(reply, (m) => ({ ...m, citations: data as Citation[] }));
        else if (event === "actions") patch(reply, (m) => ({ ...m, quick: data as Quick[] }));
        else if (event === "form") patch(reply, (m) => ({ ...m, form: data as ChatForm }));
        else if (event === "handoff") setHuman(d as Human);
        else if (event === "done") { finished = true; if (reply) patch(reply, (m) => ({ ...m, id: d.message_id as string | null, tier: d.tier as string })); }
      }
      if (!finished) throw new Error("cut off");
      return true;
    } catch {
      add({ role: "assistant", text: "Something went wrong and I couldn't finish that. Please try again, or email us.", error: true });
      return false;
    } finally {
      sending.current = false;
      setStatus(""); setBusy(false);
    }
  }, [add, api, key, patch, start]);

  return {
    msgs, status, busy, human, formErrors,
    send: (text: string, label = text) => run(label, "messages", { text }),
    submit: (id: string, fields: Record<string, string>) => run("Service request details sent", `forms/${id}`, { fields }),
    rate: async (m: Msg, rating: 1 | -1) => {
      if (!m.id || !session.current) return;
      const r = await api("feedback", { method: "POST", body: JSON.stringify({ message_id: m.id, rating, session_id: session.current.sid }) }).catch(() => null);
      if (r?.ok) patch(m.key, (x) => ({ ...x, rating }));
    },
    reset: () => { session.current = null; save(key, null); setMsgs([]); setHuman(null); },
  };
}
