import { cookies } from "next/headers";
import type { ReactNode } from "react";

import { ChatWidget } from "@/components/chat/ChatWidget";
import { HelpShell } from "@/components/layout/HelpShell";
import { PERSONA_COOKIE, validPersona } from "@/lib/personas";
import { demoEnabled } from "@/lib/roles";

export default async function Layout({ children }: { children: ReactNode }) {
  const persona = demoEnabled(process.env) ? validPersona((await cookies()).get(PERSONA_COOKIE)?.value) : "";
  return <><HelpShell>{children}</HelpShell><ChatWidget key={persona} persona={persona} /></>;
}
