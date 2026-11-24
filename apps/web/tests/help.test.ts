import { existsSync, readFileSync } from "node:fs";
import { expect, it } from "vitest";

import { toInbound, validate } from "@/lib/contact";
import { FAMILIES } from "@/lib/families";
import { layoutSections } from "@/lib/sections";

const ok = { name: "R", company: "C", email: "r@c.example", serial: "", subject: "S", message: "M" };
it("FR-UI-03 contact validation and mapping", () => {
  expect(validate(ok)).toEqual({});
  expect(Object.keys(validate({ ...ok, name: " ", email: "x", serial: "12" })).sort()).toEqual(["email", "name", "serial"]);
  expect(toInbound({ ...ok, serial: "mt-dhd150-23-00417" })).toEqual({ from: "r@c.example", name: "R", subject: "S", body: "M\n\nCompany: C", serial_no: "MT-DHD150-23-00417" });
});
it("FR-UI-02 headings are shown once", () => {
  const s = (heading_path: string[], anchor: string) => ({ heading_path, anchor, content: "" });
  const out = layoutSections([s(["T"], "t"), s(["T", "A"], "a-part-1"), s(["T", "A"], "a-part-2"), s(["T", "B", "C"], "c")]);
  expect(out.map((x) => x.headings.map((h) => `${h.level}${h.text}`))).toEqual([[], ["2A"], [], ["2B", "3C"]]);
});
it("families match the catalogue", () => {
  const f = new URL("../../../mock-data/data/catalog/product_families.json", import.meta.url);
  if (existsSync(f)) expect(FAMILIES.map((x) => [x.id, x.name])).toEqual(JSON.parse(readFileSync(f, "utf8")).map((x: { id: string; name: string }) => [x.id, x.name]));
});
