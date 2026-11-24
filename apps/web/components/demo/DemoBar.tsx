import { cookies } from "next/headers";

import { getStaff } from "@/lib/auth";
import { PERSONA_COOKIE, validPersona } from "@/lib/personas";
import { demoEnabled, demoStaffEnabled } from "@/lib/roles";

import { PersonaBar } from "./PersonaBar";

export async function DemoBar() {
  if (!demoEnabled(process.env)) return null;
  const persona = validPersona((await cookies()).get(PERSONA_COOKIE)?.value);
  return <PersonaBar persona={persona} staffRole={(await getStaff())?.role ?? null}
    staffLogin={demoStaffEnabled(process.env) && Boolean(process.env.NEXTAUTH_SECRET)} />;
}
