import { expect, test } from "@playwright/test";

import { a11y, API, overflow } from "./util";
import { staffToken } from "../lib/staffToken";

test.describe("helpcentre", () => {
  test("home, category and article pages are accessible and fit a phone", async ({ page }) => {
    for (const path of ["/", "/c/DHD", "/a/DOC-MAN-DHD", "/a/DOC-POL-WARRANTY-V1", "/search?q=E04", "/contact", "/signin"]) {
      for (const width of [360, 1366]) {
        await page.setViewportSize({ width, height: 768 });
        await page.goto(path);
        expect(await overflow(page), `${path} at ${width}`).toBeLessThanOrEqual(0);
        await a11y(page);
      }
    }
  });
  test("home shows families, articles and contact options", async ({ page }) => {
    await page.goto("/");
    await expect(page.locator("#products li")).toHaveCount(7);
    await expect(page.locator("#support a")).toHaveCount(10);
    await page.getByRole("link", { name: "Dehumidifying dryer", exact: true }).click();
    await expect(page.getByRole("heading", { level: 1 })).toHaveText("Dehumidifying dryer");
  });
  test("typeahead and search find error codes and open the section", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("searchbox").fill("LP TRIP");
    await page.getByRole("list", { name: "Suggestions" }).getByRole("link").first().click();
    await expect(page).toHaveURL(/\/a\/DOC-MAN-CHL#a02/);
    await page.goto("/");
    await page.getByRole("searchbox").fill("rotor warranty");
    await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { level: 2 }).filter({ hasText: /warranty/i }).first()).toBeVisible();
    await page.goto("/search?q=zzzzqqq");
    await expect(page.getByText("Nothing found")).toBeVisible();
  });
  test("a citation deep link scrolls to its heading", async ({ page }) => {
    const a = await (await fetch(`${API}/v1/kb/articles/DOC-MAN-DHD`)).json();
    const anchor = a.sections.find((s: { anchor: string }) => s.anchor.startsWith("e04")).anchor;
    await page.goto(`/a/DOC-MAN-DHD#${anchor}`);
    await expect(page.locator(`#${anchor}`)).toBeInViewport();
    await expect(page.locator(`#${anchor} h3`)).toContainText("E04");
    expect(await page.evaluate(() => window.scrollY)).toBeGreaterThan(200);
    for (const s of a.sections) await expect(page.locator(`[id="${s.anchor}"]`)).toHaveCount(1);
  });
  test("version badge, superseded notice, unknown article", async ({ page }) => {
    await page.goto("/a/DOC-POL-WARRANTY-V1");
    await expect(page.getByText("v1.0 superseded")).toBeVisible();
    await page.getByRole("note").getByRole("link").click();
    await expect(page.locator("article").getByText("v2.0", { exact: true })).toBeVisible();
    expect((await page.goto("/a/NOPE"))!.status()).toBe(404);
    expect((await page.goto("/c/ZZZ"))!.status()).toBe(404);
  });
  test("contact form becomes an email conversation with a reply in the demo mailbox", async ({ page }) => {
    const subject = `Rotor warranty ${Date.now()}`;
    await page.goto("/contact");
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("This field is required")).toHaveCount(5);
    for (const [l, v] of [["Name", "Rohit"], ["Company", "Aarav Polymers"], ["Email", "rohit.sharma@demo-aarav-polymers.example"], ["Subject", subject], ["Message", "Are filters covered under warranty?"]])
      await page.getByLabel(l, { exact: true }).fill(v);
    await page.getByRole("button", { name: "Send" }).click();
    await expect(page.getByText("Your message has been received")).toBeVisible();
    const headers = { Authorization: `Bearer ${await staffToken({ email: "a@x.example", name: "A", role: "agent" }, "e2e-secret")}` };
    const box = async (b: string) => (await (await fetch(`${API}/v1/console/mailbox?box=${b}`, { headers })).json()).items as { subject: string; body_text: string }[];
    expect((await box("inbox")).some((m) => m.subject === subject && m.body_text.includes("Company: Aarav Polymers"))).toBe(true);
    await expect.poll(async () => (await box("outbox")).find((m) => m.subject === `Re: ${subject}`)?.body_text ?? "", { timeout: 30_000 }).toContain("Dear Rohit");
  });
});
