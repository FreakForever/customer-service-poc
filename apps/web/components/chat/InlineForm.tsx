"use client";

import { useId, useState, type FormEvent } from "react";

import type { ChatForm } from "@/lib/chat";

const LABELS: Record<string, [string, string]> = {
  serial_no: ["Serial number", "Serial numbers look like MT-GR200-24-00233."], description: ["What is the problem?", "Please describe the problem."],
  contact: ["Phone or email", "Enter a phone number or an email address."],
};

export function InlineForm({ form, errors, busy, onSubmit }: { form: ChatForm; errors: Record<string, string>; busy: boolean; onSubmit: (f: Record<string, string>) => void }) {
  const id = useId();
  const [local, setLocal] = useState<Record<string, string>>({});
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const data = new FormData(e.currentTarget);
    const v = Object.fromEntries(form.fields.map((f) => [f.name, String(data.get(f.name) ?? "").trim()]));
    if (v.serial_no) v.serial_no = v.serial_no.toUpperCase();
    const bad = Object.fromEntries(form.fields.filter((f) => (f.required && !v[f.name]) || (f.pattern && v[f.name] && !new RegExp(f.pattern).test(v[f.name])))
      .map((f) => [f.name, LABELS[f.name]?.[1] ?? "Please check this field."]));
    setLocal(bad);
    if (!Object.keys(bad).length) onSubmit(v);
  }
  return (
    <form onSubmit={submit} noValidate aria-label="Service request" className="mt-2 space-y-3 rounded-card border border-line bg-surface p-3">
      {form.fields.map((f) => {
        const err = local[f.name] ?? errors[f.name];
        return (
          <div key={f.name}><label className="text-sm font-semibold" htmlFor={id + f.name}>{LABELS[f.name]?.[0] ?? f.name}</label>
            <input id={id + f.name} name={f.name} defaultValue={f.value ?? ""} aria-invalid={Boolean(err)} aria-describedby={err ? `${id}${f.name}e` : undefined} maxLength={2000}
              className="mt-1 w-full rounded border border-field bg-surface px-2 py-1.5" />
            {err && <p id={`${id}${f.name}e`} role="alert" className="text-sm text-bad">{err}</p>}</div>
        );
      })}
      <button type="submit" disabled={busy} className="min-h-9 w-full rounded bg-blue px-4 font-semibold text-surface hover:bg-blue-600">Send request</button>
    </form>
  );
}
