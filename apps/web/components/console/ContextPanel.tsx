import { nice, type Detail } from "@/lib/console";

import { Badge, priorityTone } from "./Badge";

export function ContextPanel({ d }: { d: Detail }) {
  if (!d.customer) return <p className="p-4 text-sm text-steel">The sender isn&apos;t matched to a customer. Don&apos;t share account details before checking who they are.{d.tickets.length > 0 && ` Ticket raised here: ${d.tickets.map((t) => t.id).join(", ")}`}</p>;
  const c = d.customer;
  return (
    <div className="space-y-5 p-4 text-sm">
      <section><h2 className="text-base">{c.company}</h2><p className="text-steel">{c.id} · {c.industry} · {c.city} · {c.service_centre}</p>
        <ul className="mt-2 space-y-1">{c.contacts.map((p) => <li key={p.email} className="break-words">{p.name}<br /><span className="text-steel">{p.email} · {p.phone}</span></li>)}</ul></section>
      <section><h2 className="text-base">Machines ({d.assets.length})</h2><ul className="mt-1 space-y-2">{d.assets.map((a) => <li key={a.serial_no}><b>{a.model}</b> {a.serial_no}<br />
        <Badge tone={a.in_warranty ? "ok" : "plain"}>{a.in_warranty ? `warranty to ${a.warranty_end}` : "out of warranty"}</Badge> <Badge tone={a.in_amc ? "ok" : "plain"}>{a.in_amc ? "AMC" : "no AMC"}</Badge></li>)}</ul></section>
      <section><h2 className="text-base">AMC contracts ({d.contracts.length})</h2><ul className="mt-1 space-y-1">{d.contracts.map((k) => <li key={k.id}>{k.id} {k.plan} <Badge tone={k.active ? "ok" : "bad"}>{k.active ? `to ${k.end_date}` : `ended ${k.end_date}`}</Badge></li>)}</ul></section>
      <section><h2 className="text-base">Open tickets ({d.tickets.length})</h2><ul className="mt-1 space-y-2">{d.tickets.map((t) => <li key={t.id}><b>{t.id}</b> <Badge tone={priorityTone(t.priority)}>{t.priority}</Badge> {nice(t.status)}{t.here && " · raised here"}<br /><span className="text-steel">{t.summary}</span></li>)}</ul></section>
    </div>
  );
}
