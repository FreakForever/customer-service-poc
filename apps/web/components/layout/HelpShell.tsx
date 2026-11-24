import Link from "next/link";
import type { ReactNode } from "react";

import { DemoBadge } from "../demo/DemoBadge";
import { Wordmark } from "./Wordmark";

const link = "inline-flex min-h-8 items-center hover:underline";

export function HelpShell({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen flex-col">
      <header className="on-navy bg-navy text-surface">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
          <Link href="/"><Wordmark sub="Support" /></Link>
          <nav aria-label="Main" className="flex flex-1 flex-wrap gap-x-5 text-sm">
            <Link className={link} href="/#products">Products</Link>
            <Link className={link} href="/#support">Support</Link>
            <Link className={link} href="/#service-centres">Service centres</Link>
            <Link className={link} href="/contact">Contact</Link>
          </nav>
          <DemoBadge label="DEMO" />
          <Link className={`${link} text-sm`} href="/console">Sign in (staff)</Link>
        </div>
      </header>
      <main id="main" className="flex-1">{children}</main>
      <footer className="border-t border-line bg-surface text-sm text-steel">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-3 px-4 py-4">
          <span>Matsui support demo</span><DemoBadge label="Demo data — synthetic" />
        </div>
      </footer>
    </div>
  );
}
