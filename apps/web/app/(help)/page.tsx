import Link from "next/link";

import { ChatButton } from "@/components/chat/ChatButton";
import { DocCard } from "@/components/help/DocCard";
import { SearchBox } from "@/components/help/SearchBox";
import { CENTRES, FAMILIES } from "@/lib/families";
import { kb, type Home } from "@/lib/kb";

const chips = [["Error codes", "/search?q=error+code"], ["Warranty", "/search?q=warranty"], ["AMC", "/search?q=AMC"], ["Spare parts", "/search?q=spare+parts"]];

export default async function HomePage() {
  const home = await kb<Home>("/home").catch(() => null);
  const news = home?.bulletins.length ? home.bulletins : home?.recent ?? [];
  return (
    <>
      <section className="on-navy bg-navy text-surface">
        <div className="mx-auto max-w-7xl px-4 py-14">
          <h1 className="mb-6 text-3xl md:text-4xl">How can we help your plant today?</h1>
          <SearchBox large />
          <ul className="mt-4 flex flex-wrap gap-2" aria-label="Common topics">
            {chips.map(([label, href]) => <li key={label}><Link href={href} className="inline-flex min-h-8 items-center rounded-full border border-surface px-3 text-sm hover:bg-blue-600">{label}</Link></li>)}
          </ul>
        </div>
      </section>
      <div className="border-b border-line bg-sky"><ul className="mx-auto flex max-w-7xl flex-wrap gap-x-8 gap-y-1 px-4 py-3 text-sm">
        <li>Total Solution Provider in Auxiliary Equipment for Plastic Processing</li><li>{CENTRES.length} service centres</li><li>24x7 breakdown line</li>
      </ul></div>
      <div className="mx-auto max-w-7xl space-y-12 px-4 py-10">
        <section id="products" aria-labelledby="h-products">
          <h2 id="h-products" className="mb-4 text-2xl">Products</h2>
          <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            {FAMILIES.map((f) => (
              <li key={f.id} className="rounded-card border border-line bg-surface p-5">
                <span aria-hidden className="mb-3 inline-flex rounded bg-sky px-2 py-1 text-sm font-semibold text-blue">{f.id}</span>
                <h3 className="text-lg"><Link href={`/c/${f.id}`} className="text-blue hover:underline">{f.name}</Link></h3>
                <p className="mt-1 text-sm text-steel">{f.summary}</p>
                <p className="mt-3 flex flex-wrap gap-x-3 text-sm text-blue">
                  {[["Troubleshooting", "errors"], ["Manuals", "docs"], ["Parts", "parts"]].map(([t, h]) => <Link key={h} href={`/c/${f.id}#${h}`} className="underline" aria-label={`${t}: ${f.name}`}>{t}</Link>)}
                </p>
              </li>
            ))}
          </ul>
        </section>
        <section id="support" className="grid gap-8 lg:grid-cols-2">
          <div><h2 className="mb-4 text-2xl">Popular articles</h2>
            {home ? <ul className="space-y-3">{home.popular.map((d) => <li key={d.doc_id}><DocCard doc={d} /></li>)}</ul> : <p>Articles can&apos;t be loaded right now. Please try again shortly.</p>}</div>
          <div><h2 className="mb-4 text-2xl">{home?.bulletins.length ? "Service bulletins" : "Recently updated"}</h2>
            <ul className="space-y-3">{news.map((d) => <li key={d.doc_id}><DocCard doc={d} /></li>)}</ul>
            {home && !home.bulletins.length && <p className="mt-3 text-sm text-steel">No service bulletins have been published yet.</p>}</div>
        </section>
        <section id="service-centres" aria-labelledby="h-centres">
          <h2 id="h-centres" className="mb-3 text-2xl">Service centres</h2><p>{CENTRES.join(" · ")}</p>
        </section>
        <section id="contact" aria-labelledby="h-contact" className="rounded-card border border-line bg-surface p-6">
          <h2 id="h-contact" className="mb-4 text-2xl">Contact us</h2>
          <div className="flex flex-wrap items-center gap-4">
            <ChatButton />
            <Link href="/contact" className="inline-flex min-h-11 items-center rounded-card bg-blue px-5 font-semibold text-surface hover:bg-blue-600">Email us</Link>
            <span>Breakdown line (24x7): +91-00000-00000 <span className="text-steel">(placeholder)</span></span>
          </div>
        </section>
      </div>
    </>
  );
}
