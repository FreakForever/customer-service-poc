import { expect, test, type Page } from "@playwright/test";

import { a11y, overflow, signIn } from "./util";

const tile = (page: Page, name: string) => page.getByRole("group", { name, exact: true });

async function ask(page: Page, text: string) {
  const p = page.getByRole("dialog", { name: "Matsui Assistant" });
  const n = await p.locator("[data-role=assistant]").count();
  await p.getByLabel("Your message").fill(text);
  await p.getByRole("button", { name: "Send", exact: true }).click();
  await expect(p.locator("[data-role=assistant]").nth(n)).toHaveAttribute("data-tier", /T/, { timeout: 40_000 });
  return p.locator("[data-role=assistant]").nth(n);
}

test.describe("analytics", () => {
  test("role guard", async ({ page, request }) => {
    await page.goto("/analytics");
    await expect(page).toHaveURL(/\/signin\?callbackUrl=%2Fanalytics/);
    expect((await request.get("/api/staff/analytics/summary")).status()).toBe(401);
    await signIn(page, "agent", "/analytics");
    await expect(tile(page, "Conversations")).toBeVisible();
  });
  test("a chat with a thumbs down and a handoff shows up, and the page agrees with the API", async ({ page, browser }) => {
    const visitor = await (await browser.newContext()).newPage();
    await visitor.goto("/");
    await visitor.getByRole("button", { name: "Chat with us" }).click();
    await ask(visitor, "What is the rotor warranty?");
    await visitor.getByRole("button", { name: "Not helpful" }).last().click();
    await ask(visitor, "talk to a human");
    await visitor.context().close();
    await signIn(page, "agent", "/analytics");
    const s = await (await page.request.get("/api/staff/analytics/summary")).json();
    expect(s.volume.turns).toBeGreaterThanOrEqual(2);
    expect(s.handoff_rate.conversations).toBeGreaterThanOrEqual(1);
    expect(s.cost.total).toBeGreaterThan(0);
    await page.reload();
    await expect(tile(page, "Conversations")).toContainText(`${s.volume.conversations}`);
    await expect(tile(page, "Conversations")).toContainText(`${s.volume.turns} answers`);
    await expect(tile(page, "Handoff rate")).toContainText(`${s.handoff_rate.conversations} handed off`);
    await expect(tile(page, "Cost per conversation")).toContainText("$0.0");
    const flagged = page.getByRole("list", { name: "Flagged answers" });
    await expect(flagged).toContainText("thumbs down");
    await page.getByRole("list", { name: "Answers by tier per day" }).getByRole("button").last().focus();
    await expect(page.getByText(/T4 person \d/).first()).toBeVisible();
    await page.getByText("Show as table").first().click();
    await expect(page.getByRole("region", { name: "Answers by tier per day as a table" })).toBeVisible();
    await flagged.getByRole("link", { name: "Open AI trace" }).first().click();
    await expect(page).toHaveURL(/\/console\/c\/.+trace=/);
    await expect(page.getByRole("tab", { name: "AI trace" })).toHaveAttribute("aria-selected", "true");
  });
  test("filters", async ({ page }) => {
    await signIn(page, "agent", "/analytics");
    await expect(tile(page, "Conversations")).toBeVisible();
    await page.getByRole("combobox", { name: "Channel" }).selectOption("chat");
    await expect(page.getByRole("region", { name: "Volume by channel" }).last()).not.toContainText("email");
    await page.getByRole("combobox", { name: "Period" }).selectOption("custom");
    await page.getByLabel("From", { exact: true }).fill("2024-01-01");
    await expect(page.getByText("Choose a period of 1 to 92 days.")).toBeVisible();
    await page.getByLabel("To", { exact: true }).fill("2024-01-07");
    await expect(tile(page, "Conversations")).toContainText("0 answers");
    await expect(tile(page, "Deflection")).toContainText("–");
  });
  test("cost and benchmark tabs", async ({ page }) => {
    await signIn(page, "agent", "/analytics");
    await page.getByRole("button", { name: "Cost", exact: true }).click();
    await expect(page.getByRole("region", { name: "Cost per model" }).last()).toContainText("gemini-3.5-flash-lite");
    await expect(page.getByRole("region", { name: "Most expensive conversations" }).last().getByRole("link").first()).toBeVisible();
    await page.getByRole("button", { name: "Benchmark" }).click();
    await expect(page.getByText("No benchmark run has been recorded yet.")).toBeVisible();
    await page.route("**/api/staff/analytics/benchmark", (r) => r.fulfill({ json: { runs: [], by_mode: ["kg", "rag"].map((mode, i) => ({ mode, started_at: "2026-01-01T00:00:00Z", n: 10, correctness: 0.8 - i / 5, groundedness: 0.9, p50_ms: 900, p95_ms: 2000, tokens: 1000, cost_usd: 0.01 })), by_type: [{ type: "multi_hop", modes: { kg: { n: 4, correctness: 1 } } }] } }));
    await page.reload();
    await page.getByRole("button", { name: "Benchmark" }).click();
    await expect(page.getByRole("region", { name: "By question type" })).toContainText("multi hop");
    await expect(page.getByRole("list", { name: "Correctness by mode" })).toContainText("60%");
  });
  test("accessible and no page overflow", async ({ page }) => {
    await signIn(page, "agent", "/analytics");
    for (const tab of ["Overview", "Cost", "Benchmark"]) for (const width of [360, 1366]) {
      await page.setViewportSize({ width, height: 768 });
      await page.getByRole("button", { name: tab, exact: true }).click();
      await expect(page.getByText("Loading…")).toHaveCount(0);
      expect(await overflow(page), `${tab} at ${width}`).toBeLessThanOrEqual(0);
      await a11y(page);
      if (process.env.SHOTS) await page.screenshot({ path: `${process.env.SHOTS}/${tab}-${width}.png`, fullPage: true });
    }
  });
});
