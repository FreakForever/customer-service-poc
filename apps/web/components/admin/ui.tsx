import type { ReactNode } from "react";

export const field = "min-h-9 rounded border border-field bg-surface px-2 text-sm";
export const primary = "min-h-9 rounded bg-blue px-4 font-semibold text-surface hover:bg-blue-600 disabled:bg-steel";
export const secondary = "min-h-8 rounded border border-field bg-surface px-3 text-sm font-semibold text-blue hover:bg-sky disabled:text-steel";

export function Table({ heads, label, children }: { heads: string[]; label: string; children: ReactNode }) {
  return (
    <div className="overflow-x-auto" tabIndex={0} role="region" aria-label={label}>
      <table className="w-full border-collapse bg-surface text-left text-sm [&_td]:border [&_td]:border-line [&_td]:p-2 [&_th]:border [&_th]:border-line [&_th]:p-2">
        <thead><tr className="bg-sky">{heads.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead><tbody>{children}</tbody>
      </table>
    </div>
  );
}
export function Status({ error, note, loading }: { error: string; note?: string; loading: boolean }) {
  return <><p role="status" className="my-2 min-h-5 text-sm text-steel">{note || (loading && !error ? "Loading…" : "")}</p>{error && <p role="alert" className="my-2 text-sm text-bad">{error}</p>}</>;
}
