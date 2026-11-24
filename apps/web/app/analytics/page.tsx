import { Dashboard } from "@/components/analytics/Dashboard";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("agent");       // pages can render without their layout being re-run
  return <Dashboard />;
}
