import { existsSync, readFileSync } from "node:fs";
import { jwtVerify } from "jose";
import { createHash } from "node:crypto";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import { PersonaBar } from "@/components/demo/PersonaBar";
import { HelpShell } from "@/components/layout/HelpShell";
import { StaffShell } from "@/components/layout/StaffShell";
import { PERSONAS, validPersona } from "@/lib/personas";
import { staffToken } from "@/lib/staffToken";

vi.mock("next/navigation", () => ({ useRouter: () => ({ refresh: () => {} }) }));
const agent = { email: "a@m.example", name: "Asha", role: "agent" as const };

describe("FR-UI-08 persona bar", () => {
  const html = (p: Partial<Parameters<typeof PersonaBar>[0]>) => renderToStaticMarkup(<PersonaBar persona="" staffRole={null} staffLogin={false} {...p} />);
  it("offers the seed companies, and staff only where allowed", () => {
    for (const p of PERSONAS) expect(html({})).toContain(p.company);
    expect(html({})).not.toContain("staff:agent");
    expect(html({ staffLogin: true })).toContain("staff:admin");
  });
  it("shows demo actions to admins only", () => {
    expect(html({ staffRole: "agent", staffLogin: true })).not.toContain("Send sample emails");
    expect(html({ staffRole: "admin", staffLogin: true })).toContain("Send sample emails");
  });
  it("personas match the seed data", () => {
    const f = new URL("../../../mock-data/data/ops/customers.json", import.meta.url);
    if (!existsSync(f)) return;
    const seed = new Map((JSON.parse(readFileSync(f, "utf8")) as { id: string; company: string }[]).map((c) => [c.id, c.company]));
    for (const p of PERSONAS) expect(seed.get(p.id)).toBe(p.company);
    expect([validPersona("CUST-0001"), validPersona("CUST-9999"), validPersona(undefined)]).toEqual(["CUST-0001", "", ""]);
  });
});

describe("shells", () => {
  it("help centre carries the demo badges", () => {
    const h = renderToStaticMarkup(<HelpShell>x</HelpShell>);
    expect(h).toContain("Demo data — synthetic");
    expect(h).toContain("Sign in (staff)");
  });
  it("admin link is for admins", () => {
    const h = (role: "agent" | "admin") => renderToStaticMarkup(<StaffShell staff={{ ...agent, role }} section="console">x</StaffShell>);
    expect(h("agent")).not.toContain('href="/admin"');
    expect(h("admin")).toContain('href="/admin"');
    expect(h("agent")).toContain("Demo data");
  });
});

it("NFR-SEC-01 API token: short-lived, scoped, separate key", async () => {
  const key = createHash("sha256").update("staff-api:s").digest();
  const { payload } = await jwtVerify(await staffToken(agent, "s"), key, { audience: "mcs-agent-api", issuer: "mcs-web" });
  expect(payload).toMatchObject({ sub: "a@m.example", role: "agent" });
  expect(payload.exp! - payload.iat!).toBe(300);
});
