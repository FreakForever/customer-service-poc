"use client";

import { useState, type FormEvent } from "react";

import { validate, type Contact, type Errors } from "@/lib/contact";

const FIELDS: { k: keyof Contact; label: string; type?: string; auto?: string; required?: boolean; hint?: string }[] = [
  { k: "name", label: "Name", auto: "name", required: true }, { k: "company", label: "Company", auto: "organization", required: true },
  { k: "email", label: "Email", type: "email", auto: "email", required: true },
  { k: "serial", label: "Machine serial number (optional)", hint: "For example MT-DHD150-23-00417" },
  { k: "subject", label: "Subject", required: true }, { k: "message", label: "Message", required: true },
];

export function ContactForm() {
  const [errors, setErrors] = useState<Errors>({});
  const [state, setState] = useState<"idle" | "busy" | "sent" | "failed" | "limited">("idle");

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = e.currentTarget;
    const c = Object.fromEntries(FIELDS.map((f) => [f.k, String(new FormData(form).get(f.k) ?? "")])) as Contact;
    const found = validate(c);
    setErrors(found);
    const first = FIELDS.find((f) => found[f.k]);
    if (first) { form.querySelector<HTMLElement>(`[name=${first.k}]`)?.focus(); return; }
    setState("busy");
    try {
      const r = await fetch("/api/contact", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(c) });
      if (r.status === 422) setErrors((await r.json()).errors ?? {});
      setState(r.ok ? "sent" : r.status === 429 ? "limited" : r.status === 422 ? "idle" : "failed");
    } catch { setState("failed"); }
  }

  if (state === "sent") return <p role="status" className="rounded-card border border-ok bg-surface p-5">Thank you. Your message has been received and we&apos;ll reply by email.</p>;
  return (
    <form onSubmit={(e) => void submit(e)} noValidate className="space-y-4 rounded-card border border-line bg-surface p-6">
      {FIELDS.map(({ k, label, type, auto, required, hint }) => {
        const p = { id: k, name: k, required, autoComplete: auto, "aria-invalid": Boolean(errors[k]), "aria-describedby": errors[k] ? `${k}-e` : hint ? `${k}-h` : undefined,
          className: "mt-1 w-full rounded border border-field bg-surface px-3 py-2" };
        return (
          <div key={k}><label htmlFor={k} className="font-semibold">{label}</label>
            {k === "message" ? <textarea {...p} rows={6} /> : <input {...p} type={type ?? "text"} />}
            {hint && !errors[k] && <p id={`${k}-h`} className="text-sm text-steel">{hint}</p>}
            {errors[k] && <p id={`${k}-e`} className="text-sm text-bad">{errors[k]}</p>}</div>
        );
      })}
      <button type="submit" disabled={state === "busy"} className="min-h-11 rounded-card bg-blue px-6 font-semibold text-surface hover:bg-blue-600">{state === "busy" ? "Sending…" : "Send"}</button>
      <p role="alert" className="text-bad">{state === "failed" ? "Your message couldn't be sent. Please try again." : state === "limited" ? "Too many messages. Please try again in a minute." : ""}</p>
    </form>
  );
}
