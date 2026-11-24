const TONE = { ok: "border-ok text-ok", warn: "border-warn text-warn", bad: "border-bad text-bad", plain: "border-field text-steel" };

export function Badge({ children, tone = "plain" }: { children: React.ReactNode; tone?: keyof typeof TONE }) {
  return <span className={`inline-block whitespace-nowrap rounded-full border bg-surface px-2 text-xs font-semibold ${TONE[tone]}`}>{children}</span>;
}
export const priorityTone = (p: string | null) => (p === "P1" ? "bad" : p === "P2" ? "warn" : "plain");
