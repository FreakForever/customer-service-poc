import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";

import { DocCard, VersionBadge } from "@/components/help/DocCard";
import { Markdown } from "@/components/help/Markdown";
import { familyName } from "@/lib/families";
import { kb, type Article } from "@/lib/kb";
import { layoutSections } from "@/lib/sections";

type Props = { params: Promise<{ docId: string }>; searchParams: Promise<{ version?: string | string[] }> };
const load = cache((id: string, v: string | undefined) => kb<Article>(`/articles/${encodeURIComponent(id)}${v ? `?version=${encodeURIComponent(v)}` : ""}`));
const args = async (p: Props) => {
  const [{ docId }, { version }] = await Promise.all([p.params, p.searchParams]);
  return [decodeURIComponent(docId), typeof version === "string" ? version : undefined] as const;
};

export async function generateMetadata(p: Props): Promise<Metadata> {
  return { title: (await load(...(await args(p))))?.document.title ?? "Not found" };
}

export default async function ArticlePage(p: Props) {
  const a = await load(...(await args(p)));
  if (!a) notFound();
  const d = a.document;
  const secs = layoutSections(a.sections);
  const H = { 2: "h2", 3: "h3", 4: "h4" } as const;
  return (
    <div className="mx-auto grid max-w-7xl gap-8 px-4 py-8 lg:grid-cols-[1fr_18rem]">
      <article className="min-w-0">
        <nav aria-label="Breadcrumb" className="text-sm"><Link href="/" className="text-blue underline">Help centre</Link>{d.family && <> / <Link href={`/c/${d.family}`} className="text-blue underline">{familyName(d.family) ?? d.family}</Link></>}</nav>
        <h1 className="mt-2 text-3xl">{d.title}</h1>
        <p className="mt-2 flex flex-wrap items-center gap-2 text-sm text-steel"><VersionBadge version={d.version} status={d.status} /> Effective from {d.effective_from}</p>
        {d.status !== "active" && <p role="note" className="mt-4 rounded-card border border-bad bg-surface p-4">This version is no longer in force.{" "}
          {a.superseded_by ? <Link className="text-blue underline" href={`/a/${a.superseded_by.doc_id}`}>Read {a.superseded_by.title}</Link> : <Link className="text-blue underline" href={`/a/${d.doc_id}`}>Read the current version</Link>}</p>}
        <div className="mt-6 space-y-5">
          {secs.map((s) => (
            <section key={s.anchor} id={s.anchor} className="cite-target rounded p-1">
              {s.headings.map((h) => { const T = H[h.level as 2 | 3 | 4]; return <T key={h.text} className={h.level === 2 ? "mb-2 mt-4 text-2xl" : "mb-2 mt-2 text-lg"}>{h.text.replace(/[`*]/g, "")}</T>; })}
              <Markdown>{s.content}</Markdown>
            </section>
          ))}
        </div>
      </article>
      <aside className="space-y-6">
        <nav aria-label="On this page"><h2 className="mb-2 text-lg">On this page</h2>
          <ul className="space-y-1 text-sm">{secs.filter((s) => s.headings.length).map((s) => <li key={s.anchor}><a className="inline-block py-0.5 text-blue hover:underline" href={`#${s.anchor}`}>{s.headings.at(-1)!.text.replace(/[`*]/g, "")}</a></li>)}</ul></nav>
        {a.versions.length > 1 && <section><h2 className="mb-2 text-lg">Versions</h2><ul className="text-sm">{a.versions.map((v) => <li key={v.version}><Link className="inline-block py-0.5 text-blue underline" href={`/a/${d.doc_id}?version=${v.version}`}>v{v.version}</Link> ({v.status})</li>)}</ul></section>}
        <section><h2 className="mb-2 text-lg">Related articles</h2><ul className="space-y-2">{a.related.map((r) => <li key={r.doc_id}><DocCard doc={r} /></li>)}</ul></section>
      </aside>
    </div>
  );
}
