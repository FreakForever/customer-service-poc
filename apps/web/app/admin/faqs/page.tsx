import { Faqs } from "@/components/admin/Faqs";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Faqs />;
}
