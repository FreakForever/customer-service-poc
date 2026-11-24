import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("agent");       // pages can render without their layout being re-run
  return (
    <div className="rounded-card border border-line bg-surface p-6">
      <h1 className="text-2xl">Conversations</h1>
      <p className="mt-2 text-steel">Conversation views arrive in BS-25.</p>
    </div>
  );
}
