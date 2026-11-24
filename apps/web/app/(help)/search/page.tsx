import Link from "next/link";

import { SearchBox } from "@/components/help/SearchBox";
import { kb, type Hit } from "@/lib/kb";

export const metadata = { title: "Search" };
const KIND = { error_code: "Error code", article: "Article", faq: "FAQ" };

export default async function SearchPage({ searchParams }: { searchParams: Promise<{ q?: string | string[] }> }) {
  const raw = (await searchParams).q;
  const q = (typeof raw === "string" ? raw : "").trim().slice(0, 200);
  const items = q ? (await kb<{ items: Hit[] }>(`/search?q=${encodeURIComponent(q)}`))?.items ?? [] : [];
  return (
    <div className="mx-auto max-w-4xl px-4 py-8">
      <h1 className="mb-4 text-3xl">Search</h1>
      <SearchBox key={q} initial={q} />
      {q && <p className="mt-4 text-steel">{items.length ? `${items.length} results for “${q}”` : `Nothing found for “${q}”.`}{!items.length && <> Try an error code, or <Link className="text-blue underline" href="/contact">email us</Link>.</>}</p>}
      <ol className="mt-4 space-y-3">
        {items.map((h, i) => (
          <li key={i} className="rounded-card border border-line bg-surface p-4">
            <span className="text-xs font-semibold text-steel">{KIND[h.kind]}</span>
            <h2 className="text-lg">{h.url ? <Link className="text-blue hover:underline" href={h.url}>{h.title}</Link> : h.title}</h2>
            <p className="text-sm">{h.snippet}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
