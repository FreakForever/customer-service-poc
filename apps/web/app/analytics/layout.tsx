import type { ReactNode } from "react";

import { StaffShell } from "@/components/layout/StaffShell";
import { requireStaff } from "@/lib/auth";

export const metadata = { title: "Analytics" };

export default async function Layout({ children }: { children: ReactNode }) {
  return <StaffShell staff={await requireStaff("agent")} section="analytics">{children}</StaffShell>;
}
