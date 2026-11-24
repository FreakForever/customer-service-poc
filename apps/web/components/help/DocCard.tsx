import Link from "next/link";

import { familyName } from "@/lib/families";
import type { Doc } from "@/lib/kb";

export function VersionBadge({ version, status = "active" }: { version: string; status?: string }) {
  const old = status !== "active";
  return <span className={`inline-block whitespace-nowrap rounded-full border px-2 py-0.5 text-xs font-semibold ${old ? "border-bad text-bad" : "border-ok text-ok"}`}>v{version}{old && " superseded"}</span>;
}

export function DocCard({ doc }: { doc: Doc }) {
  return (
    <Link href={`/a/${encodeURIComponent(doc.doc_id)}`} className="flex items-start justify-between gap-3 rounded-card border border-line bg-surface p-4 hover:border-blue">
      <span className="min-w-0"><span className="block font-semibold text-blue">{doc.title}</span>
        <span className="text-sm text-steel">{familyName(doc.family) ?? "All products"} · {doc.doc_type}</span></span>
      <VersionBadge version={doc.version} />
    </Link>
  );
}
