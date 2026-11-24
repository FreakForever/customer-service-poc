import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");       // pages can render without their layout being re-run
  return (
    <div className="rounded-card border border-line bg-surface p-6">
      <h1 className="text-2xl">Knowledge admin</h1>
      <p className="mt-2 text-steel">Uploads, documents and graph changes arrive in BS-26.</p>
    </div>
  );
}
