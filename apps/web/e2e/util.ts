import AxeBuilder from "@axe-core/playwright";
import { expect, type Page } from "@playwright/test";

export const API = "http://127.0.0.1:8100";

export async function a11y(page: Page) {
  const r = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"]).analyze();
  expect(r.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(" | ")}`)).toEqual([]);
}
export const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);

export async function signIn(page: Page, role: "agent" | "admin", to = role === "admin" ? "/admin" : "/console") {
  await page.goto(`/signin?callbackUrl=${encodeURIComponent(to)}`);
  await page.getByRole("button", { name: `Continue as demo ${role}` }).click();
  await page.waitForURL(`**${to}`);
}
