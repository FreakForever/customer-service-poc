export type SseEvent = { event: string; data: unknown };

/** Parse a text/event-stream body; events may be split anywhere across chunks. Comments and unknown fields are ignored. */
export async function* readSse(body: ReadableStream<Uint8Array>): AsyncGenerator<SseEvent> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      const { done, value } = await reader.read();
      buf = (buf + (done ? dec.decode() : dec.decode(value, { stream: true }))).replace(/\r\n/g, "\n");
      let i;
      while ((i = buf.indexOf("\n\n")) >= 0) {
        const block = buf.slice(0, i);
        buf = buf.slice(i + 2);
        let event = "message";
        const data: string[] = [];
        for (const line of block.split("\n")) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        }
        if (data.length) yield { event, data: JSON.parse(data.join("\n")) };
      }
      if (done) return;
    }
  } finally {
    void reader.cancel().catch(() => {});
  }
}
