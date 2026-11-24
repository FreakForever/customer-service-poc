import { Explorer } from "@/components/admin/Explorer";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Explorer />;
}
