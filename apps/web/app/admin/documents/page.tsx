import { Documents } from "@/components/admin/Documents";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Documents />;
}
