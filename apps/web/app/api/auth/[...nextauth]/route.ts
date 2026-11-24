import NextAuth from "next-auth";

import { authOptions } from "@/lib/auth";

// built per request: providers depend on the runtime environment, not on the build
type Ctx = { params: Promise<{ nextauth: string[] }> };
async function handler(req: Request, ctx: Ctx) {
  if (!process.env.NEXTAUTH_SECRET) return Response.json({ title: "Sign-in is not configured", status: 503 }, { status: 503 });
  return NextAuth(req as never, ctx as never, authOptions());
}
export { handler as GET, handler as POST };
