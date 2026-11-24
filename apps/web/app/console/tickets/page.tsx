import { Tickets } from "@/components/console/Tickets";
import { requireStaff } from "@/lib/auth";

export const metadata = { title: "Tickets" };

export default async function Page() {
  await requireStaff("agent");
  return <Tickets />;
}
