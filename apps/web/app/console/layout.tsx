import { Suspense, type ReactNode } from "react";

import { ViewList } from "@/components/console/ViewList";
import { StaffShell } from "@/components/layout/StaffShell";
import { requireStaff } from "@/lib/auth";

export const metadata = { title: "Conversations" };

export default async function Layout({ children }: { children: ReactNode }) {
  return (
    <StaffShell staff={await requireStaff("agent")} section="console" flush>
      <div className="grid min-h-0 flex-1 md:grid-cols-[15rem_1fr]"><Suspense><ViewList /></Suspense>{children}</div>
    </StaffShell>
  );
}
