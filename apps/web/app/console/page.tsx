import { requireStaff } from "@/lib/auth";

export default async function Page() {
  await requireStaff("agent");
  return <section className="p-6"><h1 className="text-2xl">Conversations</h1><p className="mt-2 text-steel">Choose a conversation from the list.</p></section>;
}
