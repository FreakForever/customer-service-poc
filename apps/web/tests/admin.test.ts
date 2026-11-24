import { expect, it } from "vitest";

import { layout, type KgNode } from "@/lib/admin";

it("FR-UI-05 graph layout is stable, inside the frame and finite", () => {
  const nodes: KgNode[] = Array.from({ length: 41 }, (_, i) => ({ id: `n${i}`, label: "X", name: "", source: null, dist: i ? 1 : 0 }));
  const edges = nodes.slice(1).map((n) => ({ src: "n0", type: "T", dst: n.id, source: null }));
  const p = layout(nodes, [...edges, { src: "n1", type: "SELF", dst: "n1", source: null }], 900, 560);
  expect(p).toEqual(layout(nodes, [...edges, { src: "n1", type: "SELF", dst: "n1", source: null }], 900, 560));
  expect(p.get("n0")).toEqual({ x: 450, y: 280 });
  for (const { x, y } of p.values()) expect(x >= 50 && x <= 850 && y >= 20 && y <= 540).toBe(true);
  expect(layout([nodes[0]], [], 900, 560).size).toBe(1);
});
