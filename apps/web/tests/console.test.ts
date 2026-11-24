import { expect, it } from "vitest";

import { askedBefore, sourceOf, suggestion, type Detail, type Trace } from "@/lib/console";
import { staffPath } from "@/lib/staffPath";

const m = (id: string, role: string, content: string, delivery = "sent") => ({ id, role, content, delivery, citations: [], actions: [], trace_id: null, created_at: "", rating: null, attachments: [] });
const d = (...messages: object[]) => ({ messages }) as unknown as Detail;

it("FR-HO-02 suggestion prefers the newest draft, then a held-back one", () => {
  const t = { trace: { suggested: "Held [E1]." } } as Trace;
  expect(suggestion(d(m("1", "assistant", "old", "draft"), m("2", "assistant", "new", "draft"), m("3", "assistant", "x")), t)).toEqual({ text: "new", draftId: "2" });
  expect(suggestion(d(m("3", "assistant", "x")), t)).toEqual({ text: "Held.", draftId: null });
  expect(suggestion(d(), null)).toEqual({ text: "", draftId: null });
});
it("FR-CA-05 promotion defaults", () => {
  expect(askedBefore(d(m("a", "customer", "Q1"), m("b", "assistant", "A1"), m("c", "customer", "Q2"), m("e", "assistant", "A2")), "b")).toBe("Q1");
  expect(sourceOf({ ...m("a", "assistant", ""), citations: [{ marker: "E1", url: null }, { marker: "E2", url: "/a/DOC-X#y" }] } as never)).toBe("DOC-X");
});
it("staff proxy paths", () => {
  expect(staffPath(["console", "tickets", "SR-2026-00101"])).toBe("/v1/console/tickets/SR-2026-00101");
  for (const p of [["chat", "x"], ["demo", "reset"], ["console", ".."], ["console", "a/b"], ["console", "a?b"], []]) expect(staffPath(p)).toBeNull();
});
