import { readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

import { a11y, overflow, signIn } from "./util";

const sample = "../../mock-data/upload-samples/SB-2026-12_DHD_rotor_revision.md";     // pnpm runs in apps/web

test.describe("admin", () => {
  test("agents can't open it", async ({ page }) => {
    await signIn(page, "agent");
    for (const p of ["/admin", "/admin/jobs", "/admin/documents", "/admin/faqs", "/admin/changes", "/admin/kg"]) expect((await page.goto(p))!.status(), p).toBe(403);
  });
  test("upload to answer, then delete, entirely from the UI", async ({ page, browser }) => {
    test.setTimeout(120_000);
    await signIn(page, "admin");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    const name = `sb-e2e-${Date.now()}.md`, doc = `DOC-${name.replace(".md", "").toUpperCase()}`;
    await page.getByLabel("Drop a file here, or choose one").setInputFiles({ name, mimeType: "text/markdown", buffer: readFileSync(sample) });
    await page.getByLabel("Title").fill("Rotor revision bulletin e2e");
    await page.getByLabel("Product family").selectOption("DHD");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await expect(page.getByText("sections are searchable now")).toBeVisible({ timeout: 40_000 });
    await page.getByRole("link", { name: "Jobs" }).click();
    await expect(page.locator("tbody tr").first()).toContainText(doc);
    await expect(page.locator("tbody tr").first()).toContainText("done");

    const visitor = await (await browser.newContext()).newPage();
    await visitor.goto("/");
    await expect(visitor.getByRole("link", { name: /Rotor revision bulletin e2e/ }).first()).toBeVisible();
    await expect(visitor.getByRole("heading", { name: "Service bulletins" })).toBeVisible();
    await visitor.getByRole("button", { name: "Chat with us" }).click();
    const p = visitor.getByRole("dialog", { name: "Matsui Assistant" });
    const ask = async () => {
      const n = await p.locator("[data-role=assistant]").count();
      await p.getByLabel("Your message").fill("Which rotor assembly replaces MT-DHD-ROTOR after the rotor revision bulletin for ROTOR STALL?");
      await p.getByRole("button", { name: "Send", exact: true }).click();
      const b = p.locator("[data-role=assistant]").nth(n);
      await expect(b).toHaveAttribute("data-tier", /T/, { timeout: 40_000 });
      return b;
    };
    await expect((await ask()).locator(`a[href^="/a/${doc}#"]`).first()).toBeVisible();

    await page.getByRole("link", { name: "Documents" }).click();
    await page.getByRole("button", { name: `Re-index ${doc}` }).click();
    await expect(page.getByText(`${doc} queued for re-indexing`)).toBeVisible();
    await page.getByRole("button", { name: `Delete ${doc}` }).click();
    await page.getByRole("button", { name: "Keep" }).click();
    await page.getByRole("button", { name: `Delete ${doc}` }).click();
    await page.getByRole("button", { name: "Yes, delete" }).click();
    await expect(page.locator("tr", { hasText: doc }).getByText("deleted", { exact: true })).toBeVisible();
    await expect(page.locator("tr", { hasText: doc }).locator("td").nth(4)).toHaveText("0", { timeout: 20_000 });
    await p.getByRole("button", { name: "New chat" }).click();
    await expect((await ask()).locator(`a[href^="/a/${doc}"]`)).toHaveCount(0);
    expect((await visitor.goto(`/a/${doc}`))!.status()).toBe(404);
    await visitor.context().close();
  });
  test("bad files are refused with a reason", async ({ page }) => {
    await signIn(page, "admin");
    await page.getByLabel("Title").fill("Bad file");
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await expect(page.getByText("Choose a file first.")).toBeVisible();
    await page.getByLabel("Drop a file here, or choose one").setInputFiles({ name: "x.pdf", mimeType: "application/pdf", buffer: Buffer.from("nope") });
    await page.getByRole("button", { name: "Upload", exact: true }).click();
    await expect(page.getByText("the file can't be read")).toBeVisible();
  });
  test("FAQ edit", async ({ page }) => {
    await signIn(page, "admin", "/admin/faqs");
    await page.getByLabel("Search").fill("FAQ-004");
    await expect(page.locator("tbody tr")).toHaveCount(1);
    await page.getByRole("button", { name: "Edit FAQ-004" }).click();
    const f = page.getByRole("form", { name: "Edit FAQ-004" });
    await f.getByLabel("Answer").fill("Edited in the browser test.");
    await f.getByRole("button", { name: "Save" }).click();
    await expect(page.getByText("FAQ-004 saved")).toBeVisible();
    await expect(page.locator("tbody tr")).toContainText("Edited in the browser test.");
  });
  test("graph changes are listed and decided", async ({ page }) => {
    await signIn(page, "admin", "/admin/changes");
    const card = page.getByRole("list", { name: "Changes" }).getByRole("listitem").filter({ hasText: "E2E-REJECT" });
    await expect(card).toContainText("add node");
    await card.getByRole("button", { name: "Reject" }).click();
    await expect(page.getByText("Rejected", { exact: true })).toBeVisible();
    await expect(card).toHaveCount(0);
    await page.getByLabel("Show").selectOption("rejected");
    await expect(card).toContainText("rejected by admin@staff.matsui-demo.example");
  });
  test("graph explorer", async ({ page }) => {
    await signIn(page, "admin", "/admin/kg");
    await expect(page.getByRole("heading", { name: /DHD-150/, level: 2 })).toBeVisible();
    await page.getByLabel("Find a node").fill("CHL-10W");
    await page.getByRole("list", { name: "Nodes found" }).getByRole("button").first().click();
    await expect(page.getByRole("heading", { name: /CHL-10W/, level: 2 })).toBeVisible();
    const n = await page.locator("svg g").count();
    await page.getByLabel("Depth").selectOption("2");
    await expect.poll(() => page.locator("svg g").count()).toBeGreaterThan(n);
    await page.getByRole("list", { name: "Facts" }).getByRole("button").first().click();
    await expect(page.locator("svg g").first()).toBeVisible();
  });
  test("accessible and no page overflow", async ({ page }) => {
    await signIn(page, "admin");
    for (const p of ["/admin", "/admin/jobs", "/admin/documents", "/admin/faqs", "/admin/changes", "/admin/kg"]) for (const width of [360, 1366]) {
      await page.setViewportSize({ width, height: 768 });
      await page.goto(p);
      await expect(page.getByText("Loading…")).toHaveCount(0);
      expect(await overflow(page), `${p} at ${width}`).toBeLessThanOrEqual(0);
      await a11y(page);
    }
  });
});
