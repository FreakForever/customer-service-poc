import { Upload } from "@/components/admin/Upload";
import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("admin");
  return <Upload />;
}
