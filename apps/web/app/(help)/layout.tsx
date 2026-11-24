import type { ReactNode } from "react";

import { HelpShell } from "@/components/layout/HelpShell";

export default function Layout({ children }: { children: ReactNode }) {
  return <HelpShell>{children}</HelpShell>;
}
