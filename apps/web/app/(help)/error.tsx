"use client";

export default function HelpError({ reset }: { reset: () => void }) {
  return <div className="mx-auto max-w-2xl px-4 py-16"><h1 className="text-3xl">We can&apos;t load this page right now</h1><button type="button" onClick={reset} className="mt-4 min-h-11 rounded-card bg-blue px-5 font-semibold text-surface">Try again</button></div>;
}
