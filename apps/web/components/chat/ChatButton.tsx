"use client";

import { OPEN_EVENT } from "./ChatWidget";

export function ChatButton() {
  return <button type="button" onClick={() => window.dispatchEvent(new Event(OPEN_EVENT))} className="min-h-11 rounded-card border border-blue px-5 font-semibold text-blue hover:bg-sky">Chat now</button>;
}
