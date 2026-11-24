import type { Metadata } from "next";
import type { ReactNode } from "react";

import { DemoBar } from "@/components/demo/DemoBar";
import "../styles/globals.css";

export const metadata: Metadata = { title: { default: "Matsui Support", template: "%s | Matsui Support" } };
export const dynamic = "force-dynamic";      // environment and session are read per request

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:bg-surface focus:p-2">Skip to content</a>
        <DemoBar />
        {children}
      </body>
    </html>
  );
}
