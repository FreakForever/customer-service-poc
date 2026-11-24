import { describe, expect, it } from "vitest";

import { chatPath, display } from "@/lib/chat";
import { readSse } from "@/lib/sse";

const stream = (parts: Uint8Array[]) => new ReadableStream<Uint8Array>({ start(c) { parts.forEach((p) => c.enqueue(p)); c.close(); } });
const all = async (parts: Uint8Array[]) => { const out = []; for await (const e of readSse(stream(parts))) out.push(e); return out; };

describe("FR-CH-01 SSE client", () => {
  const raw = new TextEncoder().encode(': hi\n\nevent: token\ndata: {"t":"120 °C — ठीक"}\n\nevent: done\r\ndata: {"tier":"T1"}\r\n\r\n');
  const want = [{ event: "token", data: { t: "120 °C — ठीक" } }, { event: "done", data: { tier: "T1" } }];
  it("one chunk", async () => expect(await all([raw])).toEqual(want));
  it("any split, also inside a character", async () => {
    for (let i = 1; i < raw.length; i++) expect(await all([raw.slice(0, i), raw.slice(i)])).toEqual(want);
    expect(await all([...raw].map((b) => new Uint8Array([b])))).toEqual(want);
  });
  it("an unfinished event is dropped", async () => expect(await all([raw.slice(0, 30)])).toEqual([]));
});
it("markers", () => {
  expect(display("Use 120 °C [E1] [E12].")).toBe("Use 120 °C [1] [12].");
  expect(["a [", "a [E", "a [E1"].map(display)).toEqual(["a ", "a ", "a "]);
});
it("the proxy reaches chat paths only", () => {
  const u = "0d9f5e4c-1111-4222-8333-0123456789ab";
  const ok: [string, string][] = [["POST", "sessions"], ["POST", `sessions/${u}/messages`], ["POST", `sessions/${u}/forms/service_request`], ["GET", `sessions/${u}`], ["GET", `sessions/${u}/events`], ["POST", "feedback"]];
  for (const [m, p] of ok) expect(chatPath(m, p.split("/"))).toBe(p === "feedback" ? "/v1/feedback" : `/v1/chat/${p}`);
  for (const [m, p] of [["GET", "sessions"], ["POST", `sessions/${u}`], ["POST", `sessions/${u}/../../console`], ["GET", "console/mailbox"], ["GET", `sessions/${u}/events/..`], ["POST", "sessions/x/messages"]])
    expect(chatPath(m, p.split("/"))).toBeNull();
});
