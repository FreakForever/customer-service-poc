import Link from "next/link";
import type { ReactNode } from "react";

import type { Staff } from "@/lib/auth";

import { DemoBadge } from "../demo/DemoBadge";
import { SignOutButton } from "./SignOutButton";
import { Wordmark } from "./Wordmark";

type Props = { staff: Staff; section: "console" | "admin" | "analytics"; children: ReactNode; flush?: boolean };

/** Layout of the agent console, knowledge admin and analytics. */
export function StaffShell({ staff, section, children, flush }: Props) {
  const items = [
    { id: "console", href: "/console", label: "Console" },
    { id: "analytics", href: "/analytics", label: "Analytics" },
    ...(staff.role === "admin" ? [{ id: "admin", href: "/admin", label: "Knowledge admin" }] : []),
  ];
  return (
    <div className={`flex flex-col ${flush ? "flush-shell min-h-screen md:min-h-0 md:flex-1" : "min-h-screen"}`}>
      <header className="on-navy bg-navy text-surface">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/console"><Wordmark sub="Service desk" /></Link>
          <nav aria-label="Staff" className="order-last flex w-full flex-wrap md:order-none md:w-auto md:flex-1 gap-2 text-sm">
            {items.map((i) => (
              <Link key={i.id} href={i.href} aria-current={i.id === section ? "page" : undefined}
                className={`inline-flex min-h-8 items-center rounded px-3 ${i.id === section ? "bg-surface font-semibold text-navy" : "hover:bg-blue-600"}`}>
                {i.label}
              </Link>
            ))}
          </nav>
          <DemoBadge />
          <span className="text-sm">{staff.name} ({staff.role})</span>
          <SignOutButton />
        </div>
      </header>
      <main id="main" className={flush ? "flex min-h-0 flex-1 flex-col" : "flex-1 p-4"}>{children}</main>
    </div>
  );
}
