/** Staff roles (NFR-SEC-01). No Next imports: shared by middleware, server code and tests. */
export type Role = "agent" | "admin";
export type Identity = { email?: string | null; hd?: string | null; demoRole?: string | null };

type Env = Record<string, string | undefined>;

/**
 * STAFF_ALLOWLIST: comma-separated `role:email` or `role:@domain` (a bare entry means agent).
 * A domain entry needs Google's hosted-domain claim, so a consumer account using that address doesn't match.
 * Evaluated on every request, so removing someone takes effect at once.
 */
export function allowlistRole(id: Identity, allowlist: string | undefined): Role | null {
  const email = id.email?.trim().toLowerCase();
  if (!email) return null;
  let role: Role | null = null;
  for (const raw of (allowlist ?? "").toLowerCase().split(",")) {
    const m = /^(?:(agent|admin):)?(\S+@\S+|@\S+)$/.exec(raw.trim());
    if (!m) continue;
    const entry = m[2];
    const hit = entry.startsWith("@") ? id.hd?.toLowerCase() === entry.slice(1) && email.endsWith(entry) : email === entry;
    if (!hit) continue;
    if (m[1] === "admin") return "admin";
    role = "agent";
  }
  return role;
}

/** Password-less demo staff login: local development only, never on Cloud Run (K_SERVICE). */
export function demoStaffEnabled(env: Env): boolean {
  return env.DEMO_STAFF_LOGIN === "true" && env.ENV === "dev" && !env.K_SERVICE;
}

/** Customer personas and the demo bar (synthetic data only, D-038). Same rule as the API's personas_enabled. */
export function demoEnabled(env: Env): boolean {
  if (env.DEMO_PERSONAS) return env.DEMO_PERSONAS === "true" || env.DEMO_PERSONAS === "1";
  return env.ENV === "dev" || env.ENV === "demo";
}

export function roleOf(id: Identity | null | undefined, env: Env): Role | null {
  if (!id) return null;
  if (id.demoRole) {
    return demoStaffEnabled(env) && (id.demoRole === "agent" || id.demoRole === "admin") ? id.demoRole : null;
  }
  return allowlistRole(id, env.STAFF_ALLOWLIST);
}

/** Role a path needs, or null when public. */
export function requiredRole(path: string): Role | null {
  if (/^\/(admin|api\/demo|api\/staff\/admin)(\/|$)/.test(path)) return "admin";
  if (/^\/(console|analytics|api\/staff)(\/|$)/.test(path)) return "agent";
  return null;
}

export function allows(have: Role | null, need: Role): boolean {
  return have === "admin" || have === need;
}
