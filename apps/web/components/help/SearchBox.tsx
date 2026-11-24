"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import type { Hit } from "@/lib/kb";

/** Plain GET form (works without JavaScript) with suggestions as a list of links below. */
export function SearchBox({ initial = "", large = false }: { initial?: string; large?: boolean }) {
  const [q, setQ] = useState(initial);
  const [hits, setHits] = useState<Hit[]>([]);

  useEffect(() => {
    if (q.trim().length < 2 || q === initial) { setHits([]); return; }
    const ctl = new AbortController();
    const t = setTimeout(() => {
      fetch(`/api/suggest?q=${encodeURIComponent(q)}`, { signal: ctl.signal }).then((r) => r.json()).then((d) => setHits(d.items ?? [])).catch(() => {});
    }, 200);
    return () => { clearTimeout(t); ctl.abort(); };
  }, [q, initial]);

  return (
    <form action="/search" role="search" className="relative max-w-2xl text-navy">
      <label htmlFor="q" className="sr-only">Search articles and error codes</label>
      <div className="flex gap-2">
        <input id="q" name="q" type="search" value={q} maxLength={200} autoComplete="off" onChange={(e) => setQ(e.target.value)}
          placeholder="Search articles and error codes" className={`min-w-0 flex-1 rounded-card border border-field bg-surface px-4 placeholder:text-steel ${large ? "min-h-14 text-lg" : "min-h-11"}`} />
        <button type="submit" className="rounded-card bg-blue px-5 font-semibold text-surface hover:bg-blue-600">Search</button>
      </div>
      <p role="status" className="sr-only">{hits.length ? `${hits.length} suggestions` : ""}</p>
      {hits.length > 0 && (
        <ul aria-label="Suggestions" className="absolute z-10 mt-1 w-full overflow-hidden rounded-card border border-line bg-surface shadow-lg">
          {hits.map((h, i) => h.url && (
            <li key={i}><Link href={h.url} className="block px-4 py-2 hover:bg-sky" onClick={() => setHits([])}>{h.title}</Link></li>
          ))}
        </ul>
      )}
    </form>
  );
}
