import { expect, it } from "vitest";

import { back, ms, pct, ticks, TIERS, today, usd } from "@/lib/analytics";

it("formats", () => {
  expect([usd(null), usd(0), usd(0.00012345), usd(0.012), usd(12.3)]).toEqual(["–", "$0", "$0.00012", "$0.012", "$12.30"]);
  expect([ms(null), ms(950.4), ms(1500), ms(12_400)]).toEqual(["–", "950 ms", "1.5 s", "12 s"]);
  expect([pct(null), pct(0), pct(0.3333), pct(1)]).toEqual(["–", "0%", "33.3%", "100%"]);
});
it("days follow India, also across months", () => {
  expect(today(new Date("2026-11-24T18:30:00Z"))).toBe("2026-11-25");
  expect([back("2026-03-01", 1), back("2026-01-01", 6)]).toEqual(["2026-02-28", "2025-12-26"]);
});
it.each([0, 0.00003, 1, 3, 7, 99, 1234])("ticks cover %s", (m) => {
  const t = ticks(m);
  expect(t[0]).toBe(0);
  expect(t[t.length - 1]).toBeGreaterThanOrEqual(m * 0.999999);
  expect(t.length).toBeLessThanOrEqual(5);
});
it("counts get whole steps", () => expect([ticks(1, true), ticks(3, true), ticks(9, true)]).toEqual([[0, 1], [0, 1, 2, 3], [0, 5, 10]]));
it("each tier has its own colour", () => expect(new Set(TIERS.map((t) => t.bg)).size).toBe(TIERS.length));
