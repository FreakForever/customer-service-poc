import { Changes } from "@/components/admin/Changes";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Changes />;
}
