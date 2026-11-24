import { expect, test, type Page } from "@playwright/test";

import { a11y, API, overflow, signIn } from "./util";

const uniq = () => `e2e ${Date.now()}`;
async function customer(page: Page, text: string) {
  await page.goto("/");
  await page.getByRole("button", { name: "Chat with us" }).click();
  const p = page.getByRole("dialog", { name: "Matsui Assistant" });
  for (const t of text.split("|")) {
    const n = await p.locator("[data-role=assistant]").count();
    await p.getByLabel("Your message").fill(t);
    await p.getByRole("button", { name: "Send", exact: true }).click();
    await expect(p.locator("[data-role=assistant]").nth(n)).toHaveAttribute("data-tier", /T/, { timeout: 40_000 });
  }
  return p;
}
async function email(subject: string, body: string, from = "rohit.sharma@demo-aarav-polymers.example") {
  const r = await fetch(`${API}/v1/email/inbound`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ from, name: "Rohit", subject, body }) });
  return (await r.json()).conversation_id as string;
}

test.describe("console", () => {
  test("role guard", async ({ page, request }) => {
    await page.goto("/console");
    await expect(page).toHaveURL(/\/signin\?callbackUrl=%2Fconsole/);
    expect((await request.get("/api/staff/console/tickets")).status()).toBe(401);
    await signIn(page, "agent");
    expect((await page.request.get("/api/staff/console/tickets")).status()).toBe(200);
    expect((await page.request.get("/api/staff/admin/jobs")).status()).toBe(403);
    expect((await page.request.post("/api/staff/console/conversations/x/takeover")).status()).toBe(400);     // no custom header
  });
  test("CH-008: handoff reaches the queue, live take-over, reply, return to bot", async ({ page, browser }) => {
    const note = uniq();
    const p = await customer(page, `hi|talk to a human, ${note}`);
    const agent = await (await browser.newContext({ viewport: { width: 1366, height: 768 } })).newPage();
    await signIn(agent, "agent");
    await agent.getByRole("link", { name: /Live handoffs/ }).click();
    await agent.getByRole("searchbox", { name: "Search conversations" }).fill(note);
    await agent.getByRole("list", { name: "Conversation list" }).getByRole("link").first().click();
    await expect(agent.getByRole("region", { name: "Handoff summary" })).toBeVisible();
    await expect(agent.getByLabel("Reply")).toBeDisabled();
    await agent.getByRole("button", { name: "Take over" }).click();
    await expect(p.getByText("You're now chatting with Demo (Matsui)")).toBeVisible();
    await agent.getByLabel("Reply").fill("Hello, this is Karthik.");
    await agent.getByRole("button", { name: "Send", exact: true }).click();
    await expect(p.locator("[data-role=agent]")).toContainText("Hello, this is Karthik.");
    await expect(p.locator("[data-role=agent]")).toHaveCount(1);
    await p.getByLabel("Your message").fill("thank you Karthik");
    await p.getByRole("button", { name: "Send", exact: true }).click();
    await expect(agent.locator("[data-role=customer]").last()).toContainText("thank you Karthik");
    await page.reload();
    await expect(p.locator("[data-role=agent]")).toHaveCount(1);
    await expect(p.getByText("Demo (Matsui)").first()).toBeVisible();
    await agent.getByRole("button", { name: "Return to bot" }).click();
    await expect(p.getByText(/You're now chatting/)).toHaveCount(0);
    await agent.context().close();
  });
  test("review queue: edit and send an email draft", async ({ page }) => {
    const subject = uniq();
    await email(subject, "Is AMC-2025-0001 active? Please tell me the status", "someone.else@demo-aarav-polymers.example");
    await signIn(page, "agent");
    await page.goto("/console?view=review");
    const row = page.getByRole("list", { name: "Conversation list" }).getByRole("link", { name: subject });
    await expect(row).toBeVisible({ timeout: 40_000 });
    await row.click();
    await expect(page.getByText("AI draft")).toBeVisible();
    await expect(page.getByLabel("Reply")).toHaveValue(/Matsui Customer Care/);
    await expect(page.getByRole("heading", { name: "Aarav Polymers", level: 2 })).toBeVisible();
    await page.getByLabel("Reply").fill("Dear customer,\n\nChecked by a person.\n\nMatsui Customer Care");
    await page.getByRole("button", { name: "Send edited reply" }).click();
    await expect(page.getByText(/edited and sent by Demo agent/)).toBeVisible();
    await expect(row).toHaveCount(0);
    const box = await (await page.request.get("/api/staff/console/mailbox?box=outbox")).json();
    expect(box.items.find((m: { subject: string }) => m.subject === `Re: ${subject}`).body_text).toContain("Checked by a person");
  });
  test("context panel and AI trace for an email with account data", async ({ page }) => {
    const subject = uniq();
    const id = await email(subject, "Is AMC-2025-0001 active? Please tell me the status");
    await signIn(page, "agent");
    await page.goto(`/console/c/${id}`);
    await expect(page.locator("[data-role=assistant]")).toBeVisible({ timeout: 40_000 });
    const ctx = page.getByRole("complementary", { name: "Context" });
    for (const t of ["Aarav Polymers", "MT-DHD150-23-00417", "AMC-2025-0001", "Open tickets"]) await expect(ctx.getByText(t).first()).toBeVisible();
    const t0 = Date.now();
    await page.getByRole("button", { name: "AI trace" }).first().click();
    await expect(ctx.getByRole("list", { name: "Spans" }).getByRole("listitem").first()).toBeVisible();
    expect(Date.now() - t0).toBeLessThan(1000);
    await expect(ctx.getByRole("list", { name: "Evidence" }).getByRole("listitem").first()).toBeVisible();
    await expect(ctx.getByText("amc.status")).toBeVisible();
  });
  test("promote a reviewed answer, then it is served from the cache", async ({ page, browser }) => {
    const q = `Is mixed regrind covered under the warranty clause ${Date.now()}?`;
    const p = await customer(page, q);
    await expect(p.locator("[data-role=assistant]").last()).not.toHaveAttribute("data-tier", "T1");
    const agent = await (await browser.newContext()).newPage();
    await signIn(agent, "agent");
    await agent.goto("/console?view=all");
    await agent.getByRole("searchbox", { name: "Search conversations" }).fill(q);
    await agent.getByRole("list", { name: "Conversation list" }).getByRole("link").first().click();
    await agent.getByRole("button", { name: "Promote to FAQ" }).first().click();
    const f = agent.getByRole("form", { name: "Promote to FAQ" });
    await expect(f.getByLabel("Question")).toHaveValue(q);
    await f.getByLabel("Answer").fill("Promoted answer about regrind.");
    await f.getByLabel("Source document").fill("DOC-POL-WARRANTY-V2");
    await f.getByRole("button", { name: "Save to FAQ" }).click();
    await expect(f.getByText(/Saved as FAQ-P-/)).toBeVisible();
    await p.getByRole("button", { name: "New chat" }).click();
    await p.getByLabel("Your message").fill(q);
    await p.getByRole("button", { name: "Send", exact: true }).click();
    await expect(p.locator("[data-role=assistant]").first()).toHaveAttribute("data-tier", "T1", { timeout: 30_000 });
    await expect(p.locator("[data-role=assistant]").first()).toContainText("Promoted answer about regrind.");
    await agent.context().close();
  });
  test("tickets: filter and update", async ({ page }) => {
    await signIn(page, "agent", "/console/tickets");
    const rows = page.locator("tbody tr");
    await expect(rows.first()).toBeVisible();
    await page.getByLabel("Priority").selectOption("P1");
    await expect.poll(async () => new Set(await rows.locator("td:nth-child(4)").allInnerTexts())).toEqual(new Set(["P1"]));
    await page.getByLabel("Search", { exact: true }).fill("SR-2026-00101");
    await expect(rows).toHaveCount(1);
    await page.getByLabel("Status of SR-2026-00101").selectOption("awaiting_parts");
    await expect(page.getByText("SR-2026-00101 updated")).toBeVisible();
    await expect(page.getByLabel("Status of SR-2026-00101")).toHaveValue("awaiting_parts");
  });
  test("three panes usable at 1366x768, accessible, no page overflow", async ({ page }) => {
    const id = await email(uniq(), "Is AMC-2025-0001 active? Please tell me the status");
    await signIn(page, "agent");
    await page.goto(`/console/c/${id}`);
    await expect(page.locator("[data-role=assistant]")).toBeVisible({ timeout: 40_000 });
    for (const n of ["Conversations", "Context"]) await expect(page.getByRole("complementary", { name: n, exact: true })).toBeInViewport();
    await expect(page.getByRole("button", { name: /send/i }).last()).toBeInViewport({ ratio: 1 });
    expect(await page.evaluate(() => document.documentElement.scrollHeight - window.innerHeight)).toBeLessThanOrEqual(0);
    await a11y(page);
    await page.getByRole("tab", { name: "AI trace" }).click();
    await a11y(page);
    await page.goto("/console/tickets");
    await expect(page.locator("tbody tr").first()).toBeVisible();
    await a11y(page);
    await page.setViewportSize({ width: 360, height: 740 });
    await page.goto(`/console/c/${id}`);
    await expect(page.locator("[data-role=assistant]")).toBeVisible();
    expect(await overflow(page)).toBeLessThanOrEqual(0);
  });
});
