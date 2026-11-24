import type { NextAuthOptions } from "next-auth";
import { getServerSession } from "next-auth";
import { getToken } from "next-auth/jwt";
import type { Provider } from "next-auth/providers/index";
import CredentialsProvider from "next-auth/providers/credentials";
import GoogleProvider from "next-auth/providers/google";
import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { allows, demoStaffEnabled, roleOf, type Role } from "./roles";

declare module "next-auth/jwt" {
  interface JWT { hd?: string; demoRole?: string }
}

export const googleConfigured = () => Boolean(process.env.GOOGLE_CLIENT_ID && process.env.GOOGLE_CLIENT_SECRET);

export function authOptions(): NextAuthOptions {
  const providers: Provider[] = [];
  if (googleConfigured()) {
    providers.push(GoogleProvider({
      clientId: process.env.GOOGLE_CLIENT_ID!, clientSecret: process.env.GOOGLE_CLIENT_SECRET!,
      authorization: { params: { prompt: "select_account", scope: "openid email profile" } },
    }));
  }
  if (demoStaffEnabled(process.env)) {
    providers.push(CredentialsProvider({
      id: "demo", name: "Demo persona", credentials: { role: {} },
      authorize: async (c) => (c?.role === "agent" || c?.role === "admin"
        ? { id: `demo-${c.role}`, name: c.role === "admin" ? "Demo admin" : "Demo agent", email: `${c.role}@staff.matsui-demo.example` }
        : null),
    }));
  }
  return {
    providers,
    secret: process.env.NEXTAUTH_SECRET,
    session: { strategy: "jwt", maxAge: 8 * 3600 },
    pages: { signIn: "/signin", error: "/signin" },
    callbacks: {
      // anyone with a verified Google address may hold a session; what they can open is decided per request by role
      signIn: async ({ account, profile }) =>
        account?.provider === "demo" ? demoStaffEnabled(process.env)
          : (profile as { email_verified?: boolean } | undefined)?.email_verified === true,
      jwt: async ({ token, account, profile, user }) => {
        if (account?.provider === "google") {
          token.hd = (profile as { hd?: string } | undefined)?.hd;
          delete token.demoRole;
        } else if (account?.provider === "demo") {
          token.demoRole = user.id.replace("demo-", "");
        }
        return token;
      },
    },
  };
}

export type Staff = { email: string; name: string; role: Role };

/** The signed-in person with a staff role, or null. */
export async function getStaff(): Promise<Staff | null> {
  if (!process.env.NEXTAUTH_SECRET) return null;
  const req = { headers: Object.fromEntries(await headers()), cookies: Object.fromEntries((await cookies()).getAll().map((c) => [c.name, c.value])) };
  const token = await getToken({ req: req as never, secret: process.env.NEXTAUTH_SECRET });
  const role = roleOf(token, process.env);
  return token?.email && role ? { email: token.email, name: token.name ?? token.email, role } : null;
}

/** Second check behind the middleware, for every staff layout and route. */
export async function requireStaff(need: Role): Promise<Staff> {
  const s = await getStaff();
  if (!s || !allows(s.role, need)) redirect("/signin?error=AccessDenied");
  return s;
}

export async function isSignedIn(): Promise<boolean> {
  return Boolean(process.env.NEXTAUTH_SECRET && (await getServerSession(authOptions())));
}
