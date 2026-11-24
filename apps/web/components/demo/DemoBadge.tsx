export function DemoBadge({ label = "Demo data" }: { label?: string }) {
  return (
    <span title="All customers, tickets and contracts shown are synthetic"
      className="inline-block whitespace-nowrap rounded-full border border-warn bg-surface px-2 py-0.5 text-xs font-semibold text-warn">
      {label}
    </span>
  );
}
