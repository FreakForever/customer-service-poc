import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("agent");       // pages can render without their layout being re-run
  return (
    <div className="rounded-card border border-line bg-surface p-6">
      <h1 className="text-2xl">Analytics</h1>
      <p className="mt-2 text-steel">KPIs and benchmark results arrive in a later step.</p>
    </div>
  );
}
