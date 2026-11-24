"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const ITEMS = [["/admin", "Upload"], ["/admin/jobs", "Jobs"], ["/admin/documents", "Documents"], ["/admin/faqs", "FAQ cache"], ["/admin/changes", "Graph changes"], ["/admin/kg", "Graph explorer"]];

export function AdminNav() {
  const path = usePathname();
  return (
    <nav aria-label="Knowledge admin" className="mb-4 flex flex-wrap gap-2 border-b border-line pb-3">
      {ITEMS.map(([href, label]) => <Link key={href} href={href} aria-current={path === href ? "page" : undefined} className={`inline-flex min-h-8 items-center rounded px-3 text-sm ${path === href ? "bg-blue font-semibold text-surface" : "bg-surface hover:bg-sky"}`}>{label}</Link>)}
    </nav>
  );
}
