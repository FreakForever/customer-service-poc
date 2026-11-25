import { notFound } from "next/navigation";

import { Workspace } from "@/components/console/Workspace";
import { requireStaff } from "@/lib/auth";

export default async function Page({ params, searchParams }: { params: Promise<{ id: string }>; searchParams: Promise<{ trace?: string | string[] }> }) {
  const staff = await requireStaff("agent");
  const { id } = await params;
  if (!/^[0-9a-f-]{36}$/.test(id)) notFound();
  const { trace } = await searchParams;
  const traceId = typeof trace === "string" && /^[0-9a-f-]{36}$/.test(trace) ? trace : null;
  return <Workspace key={`${id}${traceId ?? ""}`} id={id} me={staff.email} traceId={traceId} />;
}
