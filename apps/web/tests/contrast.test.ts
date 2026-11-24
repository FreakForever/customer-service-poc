import { readdirSync, readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";

/** FR-UI-07: WCAG 2.1 AA — 4.5:1 for text, 3:1 for control borders and focus rings. */
const root = new URL("../", import.meta.url);
const tokens = Object.fromEntries([...readFileSync(new URL("styles/tokens.css", root), "utf8").matchAll(/--([\w-]+):\s*(#[0-9a-f]{6})/g)].map((m) => [m[1], m[2]]));

function lum(hex: string) {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
export function ratio(a: string, b: string) {
  const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
  return (x + 0.05) / (y + 0.05);
}

describe("FR-UI-07 contrast", () => {
  it("formula", () => expect(ratio("#000000", "#ffffff")).toBeCloseTo(21));
  const light = ["mt-surface", "mt-bg", "mt-sky"];
  const text = ["mt-navy", "mt-blue", "mt-blue-600", "mt-steel", "ok", "warn", "bad"].flatMap((f) => light.map((b) => [f, b]));
  it.each([...text, ["mt-surface", "mt-navy"], ["mt-surface", "mt-blue"], ["mt-surface", "mt-blue-600"]])("%s on %s", (f, b) => {
    expect(ratio(tokens[f], tokens[b])).toBeGreaterThanOrEqual(4.5);
  });
  it.each([["mt-field", "mt-surface"], ["mt-field", "mt-sky"], ["mt-blue", "mt-bg"], ["mt-accent", "mt-surface"]])("%s against %s (non-text)", (f, b) => {
    expect(ratio(tokens[f], tokens[b])).toBeGreaterThanOrEqual(3);
  });
});

function walk(dir: string): string[] {
  return readdirSync(new URL(dir, root), { withFileTypes: true, recursive: true })
    .filter((e) => e.isFile() && /\.(tsx?|css)$/.test(e.name)).map((e) => `${e.parentPath}/${e.name}`);
}

it("FR-UI-07 colours are defined only in tokens.css", () => {
  const bad = ["app", "components", "lib", "styles"].flatMap(walk).filter((f) => !f.endsWith("tokens.css"))
    .filter((f) => /#[0-9a-f]{3,8}\b(?=["'\s;)\]])|rgba?\(|\b(?:text|bg|border)-(?:gray|slate|red|green|blue-\d{2}0\b(?!0)|white|black)/i.test(readFileSync(f, "utf8").replace(/blue-600/g, "")));
  expect(bad).toEqual([]);
});
