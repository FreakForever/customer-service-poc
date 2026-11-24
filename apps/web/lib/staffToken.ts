import { createHash } from "node:crypto";
import { SignJWT } from "jose";

import type { Staff } from "./auth";

/** Short-lived token for the agent API (LLD-14); verified by services/agent/app/api/staff.py. Server side only. */
export async function staffToken(s: Staff, secret = process.env.NEXTAUTH_SECRET): Promise<string> {
  if (!secret) throw new Error("NEXTAUTH_SECRET is not set");
  const key = createHash("sha256").update(`staff-api:${secret}`).digest();
  return new SignJWT({ role: s.role, name: s.name })
    .setProtectedHeader({ alg: "HS256", typ: "JWT" }).setSubject(s.email).setIssuer("mcs-web").setAudience("mcs-agent-api")
    .setIssuedAt().setExpirationTime("5m").sign(key);
}
