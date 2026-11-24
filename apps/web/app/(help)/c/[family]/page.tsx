import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { DocCard } from "@/components/help/DocCard";
import { FAMILIES } from "@/lib/families";
import { kb, type FamilyInfo } from "@/lib/kb";

type Props = { params: Promise<{ family: string }> };
const find = async (p: Props["params"]) => { const { family } = await p; return FAMILIES.find((f) => f.id === family.toUpperCase()); };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  return { title: (await find(params))?.name ?? "Not found" };
}

export default async function FamilyPage({ params }: Props) {
  const f = await find(params);
  const info = f && (await kb<FamilyInfo>(`/families/${f.id}`));
  if (!f || !info) notFound();
  return (
    <div className="mx-auto max-w-7xl space-y-10 px-4 py-8">
      <header><nav aria-label="Breadcrumb" className="text-sm"><Link href="/" className="text-blue underline">Help centre</Link> / {f.name}</nav>
        <h1 className="mt-2 text-3xl">{f.name}</h1><p className="text-steel">{f.summary}</p>
        <p className="mt-2 text-sm">Models: {info.models.map((m) => m.id).join(", ")}</p></header>
      <section id="errors"><h2 className="mb-3 text-2xl">Troubleshooting</h2>
        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {info.error_codes.map((e) => {
            const body = <><span className="block font-semibold text-blue">{e.code} — {e.title}</span><span className="text-sm text-steel">Display: {e.display_text} · <span className={e.severity === "critical" ? "font-semibold text-bad" : ""}>{e.severity}</span></span></>;
            return <li key={e.id}>{e.doc_id ? <Link className="block rounded-card border border-line bg-surface p-4 hover:border-blue" href={`/a/${e.doc_id}#${e.anchor}`}>{body}</Link> : <div className="rounded-card border border-line bg-surface p-4">{body}</div>}</li>;
          })}
        </ul></section>
      <section id="docs"><h2 className="mb-3 text-2xl">Manuals and policies</h2><ul className="grid gap-3 md:grid-cols-2">{info.documents.map((d) => <li key={d.doc_id}><DocCard doc={d} /></li>)}</ul></section>
      <section id="parts"><h2 className="mb-3 text-2xl">Spare parts</h2>
        <div className="overflow-x-auto prose-mt" tabIndex={0} role="region" aria-label="Spare parts"><table><thead><tr><th scope="col">Part number</th><th scope="col">Name</th><th scope="col">Fits</th></tr></thead>
          <tbody>{info.parts.map((p) => <tr key={p.part_no}><td>{p.part_no}</td><td>{p.name}</td><td>{p.fits_models.join(", ")}</td></tr>)}</tbody></table></div></section>
    </div>
  );
}
