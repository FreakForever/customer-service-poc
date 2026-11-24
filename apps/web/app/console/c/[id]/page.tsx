import { notFound } from "next/navigation";

import { Workspace } from "@/components/console/Workspace";
import { requireStaff } from "@/lib/auth";

export default async function Page({ params }: { params: Promise<{ id: string }> }) {
  const staff = await requireStaff("agent");
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/.test(id)) notFound();
  return <Workspace key={id} id={id} me={staff.email} />;
}
