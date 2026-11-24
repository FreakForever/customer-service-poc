import { Jobs } from "@/components/admin/Jobs";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Jobs />;
}
