import type { ReactNode } from "react";

import { AdminNav } from "@/components/admin/AdminNav";
import { StaffShell } from "@/components/layout/StaffShell";
import { requireStaff } from "@/lib/auth";

export const metadata = { title: "Knowledge admin" };

export default async function Layout({ children }: { children: ReactNode }) {
  return <StaffShell staff={await requireStaff("admin")} section="admin"><AdminNav />{children}</StaffShell>;
}
