import { encode } from "next-auth/jwt";
import { NextRequest } from "next/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { authOptions } from "@/lib/auth";
import { allowlistRole, allows, demoEnabled, demoStaffEnabled, requiredRole, roleOf } from "@/lib/roles";
import { middleware } from "@/middleware";

vi.mock("next/headers", () => ({ cookies: vi.fn(), headers: vi.fn() }));
const SECRET = "test-secret";
beforeEach(() => {
  for (const [k, v] of Object.entries({ NEXTAUTH_SECRET: SECRET, ENV: "dev", DEMO_STAFF_LOGIN: "true", K_SERVICE: "", NEXTAUTH_URL: "",
    GOOGLE_CLIENT_ID: "", GOOGLE_CLIENT_SECRET: "", STAFF_ALLOWLIST: "agent:@matsui.example, admin:boss@matsui.example" })) vi.stubEnv(k, v);
});
afterEach(() => vi.unstubAllEnvs());

describe("NFR-SEC-01 allow-list", () => {
  const list = "agent:@m.example, admin:boss@m.example, plain@x.example, admin:bad entry, admin:";
  it.each([
    ["boss@m.example", undefined, "admin"], ["BOSS@M.example", "m.example", "admin"], ["a@m.example", "m.example", "agent"],
    ["a@m.example", undefined, null], ["a@m.example", "evil.example", null], ["a@evil.example", "m.example", null],
    ["plain@x.example", undefined, "agent"], ["x@y.example", undefined, null], ["", "m.example", null],
  ])("%s (hd %s) → %s", (email, hd, want) => expect(allowlistRole({ email, hd }, list)).toBe(want));
  it("empty list admits nobody", () => {
    expect(allowlistRole({ email: "a@b.example" }, "")).toBeNull();
    expect(allowlistRole({ email: "a@b.example" }, undefined)).toBeNull();
  });
});

describe("demo switches", () => {
  it.each([
    [{ ENV: "dev", DEMO_STAFF_LOGIN: "true" }, true], [{ ENV: "dev" }, false], [{ ENV: "demo", DEMO_STAFF_LOGIN: "true" }, false],
    [{ ENV: "dev", DEMO_STAFF_LOGIN: "true", K_SERVICE: "mcs-web" }, false], [{ DEMO_STAFF_LOGIN: "true" }, false],
  ])("demo staff %j → %s", (env, want) => expect(demoStaffEnabled(env)).toBe(want));
  it("a demo session is worthless where demo login is off", () =>
    expect(roleOf({ email: "boss@m.example", demoRole: "admin" }, { ENV: "demo", STAFF_ALLOWLIST: "admin:boss@m.example" })).toBeNull());
  it("persona bar", () => {
    expect([{ ENV: "dev" }, { ENV: "demo" }, { ENV: "prod" }, {}, { ENV: "dev", DEMO_PERSONAS: "false" }].map(demoEnabled)).toEqual([true, true, false, false, false]);
  });
});

it("paths and roles", () => {
  expect(["/console", "/console/c/1", "/analytics", "/admin", "/admin/kg", "/api/demo/x", "/api/staff/console/x", "/api/staff/admin/jobs", "/", "/consolex", "/signin"].map(requiredRole))
    .toEqual(["agent", "agent", "agent", "admin", "admin", "admin", "agent", "admin", null, null, null]);
  expect([allows("admin", "agent"), allows("agent", "agent"), allows("agent", "admin"), allows(null, "agent")]).toEqual([true, true, false, false]);
});

async function go(path: string, token?: Record<string, unknown>, secret = SECRET) {
  const headers: Record<string, string> = {};
  if (token) headers.cookie = `next-auth.session-token=${await encode({ token, secret })}`;
  return middleware(new NextRequest(`http://localhost${path}`, { headers }));
}

describe("NFR-SEC-01 middleware", () => {
  it("unauthenticated console redirects to sign-in", async () => {
    const r = await go("/console/c/1?x=2");
    expect(r.status).toBe(307);
    const to = new URL(r.headers.get("location")!);
    expect([to.origin, to.pathname, to.searchParams.get("callbackUrl")]).toEqual(["http://localhost", "/signin", "/console/c/1?x=2"]);
  });
  it.each([
    ["/console", { email: "a@matsui.example", hd: "matsui.example" }, 200], ["/console", { email: "x@gmail.example" }, 403],
    ["/admin", { email: "a@matsui.example", hd: "matsui.example" }, 403], ["/admin", { email: "boss@matsui.example" }, 200],
    ["/console", { email: "d@x.example", demoRole: "agent" }, 200], ["/admin", { email: "d@x.example", demoRole: "agent" }, 403],
    ["/admin", { email: "d@x.example", demoRole: "root" }, 403], ["/api/demo/send-sample-emails", { email: "d@x.example", demoRole: "agent" }, 403],
  ])("%s %j → %i", async (path, token, status) => expect((await go(path, token)).status).toBe(status));
  it("forged cookies and a missing secret are not sessions", async () => {
    expect((await go("/admin", { email: "boss@matsui.example" }, "other")).status).toBe(307);
    vi.stubEnv("NEXTAUTH_SECRET", "");
    expect((await go("/admin", { email: "boss@matsui.example" })).status).toBe(307);
    expect((await go("/api/demo/x")).status).toBe(401);
  });
  it("removal from the allow-list takes effect at once", async () => {
    vi.stubEnv("STAFF_ALLOWLIST", "");
    expect((await go("/console", { email: "boss@matsui.example" })).status).toBe(403);
  });
});

describe("providers", () => {
  const ids = () => authOptions().providers.map((p) => (p.options as { id?: string } | undefined)?.id ?? p.id);
  it("demo login only in local dev; Google only when configured", () => {
    expect(ids()).toEqual(["demo"]);
    vi.stubEnv("ENV", "demo");
    expect(ids()).toEqual([]);
    vi.stubEnv("GOOGLE_CLIENT_ID", "id");
    vi.stubEnv("GOOGLE_CLIENT_SECRET", "s");
    expect(ids()).toEqual(["google"]);
  });
  it("Google sign-in needs a verified address", async () => {
    const signIn = authOptions().callbacks!.signIn!;
    const call = (p: object) => signIn({ account: { provider: "google" }, profile: p } as never);
    expect(await call({ email_verified: true })).toBe(true);
    expect(await call({ email_verified: false })).toBe(false);
    expect(await call({})).toBe(false);
  });
  it("jwt keeps the hosted domain and never a demo role from Google", async () => {
    const jwt = authOptions().callbacks!.jwt!;
    expect(await jwt({ token: { demoRole: "admin" }, account: { provider: "google" }, profile: { hd: "m.example" } } as never)).toEqual({ hd: "m.example" });
    expect(await jwt({ token: {}, account: { provider: "demo" }, user: { id: "demo-agent" } } as never)).toEqual({ demoRole: "agent" });
  });
});
